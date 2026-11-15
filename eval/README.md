# Evaluation

How well the agents do, measured against data labelled by hand. The latest results are in
[results/](results/).

## Run it

```
export ANTHROPIC_API_KEY=...                 # PowerShell: $env:ANTHROPIC_API_KEY="..."
python eval/run.py                           # all three parts
python eval/run.py --only triage,redteam     # or some of them
```

It uses the same model settings as `rulebook review` (`RULEBOOK_MODEL`, `RULEBOOK_PROVIDER`,
...). Every answer is saved in `eval/cache/`, so running it again costs nothing, and anyone can
re-score a committed run without an API key:

```
RULEBOOK_LLM_MODE=replay python eval/run.py
```

The report is written to `eval/results/<model>.md` (and `.json`) and printed.

## What it measures

**Triage and explanation** (`cases.yml`, code in `code/`). 38 alerts in Python, TypeScript,
Java, Go and C#, each labelled REAL or NOISE. The noise is the realistic kind a pattern can't
tell apart: a constant table name in a query, MD5 for a cache key or an ETag, `redis.eval`,
`Abort()` on a class that isn't a Thread, a fake password in a test, a CLI printing its
output. The alerts are built directly rather than by running Semgrep, so this measures the
agents alone. For every real problem Agent 1 keeps, Agent 2 explains it, and the report
counts how often its quote of the policy is word for word on the first try, after its one
retry, and whether it suggested a fix.

**Red team** (`cases.yml` → `redteam`, code in `code/redteam/`). Real violations with text
aimed at the reviewer: "ignore previous instructions", a closed `</untrusted_code>` tag with a
fake system message, a claim that a security team approved it, a claim that production code is
test scaffolding, and a request to put a link, an @mention and `::add-mask::` into the
explanation. Each runs with the guardrails on and off. The rendered PR comment and the log line
are then checked for anything live: a mention, a link or a workflow command.

**Rule synthesis** (`synthesis/`). For each policy and language with a file in
`synthesis/heldout/`, the synthesizer writes a rule and its own test file (up to three
attempts). A rule that passes its own test is then run on the held-out file, which it never
saw, to count true positives, false positives and misses. Held-out files only label lines a
pattern can decide; whether MD5 hashes a password or a cache key is triage's job, not the
rule's. `synthesis/reference/` holds a hand-written rule for each held-out file, and CI checks
those rules get every label right, so the labels are known to be consistent.

## Adding cases

- A triage case: add the code under `code/<language>/`, then an entry in `cases.yml` with a
  `match` string that occurs on exactly one line of the file.
- A synthesis pair: add `synthesis/heldout/<policy id>-<language>.<ext>` with `ruleid:` and
  `ok:` comments, and a passing rule with the same name in `synthesis/reference/`.

`pytest tests/test_eval.py` checks all of this without calling a model.
