package heldout

import "os"

// ruleid: no-hardcoded-secrets-go
const apiKey = "sk_live_51HxQ2bLkM9vR8tY3wZ7pA0c"

// ruleid: no-hardcoded-secrets-go
var dbPassword = "hunter2-prod"

// ok: no-hardcoded-secrets-go
const passwordMinLength = 12

func load() string {
	// ruleid: no-hardcoded-secrets-go
	token := "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
	// ok: no-hardcoded-secrets-go
	secret := os.Getenv("APP_SECRET")
	return token + secret
}
