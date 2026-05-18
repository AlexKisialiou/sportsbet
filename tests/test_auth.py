import pytest
from .conftest import make_user, login


def test_login_success(app, client):
    make_user(app, "alice")
    resp = login(client, "alice")
    assert resp.status_code == 302
    assert resp.location in ("/", "http://localhost/")


def test_login_wrong_password(app, client):
    make_user(app, "alice")
    resp = client.post("/login", data={"username": "alice", "password": "wrong"},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert b"login" in resp.data.lower()


def test_login_unknown_user(app, client):
    resp = client.post("/login", data={"username": "nobody", "password": "pass123"},
                       follow_redirects=False)
    assert resp.status_code == 200


def test_login_bot_blocked(app, client):
    resp = client.post("/login", data={"username": "bender", "password": ""},
                       follow_redirects=False)
    assert resp.status_code == 200
    assert b"302" not in resp.data


def test_logout_clears_session(app, client):
    make_user(app, "alice")
    login(client, "alice")

    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302

    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_index_requires_login(app, client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_admin_requires_admin(app, client):
    make_user(app, "alice", is_admin=False)
    login(client, "alice")
    resp = client.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 403)


def test_admin_accessible_by_admin(app, client):
    make_user(app, "admin", is_admin=True)
    login(client, "admin")
    resp = client.get("/admin")
    assert resp.status_code == 200
