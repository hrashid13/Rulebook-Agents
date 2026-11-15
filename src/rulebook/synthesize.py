"""The rule synthesizer: one policy + one language -> a Semgrep rule and its test file.

The model writes the rule's patterns and message, and a test file in the target language
marked with Semgrep's own annotations:
    # ruleid: <id>    on the line above code that must match
    # ok: <id>        on the line above code that must not match
Code then takes over: it sets the rule's id, language, severity and policy link itself
(the model cannot get them wrong), and `semgrep --test` must pass. A failure goes back to
the model with the exact reason, up to 3 attempts. Nothing here writes to the repo; the
rules flow decides what is written, and a person merges it.

Started from Zachary Zambrana's rule synthesizer in Docker Locker. Changed: any language,
the team's own policies, and verification with `semgrep --test` annotations instead of a
compliant/non-compliant file pair.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .llm import LLMClient
from .policies import Policy
from .semgrep import SemgrepError, run_semgrep_test

MAX_ATTEMPTS = 3
SYNTH_MAX_TOKENS = 8192
GENERATOR = "rulebook-agents"

# Semgrep language -> (test file extension, comment marker, name the model knows it by)
LANGUAGE_INFO: dict[str, tuple[str, str, str]] = {
    "python": (".py", "#", "Python"), "javascript": (".js", "//", "JavaScript"),
    "typescript": (".ts", "//", "TypeScript"), "java": (".java", "//", "Java"),
    "go": (".go", "//", "Go"), "csharp": (".cs", "//", "C#"), "c": (".c", "//", "C"),
    "cpp": (".cpp", "//", "C++"), "ruby": (".rb", "#", "Ruby"), "php": (".php", "//", "PHP"),
    "kotlin": (".kt", "//", "Kotlin"), "swift": (".swift", "//", "Swift"),
    "rust": (".rs", "//", "Rust"), "scala": (".scala", "//", "Scala"),
    "bash": (".sh", "#", "Bash"), "terraform": (".tf", "#", "Terraform (HCL)"),
    "dockerfile": (".dockerfile", "#", "Dockerfile"), "yaml": (".yaml", "#", "YAML"),
    "solidity": (".sol", "//", "Solidity"),
}
SEMGREP_SEVERITY = {"error": "ERROR", "warning": "WARNING", "info": "INFO"}
DROPPED_KEYS = ("fix", "fix-regex", "paths")   # no autofix; nothing that could skip the test file

SYSTEM_PROMPT = """You write Semgrep rules that enforce a team's written coding policy, and a test file that proves each rule works.

Rules:
- Use structural patterns: pattern, pattern-either, patterns with pattern-not or pattern-inside, and metavariables such as $X, $FUNC, $ARGS. Use pattern-regex only when the policy is about text, not code structure.
- Cast a wide net over the construct the policy forbids. A reviewer looks at every match in context and dismisses the harmless ones, so a missed violation is worse than an extra match.
- Leave out only compliant code that looks different in the code itself, such as a query with placeholders and separate parameters, or a value read from the environment. Do not try to judge intent: do not filter on variable or argument names (a thread need not be called `thread`), and do not use taint mode with guessed input sources. Exceptions that depend on context, such as constants, test code or what a hash is used for, are the reviewer's job.
- Cover the common forms: the call as a statement and inside an assignment or return, on any receiver or expression, and each way the language builds strings (concatenation, formatting, interpolation) where that matters.
- Keep the rule short: a few patterns, not dozens.
- The message is one sentence telling the developer what is wrong.

Test file:
- A complete, valid source file in the target language that parses on its own.
- On the line directly above each line that must match, put a comment with `ruleid: <rule id>`. Include three to five violations in different forms.
- On the line directly above each line that must NOT match, put a comment with `ok: <rule id>`. Include two to four compliant cases that look similar to the violations.
- Keep it under 50 lines: no long setup, no explanations.

