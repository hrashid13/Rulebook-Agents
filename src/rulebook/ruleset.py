"""Which rules need writing? Compare policies.md with .rulebook/rules/.

Each policy gets one rule per language: `.rulebook/rules/<policy id>-<language>.yml`, plus
its test file `<policy id>-<language>.<ext>`. A generated rule records the fingerprint of
the policy it came from (metadata.policy_fingerprint), so:

  new        no rule yet for this policy and language      -> generate
  changed    the policy's text or fields changed            -> regenerate
  unchanged  fingerprint matches                            -> leave it (keeps human edits)
  removed    the policy, or that language, is gone          -> delete the generated rule
  hand-written  a rule without metadata.generated_by covers it -> never touched
"""
from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

import yaml

from .policies import Policy
from .render import escape
from .synthesize import GENERATOR, LANGUAGE_INFO, SynthResult, rule_id_for

CODE_LANGUAGES = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
                  ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".go": "go", ".cs": "csharp",
                  ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".rb": "ruby", ".php": "php", ".kt": "kotlin",
                  ".swift": "swift", ".rs": "rust", ".scala": "scala"}
MAX_DETECTED = 4


@dataclass(frozen=True)
class RuleFile:
    path: Path
    rule_id: str
    policy_id: str
    language: str
    fingerprint: str
    generated: bool


@dataclass
class Target:
    policy: Policy
    language: str
    reason: str                       # "new" | "changed"
    existing: Optional[RuleFile] = None


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    unchanged: list[RuleFile] = field(default_factory=list)
    remove: list[RuleFile] = field(default_factory=list)
    hand_written: list[RuleFile] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return bool(self.targets or self.remove)


def read_rule_files(rules_dir: Path) -> tuple[list[RuleFile], list[str]]:
    found: list[RuleFile] = []
    problems: list[str] = []
    if not rules_dir.exists():
        return found, problems
    for path in sorted(rules_dir.rglob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            problems.append(f"{path.name} could not be read: {exc}")
            continue
        for rule in (data.get("rules") or []) if isinstance(data, dict) else []:
            if not isinstance(rule, dict):
                continue
            meta = rule.get("metadata") if isinstance(rule.get("metadata"), dict) else {}
            langs = rule.get("languages") or [""]
            found.append(RuleFile(path=path, rule_id=str(rule.get("id", "")), policy_id=str(meta.get("policy", "")),
                                  language=str(langs[0]), fingerprint=str(meta.get("policy_fingerprint", "")),
                                  generated=meta.get("generated_by") == GENERATOR))
    return found, problems


def detect_languages(repo: Path) -> tuple[str, ...]:
    """The repo's main programming languages, from tracked files (most files first)."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ()
    counts = Counter(CODE_LANGUAGES[PurePosixPath(p).suffix.lower()] for p in out.split("\0")
                     if p and not p.startswith(".rulebook/") and PurePosixPath(p).suffix.lower() in CODE_LANGUAGES)
    return tuple(lang for lang, _ in counts.most_common(MAX_DETECTED))


def make_plan(policies: Iterable[Policy], rules_dir: Path, default_languages: tuple[str, ...],
              only: Optional[set[str]] = None) -> Plan:
    plan = Plan()
    policies = list(policies)
    existing, plan.problems = read_rule_files(rules_dir)
    generated = {(r.policy_id, r.language): r for r in existing if r.generated}
    hand = {(r.policy_id, r.language): r for r in existing if not r.generated and r.policy_id}
    wanted: set[tuple[str, str]] = set()

    for policy in policies:
        languages = policy.languages or default_languages
        if not languages:
            plan.problems.append(f"Policy \"{policy.title}\" has no languages: add `languages:` under it, "
                                 "or set `languages:` in .rulebook/config.yml.")
            continue
        for lang in languages:
            wanted.add((policy.id, lang))
            if (policy.id, lang) in hand:
                plan.hand_written.append(hand[(policy.id, lang)])
                continue
            if only is not None and policy.id not in only:
                continue
            if lang not in LANGUAGE_INFO:
                plan.problems.append(f"\"{policy.title}\": rules for {lang} can't be generated yet; write one by hand.")
                continue
            current = generated.get((policy.id, lang))
            if current is None:
                plan.targets.append(Target(policy, lang, "new"))
            elif current.fingerprint != policy.fingerprint:
                plan.targets.append(Target(policy, lang, "changed", current))
            else:
                plan.unchanged.append(current)

    if only is None:
        plan.remove = [r for key, r in sorted(generated.items()) if key not in wanted]
    return plan


def apply(plan: Plan, results: list[SynthResult], rules_dir: Path) -> list[Path]:
    """Write the rules that passed their tests and delete removed ones. Returns the files
    touched. A target whose synthesis failed keeps whatever rule it had before."""
    rules_dir.mkdir(parents=True, exist_ok=True)
    touched: list[Path] = []
    for r in results:
        if not r.ok:
            continue
        rule_file = rules_dir / f"{r.rule_id}.yml"
        test_file = rules_dir / f"{r.rule_id}{r.test_extension}"
        rule_file.write_text(r.rule_yaml, encoding="utf-8")
        test_file.write_text(r.test_code, encoding="utf-8")
        touched += [rule_file, test_file]
    for old in plan.remove:
        for f in [old.path, *old.path.parent.glob(f"{old.path.stem}.*")]:
            if f.exists() and (f == old.path or f.suffix not in (".yml", ".yaml")):
                f.unlink()
                touched.append(f)
    return touched


def summary_markdown(plan: Plan, results: list[SynthResult], policies: Iterable[Policy]) -> str:
    """The rules PR's body (and `rulebook rules` output)."""
    titles = {p.id: p.title for p in policies}
    reason = {(t.policy.id, t.language): t.reason for t in plan.targets}
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    lines = []
    if ok:
        lines += ["| Policy | Language | Change | Test |", "|---|---|---|---|"]
        for r in ok:
            hits = len(r.matched_lines)
            lines.append(f"| {escape(titles.get(r.policy_id, r.policy_id))} | {r.language} | "
                         f"{reason.get((r.policy_id, r.language), 'new')} | passed: matched {hits} "
                         f"violation{'s' if hits != 1 else ''}, skipped the compliant code |")
    if failed:
        lines += ["", "**Could not be generated** (after the attempts shown). Write these rules by hand, "
                      "or reword the policy and push again:", ""]
        for r in failed:
            lines.append(f"- {escape(titles.get(r.policy_id, r.policy_id))} ({r.language}), "
                         f"{r.attempts} attempt{'s' if r.attempts != 1 else ''}: {escape(r.error)}")
    if plan.remove:
        lines += ["", "**Removed** (their policy, or that language, is no longer in policies.md):", ""]
        lines += [f"- `{r.rule_id}`" for r in plan.remove]
    if plan.problems:
        lines += ["", "**Needs attention:**", ""] + [f"- {escape(p)}" for p in plan.problems]
    if plan.hand_written:
        lines += ["", f"Left alone: {len(plan.hand_written)} hand-written rule(s)."]
    return "\n".join(lines).strip() or "Nothing to change: every policy's rules are up to date."
