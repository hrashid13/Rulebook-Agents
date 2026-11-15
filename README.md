# Rulebook Agents

Write your team's coding rules in plain English. Rulebook Agents turns each one into a tested [Semgrep](https://semgrep.dev) rule, checks every pull request against them, and explains each real problem it finds, quoting the rule it breaks.

- **Rules:** an agent drafts a Semgrep rule and a test file for each policy and opens a pull request with them. Nothing is enforced until a person merges it.
- **Review:** on every pull request, Semgrep scans the changed lines, one agent separates real problems from noise, and another explains each real problem and quotes your policy word for word.
- **Report:** one comment on the PR, updated on every push. A maintainer can turn any finding into a GitHub issue with `/rulebook file 1`.

Works with any language Semgrep supports, and any model with an Anthropic or OpenAI-compatible API.

> **Status: early development.** The design is in [docs/DESIGN.md](docs/DESIGN.md). The GitHub Action is not usable yet.

## What works today

```
pip install -e ".[dev]"
rulebook policies examples/policies.md
```

`rulebook policies` checks a `policies.md` file and lists the policies it defines, with clear errors (and line numbers) for anything it can't read. See [examples/policies.md](examples/policies.md) for the format.

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
