# Rulebook Agents

Write your team's coding rules in plain English. Rulebook Agents turns each one into a tested [Semgrep](https://semgrep.dev) rule, checks every pull request against them, and explains each real problem it finds, quoting the rule it breaks.

- **Rules:** an agent drafts a Semgrep rule and a test file for each policy and opens a pull request with them. Nothing is enforced until a person merges it.
- **Review:** on every pull request, Semgrep scans the changed lines, one agent separates real problems from noise, and another explains each real problem and quotes your policy word for word.
- **Report:** one comment on the PR, updated on every push. A maintainer can turn any finding into a GitHub issue with `/rulebook file 1`.

Works with any language Semgrep supports, and any model with an Anthropic or OpenAI-compatible API.

> **Status: early development.** The design is in [docs/DESIGN.md](docs/DESIGN.md). The GitHub Action is not usable yet.

## What works today

You need Python 3.11+, git, and [Semgrep](https://semgrep.dev) installed in its own environment:

```
uv tool install semgrep          # or: pipx install semgrep
pip install -e ".[dev]"
```

**Check your policies file** (clear errors with line numbers for anything it can't read):

```
rulebook policies .rulebook/policies.md
```

**Review your branch** against the rules in `.rulebook/rules/`:

```
export ANTHROPIC_API_KEY=...            # PowerShell: $env:ANTHROPIC_API_KEY="..."
rulebook review --base main
```

It looks at the lines your branch changed (including uncommitted edits to tracked files), runs your Semgrep rules on them, has one agent separate real problems from noise and another explain each real problem with a word-for-word quote of the policy, and prints a Markdown report. Options:

| Option | What it does |
|---|---|
| `--base REF` | Branch to compare with (default: origin's default branch, then `main`/`master`) |
| `--output FILE` | Write the report to a file |
| `--format json` | Machine-readable report |
| `--fail-on error\|any` | Exit 1 when problems reach that level (default `none`) |

Answers are cached in `.rulebook-cache/`, so running the review again on unchanged code costs nothing. Rules are written by hand for now; generating them from `policies.md` is milestone M3. See [examples/policies.md](examples/policies.md) for the policy format and [tests/fixtures](tests/fixtures) for example rules in Python, TypeScript, Java, Go and C#.

**Model settings** (environment variables, all optional except the key):

| Variable | Default |
|---|---|
| `ANTHROPIC_API_KEY` or `RULEBOOK_API_KEY` | (required) |
| `RULEBOOK_PROVIDER` | `anthropic`; or `openai` for any OpenAI-compatible API (OpenAI, OpenRouter, Ollama) |
| `RULEBOOK_MODEL` | `claude-haiku-5-5` for Anthropic |
| `RULEBOOK_BASE_URL` | needed for `openai`, e.g. `http://localhost:11434/v1` |
| `RULEBOOK_MAX_CALLS` | `200` model calls per run |
| `RULEBOOK_PRICE_IN_PER_MTOK` / `_OUT_` | `0` (set them to see cost in the report) |

## Roadmap

| Milestone | What |
|---|---|
| M0 | Package, CLI, `policies.md` parser, CI |
| M1 | `rulebook review` on a local branch, in several languages |
| M2 | The review flow as a GitHub Action: PR comment and check |
| M3 | `rulebook rules`: policies to tested Semgrep rules, opened as a PR |
| M4 | `/rulebook file N`: findings to GitHub issues |
| M5 | Published image, `v1` release, GitHub Marketplace listing |

## Credits

Rulebook Agents grew out of **Docker Locker**, a final project for ISM 6564 (Text Analytics) at the University of South Florida by Hesham Rashid and Zachary Zambrana. The triage agent, the rule synthesizer, the PR diff handling and the evaluation approach started from Zachary's code in that project; the policy-citation agent, model client, guardrails and red-team suite started from Hesham's. Source: `ISM-6564-Final-Project-Docker-Locker` at commit `c49d12c`.

## License

[MIT](LICENSE)
