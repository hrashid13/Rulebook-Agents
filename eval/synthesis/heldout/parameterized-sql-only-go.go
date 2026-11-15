package heldout

import (
	"database/sql"
	"fmt"
)

func queries(db *sql.DB, id string, name string) {
	// ruleid: parameterized-sql-only-go
	db.Query(fmt.Sprintf("SELECT * FROM users WHERE id = '%s'", id))
	// ruleid: parameterized-sql-only-go
	db.Exec("DELETE FROM users WHERE name = '" + name + "'")
	// ruleid: parameterized-sql-only-go
	db.QueryRow(fmt.Sprintf("SELECT name FROM users WHERE id = %s", id))
	// ok: parameterized-sql-only-go
	db.Query("SELECT * FROM users WHERE id = $1", id)
	// ok: parameterized-sql-only-go
	db.Exec("DELETE FROM users WHERE name = ?", name)
	// ok: parameterized-sql-only-go
	db.QueryRow("SELECT COUNT(*) FROM users")
}
