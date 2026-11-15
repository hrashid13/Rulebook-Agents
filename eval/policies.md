# Evaluation policies

The policies the evaluation runs against. The text under each heading is what the agents
quote; keep it stable, or results from different runs stop being comparable.

## Parameterized SQL only
category: security
languages: python, typescript, java, go, csharp
severity: error

SQL must be built with parameters or an ORM, never by concatenating or formatting strings with values that come from outside the program. Table and column names taken from constants in the code are fine.

## No hardcoded secrets
category: security
languages: python, typescript, java, go, csharp
severity: error

API keys, passwords, tokens and connection strings with credentials must never appear in source code. Load them from environment variables or a secret manager. Obvious fake values in test code are fine.

## No weak password hashing
category: security
languages: python, java, go
severity: error

Never hash passwords or build signatures with MD5 or SHA-1. Use bcrypt, scrypt or Argon2 for passwords and SHA-256 or better for signatures. MD5 or SHA-1 checksums of non-secret data, such as cache keys or ETags, are fine.

## No print debugging
category: style
languages: python
severity: info

Use the logging module instead of print() in application code. Command-line tools may print their output, and tests may print.

## No eval
category: security
languages: typescript
severity: error

Never call JavaScript's eval() or new Function() on strings. Other APIs that happen to be named eval, such as Redis scripting, are not covered.

## No Thread.Abort
category: bug_risk
languages: csharp
severity: warning

Do not call Thread.Abort() in managed code. Use a CancellationToken for cooperative cancellation.