Reply with exactly two fenced code blocks and nothing else: first ```yaml with the rule file (a `rules:` list with exactly one rule), then a block with the test file."""


class SynthesisError(ValueError):
    """The model's answer could not be used; the message goes back to the model."""


@dataclass
class SynthResult:
    policy_id: str
    language: str
    rule_id: str
    ok: bool
    rule_yaml: str = ""
    test_code: str = ""
    attempts: int = 0
    error: str = ""                         # why the last attempt failed (when not ok)
    matched_lines: list[int] = field(default_factory=list)

    @property
    def test_extension(self) -> str:
        return LANGUAGE_INFO[self.language][0]


def rule_id_for(policy: Policy, language: str) -> str:
    return f"{policy.id}-{language}"


def _block(dumper: yaml.SafeDumper, value: str):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _block)


def dump_rule(rule: dict[str, Any], policy: Policy) -> str:
    header = (f"# Generated by Rulebook Agents from the policy \"{policy.title}\" in .rulebook/policies.md.\n"
              "# Review it like any code. It is regenerated only when that policy changes; delete\n"
              "# metadata.generated_by to keep it as a hand-maintained rule.\n")
    body = yaml.dump({"rules": [rule]}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    return header + body


_FENCE = re.compile(r"```([\w#+.-]*)[ \t]*\n(.*?)\n```", re.DOTALL)


def parse_answer(text: str) -> tuple[str, str]:
    """(rule yaml, test code) from the two fenced blocks."""
    blocks = _FENCE.findall(text or "")
    if len(blocks) < 2:
        raise SynthesisError("I need exactly two fenced code blocks: the rule (```yaml) and then the test file.")
    yaml_blocks = [b for lang, b in blocks if lang.lower() in ("yaml", "yml")]
    rule = yaml_blocks[0] if yaml_blocks else blocks[0][1]
    test = next(b for lang, b in blocks if b is not rule)
    return rule, test


def build_rule(rule_text: str, policy: Policy, language: str) -> dict[str, Any]:
    """Parse the model's rule and set everything code is responsible for."""
    try:
        data = yaml.safe_load(rule_text)
    except yaml.YAMLError as exc:
        raise SynthesisError(f"The rule is not valid YAML: {exc}") from exc
    rules = data.get("rules") if isinstance(data, dict) else None
    if isinstance(data, dict) and rules is None and ("pattern" in data or "patterns" in data):
        rules = [data]                                      # a bare rule without `rules:`
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        raise SynthesisError("The rule file must be `rules:` with exactly one rule.")
    rule = dict(rules[0])
    if not any(k in rule for k in ("pattern", "patterns", "pattern-either", "pattern-regex")) \
            and rule.get("mode") != "taint":
        raise SynthesisError("The rule has no pattern (pattern, patterns, pattern-either or pattern-regex).")
    for key in DROPPED_KEYS:
        rule.pop(key, None)
    message = " ".join(str(rule.get("message") or policy.title).split())
    metadata = rule.get("metadata") if isinstance(rule.get("metadata"), dict) else {}
    ordered: dict[str, Any] = {
        "id": rule_id_for(policy, language),
        "languages": [language],
        "severity": SEMGREP_SEVERITY[policy.severity],
        "message": message,
        "metadata": {**{k: v for k, v in metadata.items() if k not in ("policy", "policy_fingerprint", "generated_by")},
                     "policy": policy.id, "policy_fingerprint": policy.fingerprint,
                     "category": policy.category or "", "generated_by": GENERATOR},
    }
    for key, value in rule.items():
        if key not in ("id", "languages", "severity", "message", "metadata"):
            ordered[key] = value
    return ordered


def check_annotations(test_code: str, rule_id: str) -> None:
    if not re.search(rf"\bruleid:\s*{re.escape(rule_id)}\b", test_code):
        raise SynthesisError(f"The test file has no `ruleid: {rule_id}` comment above a line that must match.")
    if not re.search(rf"\bok:\s*{re.escape(rule_id)}\b", test_code):
        raise SynthesisError(f"The test file has no `ok: {rule_id}` comment above a line that must not match.")


def test_rule(rule: dict[str, Any], policy: Policy, test_code: str, language: str,
              workdir: Optional[Path] = None) -> tuple[bool, str, list[int], str]:
    """Run `semgrep --test` on the rule and its test file in a scratch folder.
    Returns (passed, problem, reported_lines, rule_yaml)."""
    rule_yaml = dump_rule(rule, policy)
    ext = LANGUAGE_INFO[language][0]
    scratch = Path(tempfile.mkdtemp(prefix="rulebook-synth-", dir=workdir))
    try:
        rid = rule["id"]
        (scratch / f"{rid}.yml").write_text(rule_yaml, encoding="utf-8")
        (scratch / f"{rid}{ext}").write_text(test_code.rstrip() + "\n", encoding="utf-8")
        result = run_semgrep_test(scratch)
        check = result.get(rid)
        if check is None:
            return False, "Semgrep did not run the rule against its test file.", [], rule_yaml
        if check["errors"]:
            return False, "Semgrep could not use the rule: " + " | ".join(check["errors"]), [], rule_yaml
        if check["passed"]:
            return True, "", check["reported"], rule_yaml
        lines = test_code.splitlines()

        def show(numbers: list[int]) -> str:
            return "; ".join(f"line {n}: {lines[n - 1].strip()!r}" for n in numbers if 0 < n <= len(lines)) or "none"
        expected, reported = check["expected"], check["reported"]
        missed = [n for n in expected if n not in reported]
        extra = [n for n in reported if n not in expected]
        problem = "The test failed."
        if missed:
            problem += f" The rule did not match these `ruleid:` lines: {show(missed)}."
        if extra:
            problem += f" The rule matched lines it should not: {show(extra)}."
        return False, problem, reported, rule_yaml
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def format_prompt(policy: Policy, language: str) -> str:
    ext, comment, name = LANGUAGE_INFO[language]
    rid = rule_id_for(policy, language)
    category = f", category {policy.category.replace('_', ' ')}" if policy.category else ""
    return (f"Policy \"{policy.title}\" (severity {policy.severity}{category}):\n{policy.text}\n\n"
            f"Write one Semgrep rule for {name} code with rule id `{rid}`, and a {name} test file "
            f"(it will be saved as `{rid}{ext}`). In the test file, mark lines with `{comment} ruleid: {rid}` "
            f"and `{comment} ok: {rid}` comments.")


def synthesize(policy: Policy, language: str, llm: LLMClient, *, max_attempts: int = MAX_ATTEMPTS,
               workdir: Optional[Path] = None) -> SynthResult:
    if language not in LANGUAGE_INFO:
        return SynthResult(policy.id, language, rule_id_for(policy, language), False,
                           error=f"Rulebook Agents cannot generate {language} rules yet (no test-file format).")
    rid = rule_id_for(policy, language)
    messages = [{"role": "user", "content": format_prompt(policy, language)}]
    result = SynthResult(policy.id, language, rid, False)
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        reply = llm.complete(messages, system=SYSTEM_PROMPT, max_tokens=SYNTH_MAX_TOKENS)  # LLMError propagates
        try:
            if reply.stop_reason in ("max_tokens", "length"):
                raise SynthesisError("Your answer was cut off. Keep the rule and the test file shorter.")
            rule_text, test_code = parse_answer(reply.text)
            rule = build_rule(rule_text, policy, language)
            check_annotations(test_code, rid)
            passed, problem, reported, rule_yaml = test_rule(rule, policy, test_code, language, workdir)
        except SynthesisError as exc:
            problem, passed = str(exc), False
        except SemgrepError as exc:
            problem, passed = f"Semgrep failed: {exc}", False
        else:
            result.rule_yaml, result.test_code, result.matched_lines = rule_yaml, test_code.rstrip() + "\n", reported
        if passed:
            result.ok, result.error = True, ""
            return result
        result.error = problem
        messages += [{"role": "assistant", "content": reply.text or "(empty)"},
                     {"role": "user", "content": f"{problem}\nFix it and reply again with the two fenced blocks only."}]
    return result
