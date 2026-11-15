"""The `rulebook` command.

    rulebook policies [PATH]   check policies.md and list what it defines
    rulebook review            review this branch's changes against the rules
    rulebook action            run inside GitHub Actions (used by action.yml)
    rulebook rules             write and test Semgrep rules from policies.md
    rulebook file-issues       open issues for a pull request's findings

Exit codes for `review`: 0 done, 1 problems at or above --fail-on, 2 setup error
(policies, config, git), 3 model error, 4 Semgrep missing or failing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import FAIL_ON, ConfigError, load_config
from .diff import DiffError, default_base
from .llm import LLMClient, LLMError
from .policies import PolicyError, load_policies
from .render import to_json, to_markdown
from .review import ReviewInputs, run_review, should_fail
from .semgrep import SemgrepError, SemgrepNotInstalled

DEFAULT_POLICIES = ".rulebook/policies.md"


def _err(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)


def _file_issues(args: argparse.Namespace) -> int:
    from .github import DEFAULT_AUTHOR, GitHub, GitHubError
    from .issues import CommandError, file_findings, findings_from, parse_command, reply_markdown
    from .render import MARKER, read_data_block
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        _err("set GITHUB_TOKEN (or GH_TOKEN) to a token that can write issues")
        return 2
    try:
        selection = parse_command("/rulebook file " + " ".join(args.findings))
        gh = GitHub(token, args.repository)
        ours = gh.find_own_comment(args.pr, MARKER, DEFAULT_AUTHOR)
        data = read_data_block(ours.get("body", "")) if ours else None
        if data is None:
            _err(f"pull request #{args.pr} has no Rulebook review comment")
            return 2
        result = file_findings(gh, pr=args.pr, data=data, selection=selection,
                               issue_authors={DEFAULT_AUTHOR, gh.viewer()})
    except CommandError as exc:
        _err(f"{exc}: give finding numbers (1 3) or all")
        return 2
    except GitHubError as exc:
        _err(str(exc))
        return 2
    print(reply_markdown(result, len(findings_from(data))))
    return 0


def _policies(args: argparse.Namespace) -> int:
    try:
        policies = load_policies(args.path)
    except PolicyError as exc:
        _err(str(exc))
        return 1
    print(f"{len(policies)} polic{'y' if len(policies) == 1 else 'ies'} in {args.path}")
    for p in policies:
        langs = ", ".join(p.languages) or "repo languages"
        print(f"  {p.id:<32} {p.category or '-':<9} {p.severity:<8} {langs}")
    return 0


def _review(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    rb = repo / ".rulebook"
    try:
        policies = load_policies(Path(args.policies) if args.policies else rb / "policies.md")
        config = load_config(Path(args.config) if args.config else rb / "config.yml")
        base = args.base or default_base(repo)
    except (PolicyError, ConfigError, DiffError) as exc:
        _err(str(exc))
        return 2
    fail_on = args.fail_on or config.fail_on
    try:
        overrides = {} if os.environ.get("RULEBOOK_CACHE_DIR") else {"cache_dir": repo / ".rulebook-cache" / "llm"}
        llm = LLMClient.from_env(model=config.model, **overrides)
        report = run_review(ReviewInputs(repo=repo, base=base, policies=policies,
                                         rules=Path(args.rules) if args.rules else rb / "rules",
                                         config=config), llm)
    except DiffError as exc:
        _err(str(exc))
        return 2
    except (SemgrepNotInstalled, SemgrepError) as exc:
        _err(str(exc))
        return 4
    except LLMError as exc:
        _err(f"model error, review stopped: {exc}")
        return 3

    text = to_json(report) if args.format == "json" else to_markdown(report, {p.id: p for p in policies})
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"{len(report.findings)} problem(s), {len(report.dismissed)} dismissed. Report: {args.output}")
    else:
        print(text)
    return 1 if should_fail(report, fail_on) else 0


def _rules(args: argparse.Namespace) -> int:
    from .ruleset import apply, detect_languages, make_plan, summary_markdown
    from .synthesize import synthesize

    repo = Path(args.repo).resolve()
    rb = repo / ".rulebook"
    try:
        policies = load_policies(Path(args.policies) if args.policies else rb / "policies.md")
        config = load_config(rb / "config.yml")
    except (PolicyError, ConfigError) as exc:
        _err(str(exc))
        return 2
    rules_dir = rb / "rules"
    plan = make_plan(policies, rules_dir, config.languages or detect_languages(repo),
                     only=set(args.only) if args.only else None)
    for line in ([f"  new      {t.policy.id} ({t.language})" for t in plan.targets if t.reason == "new"]
                 + [f"  changed  {t.policy.id} ({t.language})" for t in plan.targets if t.reason == "changed"]
                 + [f"  remove   {r.rule_id}" for r in plan.remove]):
        print(line)
    print(f"{len(plan.targets)} rule(s) to write, {len(plan.remove)} to remove, "
          f"{len(plan.unchanged)} up to date, {len(plan.hand_written)} hand-written.")
    for problem in plan.problems:
        print(f"  ! {problem}")
    if args.dry_run or not plan.has_work:
        return 0
    try:
        overrides = {} if os.environ.get("RULEBOOK_CACHE_DIR") else {"cache_dir": repo / ".rulebook-cache" / "llm"}
        llm = LLMClient.from_env(model=config.model, **overrides)
        results = []
        for t in plan.targets:
            print(f"Writing {t.policy.id} ({t.language})...", flush=True)
            r = synthesize(t.policy, t.language, llm)
            print(f"  {'passed' if r.ok else 'FAILED'} after {r.attempts} attempt(s)"
                  + ("" if r.ok else f": {r.error}"))
            results.append(r)
    except (SemgrepNotInstalled, SemgrepError) as exc:
        _err(str(exc))
        return 4
    except LLMError as exc:
        _err(f"model error, stopped: {exc}")
        return 3
    touched = apply(plan, results, rules_dir)
    print(f"\n{len(touched)} file(s) changed in {rules_dir.relative_to(repo).as_posix()}/. "
          "Review them (git diff) and commit; they take effect once merged.")
    return 1 if any(not r.ok for r in results) else 0


def _action() -> int:
    from .action import main as action_main
    return action_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rulebook",
        description="Plain-English coding rules, turned into tested Semgrep rules and enforced on every PR.")
    parser.add_argument("--version", action="version", version=f"rulebook-agents {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("policies", help="check policies.md and list what it defines")
    p.add_argument("path", nargs="?", default=DEFAULT_POLICIES)
    p.set_defaults(func=_policies)

    r = sub.add_parser("review", help="review this branch's changes against the rules")
    r.add_argument("--base", help="branch to compare with (default: origin's default branch, then main/master)")
    r.add_argument("--repo", default=".", help="repository folder (default: current folder)")
    r.add_argument("--policies", help="policies file (default: .rulebook/policies.md)")
    r.add_argument("--rules", help="Semgrep rules folder (default: .rulebook/rules)")
    r.add_argument("--config", help="settings file (default: .rulebook/config.yml)")
    r.add_argument("--format", choices=["markdown", "json"], default="markdown")
    r.add_argument("--output", help="write the report to this file instead of printing it")
    r.add_argument("--fail-on", choices=FAIL_ON, help="exit 1 when problems reach this level (default: config, else none)")
    r.set_defaults(func=_review)

    a = sub.add_parser("action", help="run inside GitHub Actions (used by action.yml)")
    a.set_defaults(func=lambda args: _action())

    ru = sub.add_parser("rules", help="write and test Semgrep rules from policies.md")
    ru.add_argument("--repo", default=".", help="repository folder (default: current folder)")
    ru.add_argument("--policies", help="policies file (default: .rulebook/policies.md)")
    ru.add_argument("--only", nargs="+", metavar="POLICY_ID", help="only these policies")
    ru.add_argument("--dry-run", action="store_true", help="show what would change; call no model")
    ru.set_defaults(func=_rules)

    fi = sub.add_parser("file-issues", help="open GitHub issues for findings in a pull request's review",
                        description="The same as commenting `/rulebook file ...` on the pull request, "
                                    "using your own token (GITHUB_TOKEN or GH_TOKEN).")
    fi.add_argument("findings", nargs="+", metavar="N", help="finding numbers from the review, or `all`")
    fi.add_argument("--pr", type=int, required=True, help="pull request number")
    fi.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name (default: $GITHUB_REPOSITORY)")
    fi.set_defaults(func=_file_issues)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
