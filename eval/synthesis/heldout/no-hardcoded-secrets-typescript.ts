// ruleid: no-hardcoded-secrets-typescript
const apiKey = "sk_live_51HxQ2bLkM9vR8tY3wZ7pA0c";
// ruleid: no-hardcoded-secrets-typescript
const dbPassword = "hunter2-prod";
const config = {
  // ruleid: no-hardcoded-secrets-typescript
  authToken: "xoxb-2048-9931-Zq7Lm4Np8Rt2Vw6Xy0Ab",
  // ok: no-hardcoded-secrets-typescript
  apiKeyFromEnv: process.env.API_KEY,
  // ok: no-hardcoded-secrets-typescript
  passwordMinLength: 12,
};
// ok: no-hardcoded-secrets-typescript
const secretName = process.env.SECRET_NAME;
