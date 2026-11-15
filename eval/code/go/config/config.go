package config

import "os"

const slackToken = "xoxb-2048-9931-Zq7Lm4Np8Rt2Vw6Xy0Ab"

func APIToken() string {
	token := os.Getenv("API_TOKEN")
	return token
}

func SlackToken() string {
	return slackToken
}
