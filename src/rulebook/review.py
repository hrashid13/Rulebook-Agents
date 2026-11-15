"""The review flow: changed lines -> Semgrep -> Agent 1 -> Agent 2 -> report.

Plain code decides the order. The agents only make their own judgement on one alert at a
time; they never see the GitHub token, call tools, or write anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from pathlib import Path, PurePosixPath

from .config import Config
from .diff import changed_lines
from .explain import explain
from .guardrails import ALL_ON, Guardrails
from .llm import LLMClient
from .models import Dismissal, Finding, Report
from .models import Alert
from .policies import Policy
from .ruleset import CODE_LANGUAGES, read_rule_files
from .semgrep import policy_for, scan
from .triage import triage


@dataclass
class ReviewInputs:
    repo: Path
    base: str
    policies: list[Policy]
    rules: Path
    config: Config = field(default_factory=Config)


def run_review(inputs: ReviewInputs, llm: LLMClient, *, guards: Guardrails = ALL_ON,
               changed: dict[str, set[int]] | None = None) -> Report:
    repo = Path(inputs.repo)
    by_id = {p.id: p for p in inputs.policies}
    report = Report(base=inputs.base, model=llm.model)

    if changed is None:
        changed = changed_lines(repo, inputs.base)
    # .rulebook/ is never reviewed: its rule test files contain violations on purpose.
    changed = {p: lines for p, lines in changed.items()
               if not p.startswith(".rulebook/") and inputs.config.wants(p)}
    report.changed_files = len(changed)

    if not inputs.rules.exists() or not any(inputs.rules.glob("*.y*ml")):
        report.problems.append(f"No rules yet in {inputs.rules.as_posix()}: run the rules flow "
                               "(or add Semgrep rules by hand) before reviews can find anything.")
        return report
    report.unenforced = unenforced_policies(inputs.policies, inputs.rules, changed)
    if not changed:
        return report

    alerts, problems = scan(repo, inputs.rules, changed, by_id)
    report.problems += problems
    report.alerts_total = len(alerts)
    limit = inputs.config.max_findings
    report.alerts_skipped = max(0, len(alerts) - limit)

    reviewed = alerts[:limit]
    for alert in reviewed:
        policy = by_id[alert.policy_id]
        verdict = triage(alert, policy, llm, repo, guards=guards)
        report.retries += verdict.attempts - 1
        if not verdict.real:
            report.dismissed.append(Dismissal(alert, verdict.reason))
            continue
        ex = explain(alert, policy, verdict.reason, llm, guards=guards, repo=repo,
                     other_lines=other_flagged_lines(alert, reviewed))
        report.retries += ex.attempts - 1
        report.findings.append(Finding(alert=alert, triage_reason=verdict.reason,
                                       explanation=ex.explanation, quote=ex.quote,
                                       quote_checked=ex.quote_checked, fix_code=ex.fix_code,
                                       fix_note=ex.fix_note, quarantined=verdict.quarantined))
    report.usage = dict(llm.stats)
    return report


def unenforced_policies(policies: list[Policy], rules: Path, changed: Iterable[str]) -> list[str]:
    """Policies that apply to a language in this change but have no rule for it yet (for
    example the rules pull request is not merged). Without a rule a policy cannot produce
    findings, so the report says so instead of letting "no problems" read as "checked"."""
    languages = {CODE_LANGUAGES[s] for s in (PurePosixPath(p).suffix.lower() for p in changed)
                 if s in CODE_LANGUAGES}
    ids = [p.id for p in policies]
    rule_files, _ = read_rule_files(rules)
    covered = {(policy_for(rf.rule_id, {"policy": rf.policy_id} if rf.policy_id else {}, ids), rf.language)
               for rf in rule_files}
    return [p.id for p in policies
            if any((p.id, lang) not in covered for lang in languages
                   if not p.languages or lang in p.languages)]


def other_flagged_lines(alert: Alert, alerts: list[Alert]) -> list[tuple[int, int]]:
    """The other flagged line ranges in the same file. Agent 2 is told to leave them out of
    its fix, so two findings on neighbouring lines do not each rewrite both."""
    mine = range(alert.start_line, alert.end_line + 1)
    return sorted({(a.start_line, a.end_line) for a in alerts
                   if a is not alert and a.path == alert.path
                   and not (a.start_line in mine or alert.start_line in range(a.start_line, a.end_line + 1))})


def should_fail(report: Report, fail_on: str) -> bool:
    if fail_on == "any":
        return bool(report.findings)
    if fail_on == "error":
        return report.worst_severity() == "error"
    return False
