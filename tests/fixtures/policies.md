# Fixture policies (used by the tests)

## Parameterized SQL only
category: security
languages: python, typescript, java, go
severity: error

SQL must be built with parameters or an ORM, never by concatenating or formatting strings. User input in a query string lets an attacker change the query.

## No print debugging
category: style
languages: python
severity: info

Use the logging module instead of print() in application code.

## No eval
category: security
languages: typescript
severity: error

Never pass strings to eval() or new Function(). They run arbitrary code.

## No Thread.Abort
category: bug_risk
languages: csharp
severity: warning

Do not call Thread.Abort() in managed code. Use a CancellationToken for cooperative cancellation.
