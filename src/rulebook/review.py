"""The review flow: changed lines -> Semgrep -> Agent 1 -> Agent 2 -> report.

Plain code decides the order. The agents only make their own judgement on one alert at a
time; they never see the GitHub token, call tools, or write anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .diff import changed_lines
from .explain import explain
from .guardrails import ALL_ON, Guardrails
from .llm import LLMClient
from .models import Dismissal, Finding, Report
from .policies import Policy
from .semgrep import scan
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
    changed = {p: lines for p, lines in changed.items() if inputs.config.wants(p)}
    report.changed_files = len(changed)

    if not inputs.rules.exists() or not any(inputs.rules.glob("*.y*ml")):
        report.problems.append(f"No rules yet in {inputs.rules.as_posix()}: run the rules flow "
                               "(or add Semgrep rules by hand) before reviews can find anything.")
        return report
    if not changed:
        return report

    alerts, problems = scan(repo, inputs.rules, changed, by_id)
    report.problems += problems
    report.alerts_total = len(alerts)
    limit = inputs.config.max_findings
    report.alerts_skipped = max(0, len(alerts) - limit)

    for alert in alerts[:limit]:
        policy = by_id[alert.policy_id]
        verdict = triage(alert, policy, llm, repo, guards=guards)
        report.retries += verdict.attempts - 1
        if not verdict.real:
            report.dismissed.append(Dismissal(alert, verdict.reason))
            continue
        ex = explain(alert, policy, verdict.reason, llm, guards=guards)
        report.retries += ex.attempts - 1
        report.findings.append(Finding(alert=alert, triage_reason=verdict.reason,
                                       explanation=ex.explanation, quote=ex.quote,
                                       quote_checked=ex.quote_checked, fix_code=ex.fix_code,
                                       fix_note=ex.fix_note, quarantined=verdict.quarantined))
    report.usage = dict(llm.stats)
    return report


def should_fail(report: Report, fail_on: str) -> bool:
    if fail_on == "any":
        return bool(report.findings)
    if fail_on == "error":
        return report.worst_severity() == "error"
    return False
