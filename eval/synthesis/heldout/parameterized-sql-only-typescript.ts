async function queries(db: any, req: any, id: string) {
  // ruleid: parameterized-sql-only-typescript
  await db.query("SELECT * FROM users WHERE id = '" + id + "'");
  // ruleid: parameterized-sql-only-typescript
  await db.query(`SELECT * FROM users WHERE email = '${req.body.email}'`);
  // ruleid: parameterized-sql-only-typescript
  await db.execute("DELETE FROM sessions WHERE token = '" + req.headers.token + "'");
  // ok: parameterized-sql-only-typescript
  await db.query("SELECT * FROM users WHERE id = $1", [id]);
  // ok: parameterized-sql-only-typescript
  await db.execute("DELETE FROM sessions WHERE token = ?", [req.headers.token]);
  // ok: parameterized-sql-only-typescript
  await db.query("SELECT COUNT(*) FROM users");
}
