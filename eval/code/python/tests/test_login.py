from app.auth import login

TEST_PASSWORD = "correct-horse-battery-staple"


def test_login_accepts_the_right_password(client):
    response = login(client, "alice", TEST_PASSWORD)
    print(response.json())
    assert response.status_code == 200
