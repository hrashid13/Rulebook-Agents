import logging
import sqlite3

log = logging.getLogger(__name__)
USERS_TABLE = "users"


def get_connection():
    return sqlite3.connect("app.db")


def find_user(request):
    user_id = request.args["id"]
    cur = get_connection().cursor()
    cur.execute("SELECT name, email FROM users WHERE id = '" + user_id + "'")
    row = cur.fetchone()
    print("found user", row)
    return row


def count_users():
    cur = get_connection().cursor()
    cur.execute(f"SELECT COUNT(*) FROM {USERS_TABLE}")
    return cur.fetchone()[0]


def rename_user(user_id, new_name):
    cur = get_connection().cursor()
    cur.execute("UPDATE users SET name = '%s' WHERE id = %s" % (new_name, user_id))
    log.info("renamed user %s", user_id)
