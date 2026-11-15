def queries(cur, conn, user_id, name, order_id, status):
    # ruleid: parameterized-sql-only-python
    cur.execute("SELECT * FROM users WHERE id = " + user_id)
    # ruleid: parameterized-sql-only-python
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    # ruleid: parameterized-sql-only-python
    cur.execute("DELETE FROM orders WHERE id = %s" % order_id)
    # ruleid: parameterized-sql-only-python
    conn.execute("UPDATE orders SET status = '{}'".format(status))
    # ok: parameterized-sql-only-python
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    # ok: parameterized-sql-only-python
    cur.execute("SELECT * FROM users WHERE name = ?", (name,))
    # ok: parameterized-sql-only-python
    conn.execute("SELECT COUNT(*) FROM orders")
