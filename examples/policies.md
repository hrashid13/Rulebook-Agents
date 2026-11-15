# Example coding policies

Copy this file to `.rulebook/policies.md` in your repo and replace the policies with your own.
Each "## " heading is one policy. The fields under it are optional.

## Parameterized SQL only
category: security
languages: python, java, typescript
severity: error

SQL must be built with parameters or an ORM, never by concatenating or formatting strings.

## No hardcoded secrets
category: security
severity: error

API keys, passwords and tokens must never appear in source code. Load them from environment variables or a secret manager.

## No Thread.Abort
category: bug_risk
languages: csharp

Do not call Thread.Abort() in managed code. Use a CancellationToken for cooperative cancellation.

## No print debugging
category: style
languages: python
severity: info

Use the logging module instead of print() in application code.
