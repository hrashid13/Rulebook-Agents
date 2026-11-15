import logging

log = logging.getLogger(__name__)


def get_user(cur, user_id):
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    return cur.fetchone()


def legacy_report(cur, month):
    # Pre-existing problem on an unchanged line: the review must not report it.
    cur.execute("SELECT * FROM reports WHERE month = '" + month + "'")
    return cur.fetchall()


def delete_user(cur, request):
    user_id = request.args["id"]
    cur.execute("DELETE FROM users WHERE id = " + user_id)
    print("deleted", user_id)
