"""Read `.rulebook/policies.md`: the team's coding rules, written in plain English.

Format: one policy per "## " heading. Right under the heading, optional fields
(category, languages, severity), then the policy text. Text before the first heading
is ignored, so the file can start with an introduction.

    ## No Thread.Abort
    category: bug_risk
    languages: csharp
    severity: error

    Do not call Thread.Abort() in managed code. Use a CancellationToken.

A policy's id is its heading as a slug ("no-thread-abort"): readable, stable, and two
people adding policies never collide on a number. Renaming a heading therefore gives
the policy a new id (and a new rule).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

CATEGORIES = ("security", "bug_risk", "style")
SEVERITIES = ("error", "warning", "info")
DEFAULT_SEVERITY = "warning"

# Semgrep language names, plus the spellings people actually type.
LANGUAGES = {
    "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "java": "java",
    "go": "go", "golang": "go",
    "csharp": "csharp", "c#": "csharp", "cs": "csharp",
    "c": "c",
    "cpp": "cpp", "c++": "cpp",
    "ruby": "ruby", "rb": "ruby",
    "php": "php",
    "kotlin": "kotlin", "kt": "kotlin",
    "swift": "swift",
    "rust": "rust", "rs": "rust",
    "scala": "scala",
    "bash": "bash", "sh": "bash",
    "terraform": "terraform", "hcl": "terraform",
    "dockerfile": "dockerfile",
    "yaml": "yaml",
    "json": "json",
    "solidity": "solidity",
}

_FIELD = re.compile(r"^(category|languages|severity)\s*:\s*(.*)$", re.IGNORECASE)


class PolicyError(ValueError):
    """A problem in policies.md, with the line it is on."""

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"policies.md line {line}: {message}" if line else message)


@dataclass(frozen=True)
class Policy:
    id: str
    title: str
    text: str
    category: str | None = None
    languages: tuple[str, ...] = ()
    severity: str = DEFAULT_SEVERITY
    line: int = field(default=0, compare=False)

    @property
    def fingerprint(self) -> str:
        """Changes when anything that shapes the rule changes, so the rules flow can
        tell new, changed and unchanged policies apart."""
        body = "\n".join([self.title, self.category or "", ",".join(self.languages),
                          self.severity, " ".join(self.text.split())])
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _languages(value: str, line: int) -> tuple[str, ...]:
    out: list[str] = []
    for raw in re.split(r"[,\s]+", value.strip()):
        if not raw:
            continue
        lang = LANGUAGES.get(raw.lower())
        if lang is None:
            raise PolicyError(f"unknown language {raw!r}", line)
        if lang not in out:
            out.append(lang)
    return tuple(out)


def _build(title: str, line: int, body: list[tuple[int, str]]) -> Policy:
    pid = slugify(title)
    if not pid:
        raise PolicyError("a policy heading needs letters or digits", line)
    fields: dict[str, tuple[int, str]] = {}
    i = 0
    while i < len(body) and not body[i][1].strip():
        i += 1
    while i < len(body):
        n, text = body[i]
        m = _FIELD.match(text.strip())
        if not m:
            break
        key = m.group(1).lower()
        if key in fields:
            raise PolicyError(f"{key!r} is set twice", n)
        fields[key] = (n, m.group(2).strip())
        i += 1
    text = "\n".join(t for _, t in body[i:]).strip()
    if not text:
        raise PolicyError(f"policy {title!r} has no text", line)

    category = None
    if "category" in fields:
        n, v = fields["category"]
        category = v.lower().replace("-", "_").replace(" ", "_")
        if category not in CATEGORIES:
            raise PolicyError(f"category must be one of {', '.join(CATEGORIES)}, not {v!r}", n)
    severity = DEFAULT_SEVERITY
    if "severity" in fields:
        n, v = fields["severity"]
        severity = v.lower()
        if severity not in SEVERITIES:
            raise PolicyError(f"severity must be one of {', '.join(SEVERITIES)}, not {v!r}", n)
    languages = _languages(*reversed(fields["languages"])) if "languages" in fields else ()
    return Policy(id=pid, title=title, text=text, category=category,
                  languages=languages, severity=severity, line=line)


def parse_policies(markdown: str) -> list[Policy]:
    policies: list[Policy] = []
    current: tuple[str, int] | None = None
    body: list[tuple[int, str]] = []
    in_fence = False

    def flush() -> None:
        if current is not None:
            policies.append(_build(current[0], current[1], body))

    for n, line in enumerate(markdown.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            flush()
            current, body = (line[3:].strip(), n), []
        elif current is not None:
            body.append((n, line))
    flush()

    seen: dict[str, Policy] = {}
    for p in policies:
        if p.id in seen:
            raise PolicyError(f"{p.title!r} has the same id ({p.id}) as the policy on line "
                              f"{seen[p.id].line}; rename one of them", p.line)
        seen[p.id] = p
    return policies


def load_policies(path: str | Path) -> list[Policy]:
    p = Path(path)
    if not p.exists():
        raise PolicyError(f"{p.as_posix()} not found")
    return parse_policies(p.read_text(encoding="utf-8"))
