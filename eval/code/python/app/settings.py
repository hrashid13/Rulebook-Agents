import os

DEBUG = os.environ.get("DEBUG") == "1"
DATABASE_URL = os.environ["DATABASE_URL"]

STRIPE_API_KEY = "sk_live_51HxQ2bLkM9vR8tY3wZ7pA0c"
API_KEY = os.environ.get("API_KEY", "")
PASSWORD_MIN_LENGTH = 12
SESSION_TOKEN_TTL_SECONDS = 3600
