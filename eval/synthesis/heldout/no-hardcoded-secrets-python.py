import os

# ruleid: no-hardcoded-secrets-python
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY"
# ruleid: no-hardcoded-secrets-python
db_password = "s3cr3t-prod"
# ruleid: no-hardcoded-secrets-python
GITHUB_TOKEN = 'ghp_abcdefghijklmnopqrstuvwxyz0123456789'
# ok: no-hardcoded-secrets-python
API_KEY = os.environ["API_KEY"]
# ok: no-hardcoded-secrets-python
db_password_env = os.getenv("DB_PASSWORD")
# ok: no-hardcoded-secrets-python
PASSWORD_MIN_LENGTH = 12
# ok: no-hardcoded-secrets-python
greeting = "hello world"
