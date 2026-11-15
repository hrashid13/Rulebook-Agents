"""Defences against prompt injection in the code under review.

Pull-request code is written by whoever opened the PR, so everything taken from it is
untrusted data:
  * untrusted_tagging   it goes inside <untrusted_code> tags, and the prompt says never to
                        follow instructions found there;
  * sanitize_tags       tag-like text in it is defused, so it cannot close the tag;
  * injection_quarantine if known injection phrases appear, Agent 1 may not dismiss the alert.

Each switch can be turned off, only for red-team runs that measure what it is worth.
The signatures and sanitiser started from Zachary Zambrana's Agent 1 in Docker Locker;
the switches from Hesham Rashid's guardrails module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace

INJECTION_SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("ignore_instructions", re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|rules?)", re.I)),
    ("system_override", re.compile(r"system\s+(?:override|directive|prompt)", re.I)),
    ("force_noise", re.compile(r"(?:mark|treat|triage|classify|report)\s+(?:this|it|the\s+alert)?\s*(?:as\s+)?(?:a\s+)?(?:false\s+positive|noise|safe|not\s+a\s+(?:problem|violation))", re.I)),
    ("json_hijack", re.compile(r"\{\s*\"decision\"\s*:", re.I)),
    ("suppress", re.compile(r"do\s+not\s+(?:report|flag|warn|mention)", re.I)),
    ("mode_switch", re.compile(r"you\s+are\s+now\s+(?:in\s+)?(?:developer|jailbreak|debug|admin)\s+mode", re.I)),
    ("tag_breakout", re.compile(r"<\s*/?\s*untrusted(?:_code)?\s*>", re.I)),
    ("role_tag", re.compile(r"<\s*/?\s*(?:system|assistant|user|instructions?)\s*>", re.I)),
    ("addressed_to_ai", re.compile(r"\b(?:ai|llm|assistant|reviewer\s+bot|language\s+model)\s*[:,]\s*(?:please\s+)?(?:ignore|skip|approve|dismiss)", re.I)),
]


@dataclass(frozen=True)
class Guardrails:
    untrusted_tagging: bool = True
    sanitize_tags: bool = True
    injection_quarantine: bool = True

    @classmethod
    def all_off(cls) -> "Guardrails":
        return cls(**{f.name: False for f in fields(cls)})

    def without(self, name: str) -> "Guardrails":
        if name not in {f.name for f in fields(self)}:
            raise ValueError(f"unknown guardrail {name!r}")
        return replace(self, **{name: False})


ALL_ON = Guardrails()


def detect_injection(text: str) -> list[str]:
    return [name for name, sig in INJECTION_SIGNATURES if sig.search(text)]


def sanitize(text: str) -> str:
    text = re.sub(r"<\s*/?\s*untrusted(?:_code)?\s*>", "[defused-tag]", text, flags=re.I)
    return re.sub(r"<\s*/?\s*(?:system|assistant|user|instructions?)\s*>", "[defused-tag]", text, flags=re.I)


def wrap_untrusted(body: str, guards: Guardrails = ALL_ON) -> str:
    if guards.sanitize_tags:
        body = sanitize(body)
    return f"<untrusted_code>\n{body}\n</untrusted_code>" if guards.untrusted_tagging else body
