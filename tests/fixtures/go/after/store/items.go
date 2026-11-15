package store

import (
	"database/sql"
	"fmt"
)

func ItemByID(db *sql.DB, id string) (*sql.Rows, error) {
	return db.Query("SELECT * FROM items WHERE id = $1", id)
}

func ItemsByOwner(db *sql.DB, owner string) (*sql.Rows, error) {
	return db.Query(fmt.Sprintf("SELECT * FROM items WHERE owner = '%s'", owner))
}

var _ = fmt.Sprintf
