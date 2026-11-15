"""The GitHub Action: read the event, run the matching flow, report back.

    pull_request   review the PR (M2)
    push / workflow_dispatch   rules flow (M3, not built yet)
    issue_comment  `/rulebook file N` (M4, not built yet)

Security rules this file keeps (see docs/DESIGN.md):
  * Policies, rules and config come from the BASE commit, never from the PR. A PR cannot
    delete the rule it breaks, or plant instructions in a policy's (trusted) text.
  * Nothing from the PR or from a model is printed to the job log. GitHub runs lines like
    "::add-mask::" or "::stop-commands::" found there; our own annotations are encoded.
  * The answer cache lives outside the checkout (RULEBOOK_CACHE_DIR, set by action.yml), so
    a PR cannot commit fake "cached" answers.
  * Only our own comment is edited (github.py), and only after rendering has escaped it.
  * Pull requests from forks get no secrets on GitHub, so they are skipped with a note.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, Optional

from .config import FAIL_ON, ConfigError, load_config
from .diff import DiffError, _git, changed_lines, ensure_commit, export_folder
from .github import DEFAULT_AUTHOR, GitHub, GitHubError
from .llm import LLMClient, LLMError
from .policies import PolicyError, load_policies
from .render import MARKER, data_block, failure_markdown, to_markdown
from .review import ReviewInputs, run_review, should_fail
from .semgrep import SemgrepError, SemgrepNotInstalled

MAX_ANNOTATIONS = 50
_LEVEL = {"error": "error", "warning": "warning", "info": "notice"}


# --------------------------------------------------------------------------- workflow commands

def _data(text: str) -> str:
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(text: str) -> str:
    return _data(text).replace(":", "%3A").replace(",", "%2C")


def command(level: str, message: str, *, file: str = "", line: int = 0, end_line: int = 0,
            title: str = "") -> str:
    """One workflow command line. Every value is encoded, so text from a model or the PR can
    never end the line early or start a command of its own."""
    props = []
    if file:
        props.append(f"file={_prop(file)}")
    if line:
        props.append(f"line={int(line)}")
    if end_line:
        props.append(f"endLine={int(end_line)}")
    if title:
        props.append(f"title={_prop(title)}")
    return f"::{level}{(' ' + ','.join(props)) if props else ''}::{_data(message)}"


# --------------------------------------------------------------------------- the Action

class Action:
    def __init__(self, env: Mapping[str, str], *, github: Optional[GitHub] = None,
                 llm: Optional[LLMClient] = None, out=None):
        self.env = env
        self.out = out or sys.stdout
        self.github = github
        self.llm = llm
        self.workspace = Path(env.get("GITHUB_WORKSPACE") or ".").resolve()
        self.temp = Path(env.get("RUNNER_TEMP") or self.workspace / ".rulebook-tmp")

    def say(self, line: str) -> None:
        """Only our own text goes to the log (see the module docstring)."""
        print(line, file=self.out, flush=True)

    def _append(self, var: str, text: str) -> None:
        path = self.env.get(var)
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")

    def summary(self, markdown: str) -> None:
        self._append("GITHUB_STEP_SUMMARY", markdown)

    def output(self, **values: object) -> None:
        self._append("GITHUB_OUTPUT", "".join(f"{k}={v}\n" for k, v in values.items()))

    def _gh(self) -> Optional[GitHub]:
        if self.github is None and self.env.get("RULEBOOK_GITHUB_TOKEN"):
            self.github = GitHub(self.env["RULEBOOK_GITHUB_TOKEN"], self.env.get("GITHUB_REPOSITORY", ""),
                                 api_url=self.env.get("GITHUB_API_URL") or "https://api.github.com")
        return self.github

    def _post(self, number: int, body: str) -> None:
        if self.env.get("RULEBOOK_COMMENT", "true").lower() == "false":
            return
        gh = self._gh()
        if gh is None:
            self.say(command("warning", "No github-token, so the report is only in the job summary."))
            return
        try:
            gh.upsert_comment(number, body, MARKER, self.env.get("RULEBOOK_COMMENT_AUTHOR") or DEFAULT_AUTHOR)
            self.say(f"Rulebook Agents: report posted on pull request #{number}.")
        except GitHubError as exc:
            self.say(command("warning", f"Could not post the PR comment: {exc}. "
                                        "The report is in the job summary."))

    def run(self) -> int:
        event_name = self.env.get("GITHUB_EVENT_NAME", "")
        if event_name == "pull_request":
            return self.review_pull_request()
        if event_name == "pull_request_target":
            self.say(command("error", "Rulebook Agents does not run on pull_request_target: that event "
                                      "gives untrusted PR code your secrets. Use pull_request."))
            return 1
        if event_name in ("push", "workflow_dispatch"):
            self.say(command("notice", "The rules flow (policies.md to Semgrep rules) is not built yet "
                                       "(milestone M3). Nothing to do."))
            return 0
        if event_name == "issue_comment":
            self.say(command("notice", "`/rulebook file` is not built yet (milestone M4). Nothing to do."))
            return 0
        self.say(command("notice", f"Rulebook Agents has nothing to do for {event_name or 'this'} events."))
        return 0

    def _event(self) -> dict:
        path = self.env.get("GITHUB_EVENT_PATH")
        if not path or not Path(path).exists():
            raise DiffError("GITHUB_EVENT_PATH is not set; this command runs inside GitHub Actions")
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _fail(self, number: int, head: str, message: str) -> int:
        self.say(command("error", f"Rulebook Agents: {message}", title="Rulebook Agents"))
        body = failure_markdown(message, commit=head)
        self.summary(body)
        self._post(number, body)
        self.output(problems=0, dismissed=0, status="failed")
        return 1

    def review_pull_request(self) -> int:
        event = self._event()
        pr = event.get("pull_request") or {}
        number = int(pr.get("number") or event.get("number") or 0)
        base_sha = (pr.get("base") or {}).get("sha", "")
        head_sha = (pr.get("head") or {}).get("sha", "")
        head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
        if not number or not base_sha:
            self.say(command("error", "The pull_request event has no number or base commit."))
            return 1
        if head_repo != self.env.get("GITHUB_REPOSITORY"):
            msg = ("Skipped: this pull request comes from a fork, and GitHub does not give fork PRs "
                   "the secrets (API key) or the comment permission a review needs.")
            self.say(command("notice", msg))
            self.summary(f"## Rulebook Agents\n\n{msg}\n")
            self.output(problems=0, dismissed=0, status="skipped")
            return 0

        try:
            ensure_commit(self.workspace, base_sha)
            base_dir = self.temp / "rulebook-base"
            shutil.rmtree(base_dir, ignore_errors=True)
            if not export_folder(self.workspace, base_sha, ".rulebook", base_dir):
                msg = ("Skipped: the base branch has no .rulebook/ folder yet. Rules are read from the "
                       "base branch, so merge .rulebook/ (policies.md and rules/) first.")
                self.say(command("notice", msg))
                self.summary(f"## Rulebook Agents\n\n{msg}\n")
                self.output(problems=0, dismissed=0, status="skipped")
                return 0
            rb = base_dir / ".rulebook"
            policies = load_policies(rb / "policies.md")
            config = load_config(rb / "config.yml")
            # actions/checkout gives a pull request GitHub's merge of the PR into the base
            # commit; diff against the base itself then. Otherwise use the merge base.
            # (Read the parents from the commit itself: in a shallow clone, which is the
            # checkout default, `git rev-list --parents` hides them.)
            head_commit = _git(self.workspace, "cat-file", "-p", "HEAD")
            parents = [l.split()[1] for l in head_commit.splitlines() if l.startswith("parent ")]
            is_merge = len(parents) == 2 and parents[0] == base_sha
            try:
                changed = changed_lines(self.workspace, base_sha, merge_base=not is_merge)
            except DiffError as exc:
                raise DiffError(f"{exc}. Check out with `fetch-depth: 0` so the branch history is available.") from exc
            llm = self.llm or LLMClient.from_env(model=config.model)
            report = run_review(ReviewInputs(repo=self.workspace, base=base_sha, policies=policies,
                                             rules=rb / "rules", config=config), llm, changed=changed)
        except (PolicyError, ConfigError) as exc:
            return self._fail(number, head_sha, f"the base branch's .rulebook/ has a problem: {exc}")
        except (SemgrepNotInstalled, SemgrepError) as exc:
            return self._fail(number, head_sha, f"Semgrep failed: {exc}")
        except LLMError as exc:
            return self._fail(number, head_sha, f"the model call failed, so nothing was reviewed ({exc})")
        except DiffError as exc:
            return self._fail(number, head_sha, str(exc))

        by_id = {p.id: p for p in policies}
        body = to_markdown(report, by_id, commit=head_sha) + "\n" + data_block(report, by_id, head_sha) + "\n"
        self.summary(to_markdown(report, by_id, commit=head_sha))
        self._post(number, body)

        for f in report.findings[:MAX_ANNOTATIONS]:
            policy = by_id.get(f.alert.policy_id)
            self.say(command(_LEVEL.get(f.alert.severity, "warning"), f.explanation,
                             file=f.alert.path, line=f.alert.start_line, end_line=f.alert.end_line,
                             title=f"Rulebook: {policy.title if policy else f.alert.policy_id}"))
        fail_on = (self.env.get("RULEBOOK_FAIL_ON") or config.fail_on).lower()
        if fail_on not in FAIL_ON:
            self.say(command("warning", f"fail-on must be one of {', '.join(FAIL_ON)}; using none."))
            fail_on = "none"
        failed = should_fail(report, fail_on)
        self.say(f"Rulebook Agents: {len(report.findings)} problem(s), {len(report.dismissed)} dismissed, "
                 f"{report.changed_files} changed file(s); fail_on={fail_on}.")
        self.output(problems=len(report.findings), dismissed=len(report.dismissed),
                    status="problems" if report.findings else "clean")
        return 1 if failed else 0


def main(env: Optional[Mapping[str, str]] = None) -> int:
    return Action(env if env is not None else os.environ).run()
