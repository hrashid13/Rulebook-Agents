"""Diff parsing, Semgrep plumbing, config, the model client and the report renderer."""
import json

import httpx
import pytest

from conftest import ScriptedModel, git, needs_semgrep
from rulebook.config import Config, ConfigError, load_config
from rulebook.diff import changed_lines, default_base, parse_unified_diff
from rulebook.llm import AnthropicBackend, CallLimitReached, LLMClient, LLMError, OpenAICompatBackend, ReplayMiss
from rulebook.models import Alert, Dismissal, Finding, Report
from rulebook.policies import Policy
from rulebook.render import code_block, code_span, escape, looks_like_code, to_markdown
from rulebook.semgrep import policy_for, scan

# --------------------------------------------------------------------------- diff

DIFF = """diff --git a/app/x.py b/app/x.py
index 1..2 100644
--- a/app/x.py
+++ b/app/x.py
@@ -3,0 +4,2 @@ def f():
+    a = 1
+    b = 2
@@ -10 +12 @@ def g():
-    old()
+    new()
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
--- a/old_name.py
+++ b/new_name.py
@@ -1 +1 @@
-x = 1
+x = 2
diff --git "a/caf\\303\\251.py" "b/caf\\303\\251.py"
--- "a/caf\\303\\251.py"
+++ "b/caf\\303\\251.py"
@@ -0,0 +1 @@
+print(1)
\\ No newline at end of file
"""


def test_parse_unified_diff():
    assert parse_unified_diff(DIFF) == {"app/x.py": {4, 5, 12}, "new_name.py": {1}, "café.py": {1}}


