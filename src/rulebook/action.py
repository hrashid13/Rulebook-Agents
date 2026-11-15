"""The GitHub Action: read the event, run the matching flow, report back.

    pull_request               review the PR
    push / workflow_dispatch   rules flow: policies.md -> tested Semgrep rules, opened as a PR
    issue_comment              `/rulebook file N`: findings from the review -> issues

Security rules this file keeps (see docs/DESIGN.md):
  * Policies, rules and config come from the BASE commit, never from the PR. A PR cannot
    delete the rule it breaks, or plant instructions in a policy's (trusted) text.
  * Nothing from the PR or from a model is printed to the job log. GitHub runs lines like
    "::add-mask::" or "::stop-commands::" found there; our own annotations are encoded.
  * The answer cache lives outside the checkout (RULEBOOK_CACHE_DIR, set by action.yml), so
    a PR cannot commit fake "cached" answers.
  * Only our own comment is edited (github.py), and only after rendering has escaped it.
  * Pull requests from forks get no secrets on GitHub, so they are skipped with a note.
  * `/rulebook file` runs on issue_comment, which anyone can trigger and which has the
    repository's token. So it reads no PR code, calls no model, checks the commenter's
    permission first, and takes findings only from our own review comment.
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
from .github import MAX_COMMENT
from .issues import USAGE, CommandError, file_findings, findings_from, parse_command, reply_markdown
from .render import MARKER, data_block, escape, failure_markdown, read_data_block, to_markdown
from .ruleset import apply, detect_languages, make_plan, summary_markdown
from .synthesize import synthesize
from .review import ReviewInputs, run_review, should_fail
from .semgrep import SemgrepError, SemgrepNotInstalled

MAX_ANNOTATIONS = 50
RULES_BRANCH = "rulebook/rules"
RULES_MARKER = "<!-- rulebook-agents:rules -->"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
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
            return self.rules_flow()
        if event_name == "issue_comment":
            return self.file_command()
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
        body = (to_markdown(report, by_id, commit=head_sha, file_hint=config.issues == "command") + "\n"
                + data_block(report, by_id, head_sha) + "\n")
        self.summary(to_markdown(report, by_id, commit=head_sha))
        self._post(number, body)

        for f in report.findings[:MAX_ANNOTATIONS]:
            policy = by_id.get(f.alert.policy_id)
            self.say(command(_LEVEL.get(f.alert.severity, "warning"), f.explanation,
                             file=f.alert.path, line=f.alert.start_line, end_line=f.alert.end_line,
                             title=f"Rulebook: {policy.title if policy else f.alert.policy_id}"))
        if report.unenforced:
            self.say(command("warning", f"{len(report.unenforced)} policy(ies) have no rules yet and were not "
                                        "checked: merge the rules pull request to enforce them."))
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


    # ------------------------------------------------------------------ /rulebook file

    def file_command(self) -> int:
        """`/rulebook file N` on a pull request -> one issue per finding.

        The checkout is the default branch (issue_comment events always are); only its
        .rulebook/config.yml is read. Nothing from the comment is printed or echoed back:
        the reply is built from numbers."""
        event = self._event()
        issue, comment = event.get("issue") or {}, event.get("comment") or {}
        if event.get("action") != "created" or "pull_request" not in issue:
            self.say(command("notice", "Not a new comment on a pull request. Nothing to do."))
            return 0
        if (comment.get("user") or {}).get("type") == "Bot":
            self.say(command("notice", "Comment written by a bot. Nothing to do."))
            return 0
        number = int(issue.get("number") or 0)
        try:
            selection = parse_command(str(comment.get("body") or ""))
            bad_command = False
        except CommandError:
            selection, bad_command = None, True
        if selection is None and not bad_command:
            self.say(command("notice", "The comment is not a /rulebook command. Nothing to do."))
            return 0
        gh = self._gh()
        if gh is None or not number:
            self.say(command("error", "/rulebook file needs github-token and a pull request number."))
            return 1
        try:
            config = load_config(self.workspace / ".rulebook" / "config.yml")
        except ConfigError as exc:
            self.say(command("error", f"Rulebook Agents: .rulebook/config.yml has a problem: {exc}"))
            return 1
        author = self.env.get("RULEBOOK_COMMENT_AUTHOR") or DEFAULT_AUTHOR
        try:
            if config.issues == "off":
                gh.comment(number, "Filing issues is turned off for this repository (`issues: off` in "
                                   "`.rulebook/config.yml`).")
                self.output(issues_opened=0, status="off")
                return 0
            user = str((comment.get("user") or {}).get("login") or "")
            if gh.permission(user) not in ("admin", "write"):
                gh.comment(number, "Only people with write access to this repository can file issues "
                                   "with `/rulebook file`.")
                self.say(command("notice", "/rulebook file refused: the commenter does not have write access."))
                self.output(issues_opened=0, status="refused")
                return 0
            if bad_command:
                gh.comment(number, USAGE)
                self.output(issues_opened=0, status="usage")
                return 0
            ours = gh.find_own_comment(number, MARKER, author)
            data = read_data_block(ours.get("body", "")) if ours else None
            if data is None:
                gh.comment(number, "There is no Rulebook review on this pull request yet, so there is "
                                   "nothing to file. Push a commit to run the review.")
                self.output(issues_opened=0, status="no-review")
                return 0
            result = file_findings(gh, pr=number, data=data, selection=selection, issue_authors={author},
                                   server=self.env.get("GITHUB_SERVER_URL") or "https://github.com")
            gh.comment(number, reply_markdown(result, len(findings_from(data))))
        except GitHubError as exc:
            self.say(command("error", f"Rulebook Agents: {exc}"))
            self.output(issues_opened=0, status="failed")
            return 1
        self.say(f"Rulebook Agents: {len(result.opened)} issue(s) opened, {len(result.existing)} already filed.")
        self.output(issues_opened=len(result.opened), status="filed")
        return 0

    # ------------------------------------------------------------------ rules flow

    def rules_flow(self) -> int:
        """policies.md on the default branch -> rules on the branch `rulebook/rules` -> one PR.

        Only the default branch's policies are trusted (people merged them). The branch is
        rebuilt from the default branch on every run and force-pushed, unless someone has
        pushed their own commits to it: then it is left alone and the PR says why."""
        event = self._event()
        default = (event.get("repository") or {}).get("default_branch") or ""
        ref = self.env.get("GITHUB_REF_NAME", "")
        if not default or ref != default:
            self.say(command("notice", f"The rules flow only runs on the default branch ({default or 'unknown'}), "
                                       f"not {ref or 'this ref'}. Nothing to do."))
            return 0
        rb = self.workspace / ".rulebook"
        if not (rb / "policies.md").exists():
            self.say(command("notice", "No .rulebook/policies.md on the default branch. Nothing to do."))
            return 0
        gh = self._gh()
        if gh is None:
            self.say(command("error", "The rules flow needs github-token to open a pull request."))
            return 1
        try:
            policies = load_policies(rb / "policies.md")
            config = load_config(rb / "config.yml")
        except (PolicyError, ConfigError) as exc:
            self.say(command("error", f"Rulebook Agents: .rulebook/ has a problem: {exc}"))
            self.summary(f"## Rulebook Agents\n\n.rulebook/ has a problem: {escape(str(exc))}\n")
            return 1

        plan = make_plan(policies, rb / "rules", config.languages or detect_languages(self.workspace))
        existing_pr = gh.open_pull_request(RULES_BRANCH)
        if not plan.has_work:
            msg = "Every policy's rules are up to date."
            if plan.problems:
                msg += " Some policies need attention; see the job summary."
            self.say(command("notice", msg))
            self.summary("## Rulebook Agents\n\n" + summary_markdown(plan, [], policies) + "\n")
            self.output(rules_written=0, rules_failed=0, status="up-to-date")
            return 0

        humans = self._human_commits_on_rules_branch(default)
        if humans:
            note = (f"Rulebook Agents did not update this pull request: the `{RULES_BRANCH}` branch has "
                    f"{humans} commit{'s' if humans != 1 else ''} pushed by people, and it will not overwrite "
                    "them. Merge or close this pull request, then run the Rulebook workflow again to pick up "
                    "the latest policies.")
            if existing_pr:
                gh.comment(existing_pr["number"], note)
            self.say(command("warning", f"The {RULES_BRANCH} branch has commits from people; not overwriting it."))
            self.output(rules_written=0, rules_failed=0, status="blocked")
            return 0

        try:
            llm = self.llm or LLMClient.from_env(model=config.model)
            results = []
            for t in plan.targets:
                r = synthesize(t.policy, t.language, llm, workdir=self.temp)
                self.say(f"Rulebook Agents: {t.policy.id} ({t.language}): "
                         f"{'passed' if r.ok else 'failed'} after {r.attempts} attempt(s).")
                results.append(r)
        except (SemgrepNotInstalled, SemgrepError) as exc:
            self.say(command("error", f"Rulebook Agents: Semgrep failed: {exc}"))
            return 1
        except LLMError as exc:
            self.say(command("error", f"Rulebook Agents: the model call failed, no rules were written ({exc})"))
            return 1

        written, failed = sum(r.ok for r in results), sum(not r.ok for r in results)
        body = summary_markdown(plan, results, policies)
        if not written and not plan.remove:
            self.say(command("warning", "No rule could be generated; see the job summary."))
            self.summary("## Rulebook Agents\n\n" + body + "\n")
            self.output(rules_written=0, rules_failed=failed, status="failed")
            return 1

        _git(self.workspace, "checkout", "-q", "-B", RULES_BRANCH)
        apply(plan, results, rb / "rules")
        _git(self.workspace, "add", "-A", "--", ".rulebook/rules")
        _git(self.workspace, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
             "commit", "-q", "-m", f"Rulebook: {written} rule(s) from policies.md",
             "-m", "Generated by Rulebook Agents and tested with semgrep --test. Merging puts them into effect.")
        _git(self.workspace, "push", "-q", "--force", "origin", f"HEAD:refs/heads/{RULES_BRANCH}")

        title = f"Rulebook: rules for {written} polic{'y' if written == 1 else 'ies'}" + \
                (f" ({failed} need a hand-written rule)" if failed else "")
        pr_body = (f"{RULES_MARKER}\n## Rulebook Agents: rule updates\n\n"
                   "These Semgrep rules were written from `.rulebook/policies.md` and each one passed "
                   "`semgrep --test` against its test file. **Merging this pull request is what puts them "
                   "into effect.** Review each rule like any code; edits you make here are kept.\n\n"
                   f"{body}\n\n<sub>Opened by GitHub Actions, so your other workflows don't run on it "
                   "automatically. Close and reopen it to run them.</sub>\n")[:MAX_COMMENT]
        try:
            if existing_pr:
                pr = gh.update_pull_request(existing_pr["number"], title=title, body=pr_body)
            else:
                pr = gh.create_pull_request(branch=RULES_BRANCH, base=default, title=title, body=pr_body)
        except GitHubError as exc:
            self.say(command("error", f"Rulebook Agents: the rules are on the {RULES_BRANCH} branch, "
                                      f"but the pull request could not be opened: {exc}"))
            self.summary("## Rulebook Agents\n\n" + body + "\n")
            return 1
        self.say(f"Rulebook Agents: pull request #{pr.get('number')} has {written} rule(s).")
        self.summary(f"## Rulebook Agents\n\nPull request #{pr.get('number')}.\n\n{body}\n")
        self.output(rules_written=written, rules_failed=failed, status="pull-request")
        return 0

    def _human_commits_on_rules_branch(self, default: str) -> int:
        """How many commits on the rules branch (beyond the default branch) were not made by the bot."""
        if not _git(self.workspace, "ls-remote", "--heads", "origin", RULES_BRANCH).strip():
            return 0
        _git(self.workspace, "fetch", "-q", "--no-tags", "origin",
             f"+refs/heads/{RULES_BRANCH}:refs/remotes/origin/{RULES_BRANCH}",
             f"+refs/heads/{default}:refs/remotes/origin/{default}")
        authors = _git(self.workspace, "log", "--format=%ae", f"origin/{default}..origin/{RULES_BRANCH}").split()
        return sum(1 for a in authors if a != BOT_EMAIL)


def main(env: Optional[Mapping[str, str]] = None) -> int:
    return Action(env if env is not None else os.environ).run()
