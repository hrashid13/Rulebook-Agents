package auth

import (
	"crypto/md5"
	"encoding/base64"
	"encoding/hex"
)

func HashPassword(password string) string {
	sum := md5.Sum([]byte(password))
	return hex.EncodeToString(sum[:])
}

// ContentMD5 is the Content-MD5 header value the storage API requires on uploads.
func ContentMD5(body []byte) string {
	sum := md5.Sum(body)
	return base64.StdEncoding.EncodeToString(sum[:])
}
