"""`.rulebook/config.yml`: optional settings. Every field has a default, and an unknown
field is an error (a typo must not be silently ignored)."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .policies import LANGUAGES

FAIL_ON = ("none", "error", "any")
ISSUES = ("off", "command")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    languages: tuple[str, ...] = ()
    include: tuple[str, ...] = ()           # empty: everything
    exclude: tuple[str, ...] = ()
    model: str = ""                         # empty: the provider's default
    fail_on: str = "none"
    max_findings: int = 25
    issues: str = "command"

    def wants(self, path: str) -> bool:
        """Is this repo-relative path in scope? Patterns are globs ("src/**", "*.py") or
        folder prefixes ("src/")."""
        def hit(pattern: str) -> bool:
            if pattern.endswith("/"):
                return path.startswith(pattern)
            return fnmatch.fnmatch(path, pattern) or path.startswith(pattern.rstrip("/") + "/")
        if self.include and not any(hit(p) for p in self.include):
            return False
        return not any(hit(p) for p in self.exclude)


def _strings(value, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"config.yml: {key} must be a list of strings")
    return tuple(value)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        return Config()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config.yml must be a mapping of settings")
    known = {"languages", "paths", "model", "fail_on", "max_findings", "issues"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"config.yml: unknown setting(s) {', '.join(unknown)}; "
                          f"known: {', '.join(sorted(known))}")

    languages = []
    for raw in _strings(data.get("languages"), "languages"):
        lang = LANGUAGES.get(raw.lower())
        if lang is None:
            raise ConfigError(f"config.yml: unknown language {raw!r}")
        languages.append(lang)

    paths = data.get("paths") or {}
    if not isinstance(paths, dict) or set(paths) - {"include", "exclude"}:
        raise ConfigError("config.yml: paths takes only include and exclude")

    fail_on = str(data.get("fail_on", "none")).lower()
    if fail_on not in FAIL_ON:
        raise ConfigError(f"config.yml: fail_on must be one of {', '.join(FAIL_ON)}")
    issues = data.get("issues", "command")
    issues = "off" if issues is False else str(issues).lower()     # YAML reads a bare `off` as false
    if issues not in ISSUES:
        raise ConfigError(f"config.yml: issues must be one of {', '.join(ISSUES)}")
    max_findings = data.get("max_findings", 25)
    if not isinstance(max_findings, int) or isinstance(max_findings, bool) or not 1 <= max_findings <= 200:
        raise ConfigError("config.yml: max_findings must be a whole number from 1 to 200")
    model = data.get("model") or ""
    if not isinstance(model, str):
        raise ConfigError("config.yml: model must be a string")

    return Config(languages=tuple(dict.fromkeys(languages)),
                  include=_strings(paths.get("include"), "paths.include"),
                  exclude=_strings(paths.get("exclude"), "paths.exclude"),
                  model=model, fail_on=fail_on, max_findings=max_findings, issues=issues)