def test_changed_lines_from_git(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("one\ntwo\nthree\n")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("one\nTWO\nthree\nfour\n")
    git(repo, "commit", "-qam", "change")
    (repo / "a.py").write_text("one\nTWO\nthree\nfour\nfive (uncommitted)\n")
    assert changed_lines(repo, "main") == {"a.py": {2, 4, 5}}
    assert default_base(repo) == "main"


# --------------------------------------------------------------------------- semgrep

def test_policy_for():
    ids = ["sql", "sql-strict", "no-eval"]
    assert policy_for("x", {"policy": "no-eval"}, ids) == "no-eval"
    assert policy_for("x", {"policy": "unknown"}, ids) is None          # declared but not in policies.md
    assert policy_for("sql-strict-python", {}, ids) == "sql-strict"     # longest match wins
    assert policy_for("sql-java", {}, ids) == "sql"
    assert policy_for("other", {}, ids) is None


@needs_semgrep
def test_bad_rule_is_reported(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "bad.yml").write_text(
        "rules:\n  - id: broken\n    languages: [python]\n    severity: ERROR\n    message: m\n"
        "    pattern: 'def ( :'\n")
    (tmp_path / "a.py").write_text("x = 1\n")
    alerts, problems = scan(tmp_path, tmp_path / "rules", {"a.py": {1}}, ["broken"])
    assert alerts == [] and problems and problems[0].startswith("Semgrep:")


# --------------------------------------------------------------------------- config

def test_config_defaults_and_paths(tmp_path):
    assert load_config(tmp_path / "missing.yml") == Config()
    f = tmp_path / "config.yml"
    f.write_text("languages: [py, TS]\npaths:\n  include: [src/]\n  exclude: ['*_generated.py']\n"
                 "fail_on: error\nmax_findings: 10\nmodel: claude-haiku-5-5\n")
    c = load_config(f)
    assert c.languages == ("python", "typescript") and c.fail_on == "error" and c.max_findings == 10
    assert c.wants("src/a.py") and not c.wants("lib/a.py") and not c.wants("src/x_generated.py")


@pytest.mark.parametrize("text", ["issues: off", "issues: 'off'", "issues: OFF"])
def test_config_issues_off(tmp_path, text):
    """YAML reads a bare `off` as false; it must still mean off."""
    f = tmp_path / "config.yml"
    f.write_text(text + "\n")
    assert load_config(f).issues == "off"


@pytest.mark.parametrize("text, message", [
    ("fail_on: sometimes", "fail_on"), ("max_findings: 0", "max_findings"),
    ("languages: [cobol]", "unknown language"), ("paths: {only: [a]}", "include and exclude"),
    ("modle: x", "unknown setting"), ("- a list", "mapping"), ("a: [", "not valid YAML"),
])
def test_config_errors(tmp_path, text, message):
    f = tmp_path / "config.yml"
    f.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_config(f)


# --------------------------------------------------------------------------- llm

def test_record_then_replay(tmp_path):
    backend = ScriptedModel({"agent1": ['{"decision": "REAL", "reason": "r"}']})
    rec = LLMClient(mode="record", backend=backend, cache_dir=tmp_path)
    msgs = [{"role": "user", "content": "hello"}]
    first = rec.complete(msgs, system="You triage")
    again = rec.complete(msgs, system="You triage")
    assert not first.cached and again.cached and rec.stats["live_calls"] == 1
    rep = LLMClient(mode="replay", backend=None, cache_dir=tmp_path)
    assert rep.complete(msgs, system="You triage").text == first.text
    with pytest.raises(ReplayMiss):
        rep.complete([{"role": "user", "content": "different"}], system="You triage")


def test_call_limit(tmp_path):
    llm = LLMClient(mode="live", backend=ScriptedModel(), cache_dir=tmp_path, max_calls=1)
    llm.complete([{"role": "user", "content": "Policy \"a\":\nB.\n\n"}], system="You explain")
    with pytest.raises(CallLimitReached):
        llm.complete([{"role": "user", "content": "Policy \"a\":\nB.\n\n"}], system="You explain")


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RULEBOOK_PROVIDER", "openai")
    monkeypatch.delenv("RULEBOOK_MODEL", raising=False)
    with pytest.raises(LLMError, match="no model set"):
        LLMClient.from_env()
    monkeypatch.setenv("RULEBOOK_MODEL", "llama3")
    llm = LLMClient.from_env(cache_dir=tmp_path)
    with pytest.raises(LLMError, match="needs RULEBOOK_BASE_URL"):
        llm.complete([{"role": "user", "content": "x"}])
    monkeypatch.setenv("RULEBOOK_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RULEBOOK_API_KEY", raising=False)
    monkeypatch.delenv("RULEBOOK_MODEL")
    llm = LLMClient.from_env(cache_dir=tmp_path)
    assert llm.model == "claude-haiku-5-5"
    with pytest.raises(LLMError, match="no API key"):
        llm.complete([{"role": "user", "content": "x"}])


def test_openai_compatible_wire_format():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    backend = OpenAICompatBackend("http://x/v1", "k")
    backend._http = httpx.Client(base_url="http://x/v1", headers=backend._http.headers,
                                 transport=httpx.MockTransport(handler))
    out = backend.complete(model="m", system="sys", messages=[{"role": "user", "content": "q"}],
                           temperature=0.0, max_tokens=10)
    assert out.text == "hi" and out.input_tokens == 5
    assert seen["messages"][0] == {"role": "system", "content": "sys"} and seen["auth"] == "Bearer k"


def test_anthropic_backend_with_the_real_sdk():
    """The installed anthropic SDK against a fake server: request shape and parsing.
    (anthropic 2.x moved from httpx to httpx2; use whichever the installed SDK needs.)"""
    try:
        import httpx2 as http
    except ImportError:
        http = httpx
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if "temperature" in body and len(seen) == 1:   # first try: pretend the API rejects it
            return http.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "temperature is not supported"}})
        return http.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": '{"decision": "REAL"}'}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 11, "output_tokens": 4}})
    backend = AnthropicBackend("test-key", http_client=http.Client(transport=http.MockTransport(handler)))
    out = backend.complete(model="claude-haiku-5-5", system="sys", temperature=0.0, max_tokens=50,
                           messages=[{"role": "user", "content": "q"}])
    assert out.text == '{"decision": "REAL"}' and (out.input_tokens, out.output_tokens) == (11, 4)
    assert seen[-1]["system"] == "sys" and seen[-1]["messages"] == [{"role": "user", "content": "q"}]
    assert "temperature" not in seen[-1]          # dropped after the API refused it
    backend.complete(model="m", system="", temperature=0.0, max_tokens=5,
                     messages=[{"role": "user", "content": "again"}])
    assert "temperature" not in seen[-1] and "system" not in seen[-1]


