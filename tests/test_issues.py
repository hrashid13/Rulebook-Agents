"""`/rulebook file N`: the command parser, the issue text, and the Action flow against a fake GitHub."""
from __future__ import annotations

import io
import json

import httpx
import pytest

from rulebook.action import Action
from rulebook.cli import main as cli_main
from rulebook.github import GitHub
from rulebook.issues import (KEY_PREFIX, LABEL, MAX_PER_COMMAND, CommandError, FiledFinding,
                             findings_from, issue_body, issue_key, parse_command)
from rulebook.models import Alert, Finding, Report
from rulebook.policies import Policy
from rulebook.render import data_block, to_markdown

REPO = "acme/shop"
BOT = "github-actions[bot]"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
POLICY = Policy(id="no-hardcoded-secrets", title="No hardcoded secrets",
                text="API keys must never appear in source code.", category="security", severity="error")


# --------------------------------------------------------------------------- the command

@pytest.mark.parametrize("body, selection", [
    ("/rulebook file 1", [1]),
    ("/rulebook file 3 1 3", [1, 3]),
    ("/Rulebook FILE #2, 4", [2, 4]),
    ("  /rulebook file all\nthanks!", "all"),
    ("Looks good. /rulebook file 1", None),       # must start the comment
    ("/rulebookish file 1", None),
    ("", None),
])
def test_parse_command(body, selection):
    assert parse_command(body) == selection


@pytest.mark.parametrize("body", ["/rulebook", "/rulebook flie 1", "/rulebook file", "/rulebook file one",
                                  "/rulebook file 0", "/rulebook file all 2"])
def test_parse_command_rejects(body):
    with pytest.raises(CommandError):
        parse_command(body)


# --------------------------------------------------------------------------- the issue text

def finding(n=1, **kw):
    base = dict(n=n, path="app/settings.py", line=3, end_line=3, policy_id=POLICY.id,
                policy_title=POLICY.title, severity="error", quote="API keys must never appear in source code.",
                explanation="`STRIPE_API_KEY` is hardcoded.", fix_code='KEY = os.environ["KEY"]', fix_note="")
    base.update(kw)
    return FiledFinding(**base)


def test_issue_body_links_and_escapes():
    f = finding(path="app/odd (name).py", line=3, end_line=4,
                explanation="Ping @security-team and see https://evil.example <img src=x>",
                fix_note="Ask @alice.")
    body = issue_body(f, repo=REPO, pr=7, commit=COMMIT)
    assert body.startswith(KEY_PREFIX + issue_key(7, f))
    assert f"https://github.com/{REPO}/blob/{COMMIT}/app/odd%20%28name%29.py#L3-L4" in body
    assert "**Found in:** #7" in body
    assert "@\u200bsecurity-team" in body and "@\u200balice" in body   # mentions defused
    assert body.count("https://") == 1                                # only our own blob link
    assert "\\<img" in body and "<img" not in body.replace("\\<img", "")   # HTML shown as text
    assert '```python\nKEY = os.environ["KEY"]\n```' in body


def test_issue_body_without_a_valid_commit_has_no_link():
    body = issue_body(finding(), repo=REPO, pr=7, commit="not-a-sha)](https://evil.example")
    assert "blob/" not in body and "evil" not in body


def test_findings_from_skips_malformed_items():
    data = {"findings": [{"n": 1, "path": "a.py", "line": 2, "policy_id": "p"},
                         {"n": "x", "path": "a.py", "line": 2, "policy_id": "p"},
                         {"n": 2, "path": "", "line": 2, "policy_id": "p"},
                         "junk",
                         {"n": 3, "path": "b.py", "line": 0, "policy_id": "p"}]}
    assert [f.n for f in findings_from(data)] == [1]


# --------------------------------------------------------------------------- the Action

def review_body(findings=2, commit=COMMIT):
    alerts = [Alert(path="app/settings.py", start_line=3 + i, end_line=3 + i, rule_id="r",
                    policy_id=POLICY.id, message="m", severity="error", snippet="x") for i in range(findings)]
    report = Report(base="main", findings=[Finding(alert=a, triage_reason="r", explanation="Hardcoded.",
                                                   quote=POLICY.text, quote_checked=True) for a in alerts])
    policies = {POLICY.id: POLICY}
    return to_markdown(report, policies, commit=commit) + "\n" + data_block(report, policies, commit) + "\n"


