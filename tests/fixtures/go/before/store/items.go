package store

import (
	"database/sql"
	"fmt"
)

func ItemByID(db *sql.DB, id string) (*sql.Rows, error) {
	return db.Query("SELECT * FROM items WHERE id = $1", id)
}

var _ = fmt.Sprintf