# --------------------------------------------------------------------------- render

def test_escape_rich_keeps_code_spans_only():
    from rulebook.render import escape_rich
    out = escape_rich("Concatenates `user_id` from `request.args['id']` into SQL; see @bob or http://x.y")
    assert "`user_id`" in out and "`request.args['id']`" in out
    assert "@\u200bbob" in out and "http:\u200b//" in out
    assert escape_rich("a `b` c") == "a `b` c"
    assert escape_rich("an *unclosed `tick") == "an \\*unclosed \\`tick"
    assert escape_rich("user_id and _private_ and snake_case_name") == "user_id and \\_private\\_ and snake_case_name"


def test_escape_blocks_mentions_links_and_html():
    out = escape("@octocat see https://evil.example and [click](http://x) <img src=x> www.evil.com **bold**")
    assert "@\u200boctocat" in out and "https:\u200b//" in out and "www\u200b." in out
    assert "\\[click\\]" in out and "\\<img" in out and "\\*\\*bold" in out


def test_code_spans_and_blocks_survive_backticks():
    assert code_span("a`b") == "``a`b``"
    block = code_block("x = ```danger```", "a.py")
    assert block.startswith("````python\n") and block.endswith("\n````")


def test_markdown_report():
    policy = Policy(id="no-eval", title="No eval", text="Never pass strings to eval().", category="security",
                    severity="error")
    alert = Alert("src/a.ts", 3, 3, "no-eval-typescript", "no-eval", "eval", "error", "eval(x)")
    report = Report(base="main", changed_files=2, model="m",
                    findings=[Finding(alert, "r", "Runs `@admin` input from @admin.", "Never pass strings to eval().",
                                      False, fix_code="JSON.parse(x)", fix_note="Parse, don't run.",
                                      quarantined=True)],
                    dismissed=[Dismissal(alert, "test code")], problems=["Rule x is <bad>"],
                    usage={"calls": 3, "cache_hits": 1, "cost_usd": 0.0021}, retries=1)
    md = to_markdown(report, {"no-eval": policy})
    assert "### 1. No eval · security · error" in md and "`src/a.ts` line 3" in md
    assert "Runs `@admin` input from @\u200badmin." in md      # code span kept, bare mention defused
    assert "quoted automatically" in md and "AI reviewer" in md
    assert "Parse, don't run.\n\n```typescript\nJSON.parse(x)\n```" in md and "Rule x is \\<bad\\>" in md
    assert "3 model calls (1 from cache) \u00b7 1 retry \u00b7 $0.0021" in md


def test_fix_shown_as_code_only_when_it_is_code():
    assert looks_like_code("cur.execute(q, (uid,))") and looks_like_code("a\nb")
    assert not looks_like_code("Use a parameterized query instead.")


def test_markdown_clean_run():
    md = to_markdown(Report(base="main", changed_files=1), {})
    assert "No policy problems in this change (1 changed file checked)." in md


def test_has_commit(tmp_path):
    """No `^{commit}` syntax: Git for Windows expands the braces and every commit looks missing."""
    from conftest import git
    from rulebook.diff import has_commit
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a\n")
    git(tmp_path, "add", "a.txt")
    git(tmp_path, "commit", "-q", "-m", "a")
    head = git(tmp_path, "rev-parse", "HEAD").strip()
    tree = git(tmp_path, "rev-parse", "HEAD:").strip()
    assert has_commit(tmp_path, head)
    assert not has_commit(tmp_path, tree)                 # an object, but not a commit
    assert not has_commit(tmp_path, "0" * 40)
