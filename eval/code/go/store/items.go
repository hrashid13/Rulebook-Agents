package store

import (
	"database/sql"
	"fmt"
)

const itemsTable = "items"

func GetItem(db *sql.DB, id string) (*sql.Rows, error) {
	return db.Query(fmt.Sprintf("SELECT * FROM items WHERE id = '%s'", id))
}

func ListItems(db *sql.DB, category string) (*sql.Rows, error) {
	return db.Query(fmt.Sprintf("SELECT * FROM %s WHERE category = $1", itemsTable), category)
}
