"""The whole review, end to end: a real git repo, real Semgrep, the real rules, a scripted model."""
from dataclasses import replace

import pytest

from conftest import LANGUAGES, ScriptedModel, make_repo, needs_semgrep, scripted_llm
from rulebook.config import Config
from rulebook.llm import LLMClient, LLMError
from rulebook.policies import load_policies
from rulebook.render import MARKER, to_markdown
from rulebook.review import ReviewInputs, run_review, should_fail

# What each fixture's change must produce: (path, line, policy) for real problems and noise.
EXPECTED = {
    "python": ({("app/users.py", 19, "parameterized-sql-only"), ("app/users.py", 20, "no-print-debugging")},
               {("tests/test_users.py", 6, "no-print-debugging")}),
    "typescript": ({("src/orders.ts", 6, "parameterized-sql-only"), ("src/orders.ts", 10, "no-eval")}, set()),
    "java": ({("src/AccountRepo.java", 14, "parameterized-sql-only")},
             {("src/AccountRepo.java", 20, "parameterized-sql-only")}),
    "go": ({("store/items.go", 13, "parameterized-sql-only")}, set()),
    "csharp": ({("src/Worker.cs", 16, "no-thread-abort")}, set()),
}


def inputs(repo, **config):
    return ReviewInputs(repo=repo, base="main", policies=load_policies(repo / ".rulebook" / "policies.md"),
                        rules=repo / ".rulebook" / "rules", config=Config(**config))


@needs_semgrep
@pytest.mark.parametrize("lang", LANGUAGES)
def test_review_finds_new_problems_only(tmp_path, lang):
    repo = make_repo(tmp_path, lang)
    report = run_review(inputs(repo), scripted_llm(tmp_path))
    real, noise = EXPECTED[lang]
    assert {(f.alert.path, f.alert.start_line, f.alert.policy_id) for f in report.findings} == real
    assert {(d.alert.path, d.alert.start_line, d.alert.policy_id) for d in report.dismissed} == noise
    assert report.problems == []
    assert all(f.quote_checked for f in report.findings)   # every quote is from the policy


@needs_semgrep
def test_unchanged_lines_are_not_reported(tmp_path):
    """users.py line 13 breaks the SQL policy too, but this change did not touch it."""
    repo = make_repo(tmp_path, "python")
    report = run_review(inputs(repo), scripted_llm(tmp_path))
    assert all(f.alert.start_line != 13 for f in report.findings + report.dismissed)


@needs_semgrep
def test_uncommitted_edits_are_reviewed(tmp_path):
    repo = make_repo(tmp_path, "go")
    path = repo / "store" / "items.go"
    path.write_text(path.read_text() + '\nfunc Drop(db *sql.DB, t string) { db.Exec("DROP TABLE " + t) }\n')
    report = run_review(inputs(repo), scripted_llm(tmp_path))
    assert {f.alert.start_line for f in report.findings} == {13, 18}


@needs_semgrep
def test_paths_and_max_findings(tmp_path):
    repo = make_repo(tmp_path, "python")
    report = run_review(inputs(repo, exclude=("tests/",)), scripted_llm(tmp_path))
    assert not report.dismissed                      # tests/ is out of scope
    report = run_review(inputs(repo, max_findings=1), scripted_llm(tmp_path))
    assert len(report.findings) + len(report.dismissed) == 1 and report.alerts_skipped == 2
    assert report.findings[0].alert.severity == "error"   # most severe first


@needs_semgrep
def test_no_rules_yet_is_reported_not_an_error(tmp_path):
    repo = make_repo(tmp_path, "python")
    report = run_review(replace(inputs(repo), rules=repo / "missing"), scripted_llm(tmp_path))
    assert report.findings == [] and "No rules yet" in report.problems[0]


@needs_semgrep
def test_rule_without_a_policy_is_reported(tmp_path):
    repo = make_repo(tmp_path, "csharp")
    rule = repo / ".rulebook" / "rules" / "no-thread-abort.yml"
    rule.write_text(rule.read_text().replace("policy: no-thread-abort", "policy: not-a-policy"))
    report = run_review(inputs(repo), scripted_llm(tmp_path))
    assert report.findings == [] and "does not name a policy" in report.problems[0]


@needs_semgrep
def test_model_error_stops_the_review(tmp_path):
    """An outage must never look like "no problems"."""
    repo = make_repo(tmp_path, "csharp")

    class Down:
        def complete(self, **kw):
            raise ConnectionError("network down")
    with pytest.raises(LLMError, match="network down"):
        run_review(inputs(repo), LLMClient(mode="live", backend=Down(), cache_dir=tmp_path))


@needs_semgrep
def test_second_run_is_served_from_the_cache(tmp_path):
    repo = make_repo(tmp_path, "typescript")
    first = scripted_llm(tmp_path, mode="record")
    run_review(inputs(repo), first)
    again = LLMClient(mode="replay", cache_dir=tmp_path / "cache", backend=ScriptedModel())
    report = run_review(inputs(repo), again)
    assert again.stats["live_calls"] == 0 and again.stats["cache_hits"] == first.stats["calls"]
    assert len(report.findings) == 2


@needs_semgrep
def test_report_markdown_and_fail_on(tmp_path):
    repo = make_repo(tmp_path, "python")
    policies = load_policies(repo / ".rulebook" / "policies.md")
    report = run_review(inputs(repo), scripted_llm(tmp_path))
    md = to_markdown(report, {p.id: p for p in policies})
    assert md.startswith(MARKER)
    assert "**2 problems** in this change · 1 alert dismissed as noise" in md
    assert "`app/users.py` line 19" in md and "Dismissed as noise (1)" in md
    assert should_fail(report, "error") and should_fail(report, "any") and not should_fail(report, "none")
