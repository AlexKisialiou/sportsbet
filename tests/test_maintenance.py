from unittest.mock import patch

import pytest
import requests

from .conftest import make_user
from app.models import db, Setting


@pytest.fixture(autouse=True)
def reset_rate_limits():
    from app.limiter import limiter

    limiter.reset()
    yield
    limiter.reset()


def set_mode(app, enabled):
    with app.app_context():
        row = db.session.get(Setting, "maintenance_enabled") or Setting(key="maintenance_enabled")
        row.value = "1" if enabled else "0"
        db.session.add(row)
        db.session.commit()


def authenticate(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id


@pytest.mark.parametrize("role", ["guest", "user", "admin"])
def test_maintenance_blocks_pages_and_api(app, client, role):
    if role != "guest":
        authenticate(client, make_user(app, role, is_admin=role == "admin"))
    set_mode(app, True)

    page = client.get("/")
    assert page.status_code == 503
    assert "технические работы" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"
    api = client.post("/api/prediction", json={})
    assert api.status_code == 503
    assert "error" in api.json
    assert client.post("/api/settings/maintenance", json={"enabled": False}).status_code == 503


def test_maintenance_preserves_superadmin_access(app, client):
    authenticate(client, make_user(app, "super", is_superuser=True))
    set_mode(app, True)

    response = client.get("/superadmin")
    assert response.status_code == 200
    assert "Открыть сайт" in response.get_data(as_text=True)


def test_maintenance_preserves_login_health_and_static(app, client):
    set_mode(app, True)

    assert client.get("/login").status_code == 200
    assert client.get("/health").json == {"ok": True}
    assert client.get("/static/css/main.css").status_code == 200


def test_regular_login_is_blocked_during_maintenance(app, client):
    from .conftest import login
    make_user(app, "player")
    set_mode(app, True)

    assert login(client, "player").status_code == 503
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_superuser_can_log_back_in_during_maintenance(app, client, monkeypatch):
    from .conftest import login
    monkeypatch.setenv("ADMIN_PASSWORD", "super-password")
    user_id = make_user(app, "super", is_superuser=True)
    set_mode(app, True)

    assert login(client, "super", "super-password").status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == user_id


def test_toggle_persists_notifies_and_avoids_duplicate_notifications(app, client):
    authenticate(client, make_user(app, "super", is_superuser=True))
    with patch("app.services.telegram.send_maintenance_notification") as notify:
        for enabled in (True, True, False):
            response = client.post("/api/settings/maintenance", json={"enabled": enabled})
            assert response.status_code == 200
            assert response.json["enabled"] == enabled
            with app.app_context():
                assert db.session.get(Setting, "maintenance_enabled").value == ("1" if enabled else "0")

    assert [call.args[0] for call in notify.call_args_list] == [True, False]


def test_failed_telegram_delivery_does_not_hide_mode_change(app, client):
    authenticate(client, make_user(app, "super", is_superuser=True))
    with patch("app.services.telegram.send_maintenance_notification", side_effect=requests.Timeout):
        response = client.post("/api/settings/maintenance", json={"enabled": True})

    assert response.json["enabled"] is True
    assert "warning" in response.json
    with app.app_context():
        assert db.session.get(Setting, "maintenance_enabled").value == "1"


@pytest.mark.parametrize("data", [{}, {"enabled": "true"}, {"enabled": 1}, []])
def test_toggle_rejects_invalid_input(app, client, data):
    authenticate(client, make_user(app, "super", is_superuser=True))
    assert client.post("/api/settings/maintenance", json=data).status_code == 400


def test_toggle_requires_superuser(app, client):
    authenticate(client, make_user(app, "admin", is_admin=True))
    with patch("app.services.telegram.send_maintenance_notification") as notify:
        assert client.post("/api/settings/maintenance", json={"enabled": True}).status_code == 302
    notify.assert_not_called()
    with app.app_context():
        assert db.session.get(Setting, "maintenance_enabled") is None


@pytest.mark.parametrize("enabled,expected", [(True, "🔴 Сайт временно недоступен."), (False, "🟢 Сайт снова доступен")])
def test_maintenance_notification_text(monkeypatch, enabled, expected):
    from app.services.telegram import send_maintenance_notification

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    with patch("app.services.telegram.requests.post") as send:
        send.return_value.json.return_value = {"ok": True}
        send_maintenance_notification(enabled)
    assert expected in send.call_args.kwargs["json"]["text"]
