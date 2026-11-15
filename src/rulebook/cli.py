"""The `rulebook` command.

    rulebook policies [PATH]   check policies.md and list what it defines
    rulebook review            review this branch's changes against the rules
    rulebook action            run inside GitHub Actions (used by action.yml)
    rulebook rules             draft and test rules from policies.md (milestone M3)
    rulebook file-issues       open issues for findings (milestone M4)

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


def _not_built(milestone: str):
    def run(args: argparse.Namespace) -> int:
        print(f"`rulebook {args.command}` is not built yet (milestone {milestone}).", file=sys.stderr)
        return 2
    return run


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

    for name, milestone, text in (("rules", "M3", "draft and test Semgrep rules from policies.md"),
                                  ("file-issues", "M4", "open GitHub issues for findings")):
        s = sub.add_parser(name, help=f"{text} (not built yet)")
        s.set_defaults(func=_not_built(milestone))
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