class FakeGitHub:
    """Comments, collaborator permissions, labels and issues."""

    def __init__(self, *, permissions=None, review=None, review_author=BOT, issues=None):
        self.permissions = {"maintainer": "write", "owner": "admin", "reader": "read", **(permissions or {})}
        self.comments = []
        self.issues = list(issues or [])
        self.labels = set()
        self.next_id = 100
        if review is not None:
            self.comments.append({"id": 1, "body": review, "user": {"login": review_author, "type": "Bot"}})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if "/collaborators/" in path:
            user = path.split("/collaborators/")[1].split("/")[0]
            if user not in self.permissions:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"permission": self.permissions[user]})
        if method == "GET" and path.endswith("/comments"):
            return httpx.Response(200, json=self.comments)
        if method == "POST" and path.endswith("/comments"):
            self.next_id += 1
            c = {"id": self.next_id, "body": json.loads(request.content)["body"], "user": {"login": BOT}}
            self.comments.append(c)
            return httpx.Response(201, json=c)
        if "/labels/" in path and method == "GET":
            name = path.rsplit("/", 1)[1]
            return httpx.Response(200 if name in self.labels else 404, json={"name": name})
        if path.endswith("/labels") and method == "POST":
            self.labels.add(json.loads(request.content)["name"])
            return httpx.Response(201, json={})
        if path.endswith("/issues") and method == "GET":
            assert request.url.params["labels"] == LABEL and request.url.params["state"] == "all"
            return httpx.Response(200, json=[i for i in self.issues if LABEL in i.get("labels", [])])
        if path.endswith("/issues") and method == "POST":
            self.next_id += 1
            data = json.loads(request.content)
            issue = {"number": self.next_id, "title": data["title"], "body": data["body"],
                     "labels": data["labels"], "user": {"login": BOT}}
            self.issues.append(issue)
            return httpx.Response(201, json=issue)
        if path == "/user":
            return httpx.Response(200, json={"login": "maintainer"})
        return httpx.Response(404, json={"message": "Not Found"})

    def client(self) -> GitHub:
        return GitHub("t", REPO, http=httpx.Client(base_url="https://api.github.com",
                                                    transport=httpx.MockTransport(self.handler)))

    def replies(self):
        return [c["body"] for c in self.comments if c["id"] != 1]


