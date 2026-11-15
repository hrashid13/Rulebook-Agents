"""The data that moves through a review: alert -> (triage) -> finding or dismissal -> report."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Alert:
    """One Semgrep match on a changed line, tied to the policy its rule came from."""
    path: str
    start_line: int
    end_line: int
    rule_id: str
    policy_id: str
    message: str
    severity: str          # error | warning | info
    snippet: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}"

    def sort_key(self) -> tuple:
        return (SEVERITY_ORDER.get(self.severity, 3), self.path, self.start_line, self.rule_id)


@dataclass
class Finding:
    """A real problem: Agent 1 kept it, Agent 2 explained it."""
    alert: Alert
    triage_reason: str
    explanation: str
    quote: str
    quote_checked: bool    # False: Agent 2's quote failed the check and code picked one
    fix_code: str = ""
    fix_note: str = ""
    quarantined: bool = False


@dataclass
class Dismissal:
    """An alert Agent 1 judged to be noise. Always listed in the report, so a person can disagree."""
    alert: Alert
    reason: str


@dataclass
class Report:
    base: str
    findings: list[Finding] = field(default_factory=list)
    dismissed: list[Dismissal] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)      # setup problems worth showing (bad rule, ...)
    changed_files: int = 0
    alerts_total: int = 0
    alerts_skipped: int = 0                                # over max_findings
    unenforced: list[str] = field(default_factory=list)    # policy ids with no rule yet
    retries: int = 0                                       # model answers that needed a second try
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def worst_severity(self) -> Optional[str]:
        if not self.findings:
            return None
        return min((f.alert.severity for f in self.findings), key=lambda s: SEVERITY_ORDER.get(s, 3))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
