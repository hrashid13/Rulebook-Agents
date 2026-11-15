"""The review as Markdown (the PR comment, and `rulebook review` output) or JSON.

Everything that did not come from us is escaped before it is shown: model text, file paths,
code and policy text. A model, or a crafted file in the PR, must never be able to @mention
people, post a link, add HTML or break the comment's layout. Code goes in fenced blocks,
whose contents GitHub does not render.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from .models import Report

MARKER = "<!-- rulebook-agents:review -->"   # lets the Action find and update its own comment

_SPECIAL = re.compile(r"([\\`*\[\]<>#|~!{}])")
_LONE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")   # user_id stays as it is
_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_LANG = {".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
         ".java": "java", ".go": "go", ".cs": "csharp", ".c": "c", ".h": "c", ".cpp": "cpp",
         ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
         ".rs": "rust", ".scala": "scala", ".sh": "bash", ".tf": "hcl", ".yml": "yaml",
         ".yaml": "yaml", ".json": "json", ".sol": "solidity"}


def escape(text: str) -> str:
    """Plain text, safe to place anywhere in GitHub Markdown."""
    text = " ".join(str(text).split())
    text = text.replace("&", "&amp;")
    text = _SPECIAL.sub(r"\\\1", text)
    text = _LONE_UNDERSCORE.sub(r"\\_", text)
    text = text.replace("@", "@\u200b")                        # no @mentions
    text = re.sub(r"://", ":\u200b//", text)                   # no autolinked URLs
    text = re.sub(r"(?i)\bwww\.", "www\u200b.", text)
    return text


def escape_rich(text: str) -> str:
    """Like escape(), but keeps `inline code` the writer marked. Code spans are safe as they
    are: GitHub turns nothing inside them into a mention, a link or HTML."""
    text = " ".join(str(text).split())
    # escape() drops the spaces at the edges of each piece; put back the ones between pieces
    pieces, rebuilt, pos = [], "", 0
    for m in _CODE_SPAN.finditer(text):
        pieces.append((text[pos:m.start()], False))
        pieces.append((m.group(2).strip(), True))
        pos = m.end()
    pieces.append((text[pos:], False))
    for chunk, is_code in pieces:
        if not chunk:
            continue
        if is_code:
            rebuilt += code_span(chunk)
        else:
            lead = " " if chunk[0].isspace() else ""
            trail = " " if chunk[-1].isspace() and chunk.strip() else ""
            rebuilt += lead + escape(chunk) + trail
    return rebuilt


def code_span(text: str) -> str:
    text = " ".join(str(text).split())
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def code_block(code: str, path: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    fence = "`" * max(3, longest + 1)
    lang = _LANG.get(PurePosixPath(path).suffix.lower(), "")
    return f"{fence}{lang}\n{code.rstrip()}\n{fence}"


def looks_like_code(text: str) -> bool:
    """A fix is shown as code when it has a line break or code punctuation; otherwise it is
    a sentence and is shown (escaped) as text."""
    return "\n" in text.strip() or bool(re.search(r"[(){};=<>\[\]]|::|->", text))


def _plural(n: int, word: str, plural: str = "") -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def to_markdown(report: Report, policies: dict, *, title: str = "Rulebook Agents") -> str:
    out = [MARKER, f"## {title}", ""]
    f, d = report.findings, report.dismissed
    if not f and not d and not report.problems:
        out.append(f"No policy problems in this change ({_plural(report.changed_files, 'changed file')} checked).")
    else:
        summary = f"**{_plural(len(f), 'problem')}** in this change"
        if d:
            summary += f" · {_plural(len(d), 'alert')} dismissed as noise"
        out.append(summary)
    if report.alerts_skipped:
        out += ["", f"Only the first {len(f) + len(d)} alerts were reviewed; "
                    f"{report.alerts_skipped} more were skipped (max_findings)."]

    for i, item in enumerate(f, 1):
        a = item.alert
        policy = policies.get(a.policy_id)
        title_ = escape(policy.title if policy else a.policy_id)
        category = f" · {policy.category.replace('_', ' ')}" if policy and policy.category else ""
        out += ["", f"### {i}. {title_}{category} · {a.severity}",
                f"{code_span(a.path)} line {a.start_line}" + (f"–{a.end_line}" if a.end_line != a.start_line else ""),
                "", f"> {escape(item.quote)}"]
        if not item.quote_checked:
            out.append("> _(quoted automatically: the reviewer's quote did not match the policy text)_")
        out += ["", escape_rich(item.explanation)]
        if item.quarantined:
            out += ["", "_Kept because the code contains text addressed to an AI reviewer._"]
        if item.fix_code or item.fix_note:
            out += ["", "**Suggested fix**"]
            if item.fix_note:
                out += ["", escape_rich(item.fix_note)]
            if item.fix_code:
                out += ["", code_block(item.fix_code, a.path)]

    if d:
        out += ["", f"<details><summary>Dismissed as noise ({len(d)})</summary>", ""]
        for item in d:
            a = item.alert
            policy = policies.get(a.policy_id)
            out.append(f"- {code_span(a.path)} line {a.start_line} · "
                       f"{escape(policy.title if policy else a.policy_id)}: {escape_rich(item.reason)}")
        out += ["", "</details>"]

    if report.problems:
        out += ["", "**Setup problems**", ""] + [f"- {escape(p)}" for p in report.problems]

    usage = report.usage or {}
    footer = (f"Checked {_plural(report.changed_files, 'changed file')} against "
              f"{_plural(len(policies), 'policy', 'policies')}")
    if usage.get("calls"):
        footer += (f" with {escape(report.model)} · {_plural(usage['calls'], 'model call')}"
                   f" ({usage.get('cache_hits', 0)} from cache)")
        if report.retries:
            footer += f" · {_plural(report.retries, 'retry', 'retries')}"
        if usage.get("cost_usd"):
            footer += f" · ${usage['cost_usd']:.4f}"
    out += ["", f"<sub>{footer}</sub>", ""]
    return "\n".join(out)


def to_json(report: Report) -> str:
    return json.dumps(report.as_dict(), indent=2, ensure_ascii=False)