def run_comment(tmp_path, fake, body, *, user="maintainer", user_type="User", on_pr=True, config=None,
                action="created"):
    workspace = tmp_path / "work"
    (workspace / ".rulebook").mkdir(parents=True, exist_ok=True)
    if config is not None:
        (workspace / ".rulebook" / "config.yml").write_text(config)
    issue = {"number": 7, **({"pull_request": {"url": "x"}} if on_pr else {})}
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": action, "issue": issue,
                                 "comment": {"body": body, "user": {"login": user, "type": user_type}}}))
    outputs = tmp_path / "out.txt"
    outputs.write_text("")
    env = {"GITHUB_EVENT_NAME": "issue_comment", "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": REPO,
           "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(outputs)}
    log = io.StringIO()
    code = Action(env, github=fake.client(), out=log).run()
    out = dict(l.split("=", 1) for l in outputs.read_text().split())
    return code, log.getvalue(), out


def test_file_one_then_again_then_all(tmp_path):
    fake = FakeGitHub(review=review_body(findings=2))
    code, _, out = run_comment(tmp_path, fake, "/rulebook file 1")
    assert code == 0 and out == {"issues_opened": "1", "status": "filed"}
    (issue,) = fake.issues
    assert issue["labels"] == [LABEL] and LABEL in fake.labels
    assert issue["title"] == "No hardcoded secrets: app/settings.py line 3"
    assert "**Found in:** #7" in issue["body"] and f"blob/{COMMIT}/app/settings.py#L3" in issue["body"]
    assert fake.replies()[-1] == f"Opened 1 issue (finding → issue): 1 → #{issue['number']}."

    run_comment(tmp_path, fake, "/rulebook file 1")                       # no duplicate
    assert len(fake.issues) == 1 and fake.replies()[-1] == f"Already filed: 1 → #{issue['number']}."

    _, _, out = run_comment(tmp_path, fake, "/rulebook file all")
    assert len(fake.issues) == 2 and out["issues_opened"] == "1"
    assert "Opened 1 issue" in fake.replies()[-1] and "Already filed: 1 →" in fake.replies()[-1]


def test_people_without_write_access_are_refused(tmp_path):
    for user in ("reader", "stranger"):                     # read access; not a collaborator at all
        fake = FakeGitHub(review=review_body())
        code, log, out = run_comment(tmp_path, fake, "/rulebook file all", user=user)
        assert code == 0 and out["status"] == "refused" and not fake.issues
        assert "Only people with write access" in fake.replies()[0]
        assert user not in log


def test_admins_can_file(tmp_path):
    fake = FakeGitHub(review=review_body())
    run_comment(tmp_path, fake, "/rulebook file 2", user="owner")
    assert len(fake.issues) == 1


def test_findings_only_come_from_our_own_comment(tmp_path):
    """Anyone can paste our marker and a data block into a comment of their own."""
    fake = FakeGitHub(review=review_body(), review_author="mallory")
    _, _, out = run_comment(tmp_path, fake, "/rulebook file 1")
    assert out["status"] == "no-review" and not fake.issues
    assert "no Rulebook review on this pull request" in fake.replies()[0]


def test_only_our_own_issues_count_as_already_filed(tmp_path):
    """A planted issue carrying the key must not stop the finding being filed."""
    key = issue_key(7, findings_from(json.loads(
        __import__("base64").b64decode(review_body().split("rulebook-agents:data ")[1].split(" -->")[0])))[0])
    planted = {"number": 5, "body": f"{KEY_PREFIX}{key} -->", "labels": [LABEL], "user": {"login": "mallory"}}
    fake = FakeGitHub(review=review_body(), issues=[planted])
    run_comment(tmp_path, fake, "/rulebook file 1")
    assert len(fake.issues) == 2 and "Opened 1 issue" in fake.replies()[0]


def test_numbers_not_in_the_review(tmp_path):
    fake = FakeGitHub(review=review_body(findings=2))
    run_comment(tmp_path, fake, "/rulebook file 2 9")
    assert len(fake.issues) == 1
    assert "No finding 9 in the latest review (it has 2)." in fake.replies()[0]


def test_bad_command_gets_usage_but_only_for_maintainers(tmp_path):
    fake = FakeGitHub(review=review_body())
    _, _, out = run_comment(tmp_path, fake, "/rulebook flie 1")
    assert out["status"] == "usage" and fake.replies()[0].startswith("Usage:")
    fake = FakeGitHub(review=review_body())
    _, _, out = run_comment(tmp_path, fake, "/rulebook flie 1", user="reader")
    assert out["status"] == "refused"


@pytest.mark.parametrize("kw", [dict(body="Nice work!"), dict(body="/rulebook file 1", on_pr=False),
                                dict(body="/rulebook file 1", user_type="Bot"),
                                dict(body="/rulebook file 1", action="edited")])
def test_ignored_comments(tmp_path, kw):
    fake = FakeGitHub(review=review_body())
    body = kw.pop("body")
    code, _, _ = run_comment(tmp_path, fake, body, **kw)
    assert code == 0 and not fake.issues and not fake.replies()


def test_issues_off_in_config(tmp_path):
    fake = FakeGitHub(review=review_body())
    _, _, out = run_comment(tmp_path, fake, "/rulebook file 1", config="issues: off\n")
    assert out["status"] == "off" and not fake.issues and "turned off" in fake.replies()[0]


def test_comment_text_never_reaches_the_log_or_the_reply(tmp_path):
    fake = FakeGitHub(review=review_body())
    evil = "/rulebook file 1 @everyone ::add-mask::x"
    run_comment(tmp_path, fake, evil)                              # not a valid command: usage reply
    _, log, _ = run_comment(tmp_path, fake, "/rulebook file 1\n::stop-commands::abc @everyone")
    assert "::stop-commands" not in log and "::add-mask" not in log
    assert all("@everyone" not in r and "add-mask" not in r for r in fake.replies())


def test_per_command_cap(tmp_path):
    fake = FakeGitHub(review=review_body(findings=MAX_PER_COMMAND + 2))
    run_comment(tmp_path, fake, "/rulebook file all")
    assert len(fake.issues) == MAX_PER_COMMAND and "2 more not filed" in fake.replies()[0]


def test_review_comment_mentions_the_command_only_when_on():
    alerts = [Alert(path="a.py", start_line=1, end_line=1, rule_id="r", policy_id=POLICY.id,
                    message="m", severity="error", snippet="x")]
    report = Report(base="main", findings=[Finding(alert=alerts[0], triage_reason="r", explanation="e",
                                                   quote=POLICY.text, quote_checked=True)])
    assert "/rulebook file 1" in to_markdown(report, {POLICY.id: POLICY}, file_hint=True)
    assert "/rulebook file" not in to_markdown(report, {POLICY.id: POLICY})
    assert "/rulebook file" not in to_markdown(Report(base="main"), {POLICY.id: POLICY}, file_hint=True)


# --------------------------------------------------------------------------- the CLI

def test_cli_file_issues(tmp_path, monkeypatch, capsys):
    fake = FakeGitHub(review=review_body())
    real_init = GitHub.__init__
    http = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(fake.handler))

    def init(self, token, repository, **kw):
        real_init(self, token, repository, http=http)
    monkeypatch.setattr(GitHub, "__init__", init)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli_main(["file-issues", "1", "--pr", "7", "--repository", REPO]) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    assert cli_main(["file-issues", "1", "--pr", "7", "--repository", REPO]) == 0
    assert "Opened 1 issue" in capsys.readouterr().out and len(fake.issues) == 1
    assert cli_main(["file-issues", "nope", "--pr", "7", "--repository", REPO]) == 2
