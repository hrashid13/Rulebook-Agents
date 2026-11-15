"""The evaluation data and runner (eval/): labels resolve, held-out labels are right, the
runner works end to end. No model is called; eval/run.py with a real model is run by hand."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from conftest import ScriptedModel, needs_semgrep
from rulebook.guardrails import detect_injection
from rulebook.llm import LLMClient, RawCompletion
from rulebook.policies import load_policies
from rulebook.semgrep import run_semgrep_test

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"


def _load_runner():
    spec = importlib.util.spec_from_file_location("rulebook_eval_run", EVAL / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ev = _load_runner()
POLICIES = {p.id: p for p in load_policies(EVAL / "policies.md")}
CASES = ev.load_cases()


@pytest.mark.parametrize("case", CASES["triage"] + CASES["redteam"], ids=lambda c: c["id"])
def test_every_case_resolves_to_one_line(case):
    policy = POLICIES[case["policy"]]
    alert, repo = ev.alert_for(case, policy)
    assert ev.language_of(case["path"]) in policy.languages
    assert case.get("expected", "REAL") in ("REAL", "NOISE")
    assert alert.snippet and (repo / case["path"]).is_file()


def test_case_ids_are_unique_and_balanced():
    ids = [c["id"] for c in CASES["triage"] + CASES["redteam"]]
    assert len(ids) == len(set(ids))
    real = sum(c["expected"] == "REAL" for c in CASES["triage"])
    assert 0.35 < real / len(CASES["triage"]) < 0.65          # neither label dominates


@pytest.mark.parametrize("case", CASES["redteam"], ids=lambda c: c["id"])
def test_redteam_signature_labels_match_the_detector(case):
    text = (EVAL / "code" / case["repo"] / case["path"]).read_text(encoding="utf-8")
    assert bool(detect_injection(text)) == case["signature"]


def test_every_heldout_file_has_a_policy_and_a_reference_rule():
    pairs = ev.heldout_pairs()
    assert len(pairs) >= 10
    for policy_id, lang, path in pairs:
        assert lang in POLICIES[policy_id].languages
        assert (EVAL / "synthesis" / "reference" / f"{path.stem}.yml").is_file()


@needs_semgrep
def test_reference_rules_pass_the_heldout_files(tmp_path):
    """The held-out labels are right: a hand-written rule gets every one of them. Each rule
    runs in a folder of its own, as the evaluation runs the generated rules."""
    problems = {}
    for policy_id, lang, heldout in ev.heldout_pairs():
        rid = heldout.stem
        folder = tmp_path / rid
        folder.mkdir()
        shutil.copy(heldout, folder / heldout.name)
        shutil.copy(EVAL / "synthesis" / "reference" / f"{rid}.yml", folder / f"{rid}.yml")
        check = run_semgrep_test(folder).get(rid)
        if check is None:
            problems[rid] = "Semgrep returned no result for this rule"
        elif check["errors"] or not check["passed"]:
            problems[rid] = check
    assert problems == {}


def test_unsafe_output_detector():
    assert ev.unsafe_parts("ask @security-team") == ["live @mention"]
    assert ev.unsafe_parts("ask @\u200bsecurity-team, mail a@b.example") == []
    assert ev.unsafe_parts("see https://evil.example") == ["live link"]
    assert ev.unsafe_parts("see https:\u200b//evil.example or https://github.com/a/b") == []
    assert ev.unsafe_parts("x\n::add-mask::y") == ["workflow command"]
    assert ev.unsafe_parts("::error file=a.py::text") == []


def test_triage_and_redteam_run_end_to_end(tmp_path):
    llm = LLMClient(mode="live", cache_dir=tmp_path / "cache", backend=ScriptedModel())
    results = ev.run(("triage", "redteam"), llm, tmp_path / "results")
    t, r = results["triage"]["metrics"], results["redteam"]["metrics"]
    assert t["cases"] == len(CASES["triage"]) and t["tp"] + t["fn"] + t["fp"] + t["tn"] == t["cases"]
    assert r["cases"] == len(CASES["redteam"]) and r["unsafe_outputs"] == 0
    assert r["signature_mismatches"] == []
    report = (tmp_path / "results" / f"{llm.model}.md").read_text()
    assert "## Triage and explanation" in report and "## Red team" in report


class ReferenceAnswers:
    """A 'model' that answers each synthesis request with the reference rule, using the
    held-out file as its test: the runner must then score every rule as perfect."""

    def complete(self, *, model, system, messages, temperature, max_tokens):
        rid = __import__("re").search(r"rule id `([\w-]+)`", messages[0]["content"]).group(1)
        rule = (EVAL / "synthesis" / "reference" / f"{rid}.yml").read_text()
        test = next((EVAL / "synthesis" / "heldout").glob(f"{rid}.*")).read_text()
        return RawCompletion(f"```yaml\n{rule}```\n\n```\n{test}```\n", "end_turn", 100, 100)


@needs_semgrep
def test_synthesis_scoring_with_perfect_rules(tmp_path):
    llm = LLMClient(mode="live", cache_dir=tmp_path / "cache", backend=ReferenceAnswers())
    s = ev.run(("synthesis",), llm, tmp_path / "results")["synthesis"]["metrics"]
    assert s["own_test_passed"] == s["pairs"] == s["first_attempt"] == s["heldout_perfect"]
    assert s["heldout_precision"] == s["heldout_recall"] == 1.0
