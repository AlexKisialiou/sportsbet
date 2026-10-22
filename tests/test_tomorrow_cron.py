from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from tg_notify import tomorrow as cron


def test_tomorrow_window_uses_minsk_date():
    day, start, end = cron.tomorrow_window(datetime(2030, 1, 1, 22, tzinfo=timezone.utc))
    assert day.isoformat() == "2030-01-03"
    assert start == datetime(2030, 1, 2, 21)
    assert end == datetime(2030, 1, 3, 21)


@pytest.mark.parametrize("suspended,post_count", [("suspended", 1), ("not_suspended", 0)])
def test_resume_waits_for_health(suspended, post_count):
    status, health = MagicMock(), MagicMock()
    status.json.return_value = {"suspended": suspended}
    health.status_code = 200
    health.json.return_value = {"ok": True}
    with patch.object(cron.requests, "get", side_effect=[status, health]), \
         patch.object(cron.requests, "post") as resume:
        cron.resume_and_wait("key", "srv-test", "https://example.com")
    assert resume.call_count == post_count
    if post_count:
        assert resume.call_args.args[0].endswith("/resume")


def test_resume_rejects_unknown_state():
    with patch.object(cron.requests, "get") as get:
        get.return_value.json.return_value = {"suspended": "unknown"}
        with pytest.raises(RuntimeError):
            cron.resume_and_wait("key", "srv-test", "https://example.com")


def test_health_timeout_is_explicit():
    with patch.object(cron.requests, "get") as get, \
         patch.object(cron.time, "monotonic", side_effect=[0, 601]):
        get.return_value.json.return_value = {"suspended": "not_suspended"}
        with pytest.raises(RuntimeError, match="10 minutes"):
            cron.resume_and_wait("key", "srv-test", "https://example.com")


@pytest.fixture
def run_env(monkeypatch):
    for key in ("DATABASE_URL", "RENDER_API_KEY", "RENDER_SERVICE_ID", "APP_URL",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(key, "test")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(True,), None]
    with patch.object(cron, "load_dotenv"), \
         patch.object(cron.psycopg2, "connect", return_value=conn), \
         patch.object(cron, "get_matches") as matches, \
         patch.object(cron, "resume_and_wait") as resume, \
         patch.object(cron, "send_notice") as send:
        matches.return_value = [datetime(2030, 1, 2, 15)]
        yield conn, cursor, matches, resume, send


def test_no_matches_does_not_resume_or_notify(run_env):
    conn, _, matches, resume, send = run_env
    matches.return_value = []
    cron.run(datetime(2030, 1, 1, 15, tzinfo=timezone.utc))
    resume.assert_not_called()
    send.assert_not_called()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()


def test_success_opens_before_notification_and_marks_date(run_env):
    conn, cursor, _, resume, send = run_env
    send.side_effect = lambda *args: conn.commit.assert_called_once()
    cron.run(datetime(2030, 1, 1, 15, tzinfo=timezone.utc))
    resume.assert_called_once()
    send.assert_called_once()
    statements = [(str(call.args[0]), call.args[1:]) for call in cursor.execute.call_args_list]
    assert any(args == (("maintenance_enabled",),) for _, args in statements)
    assert any(args == (("betting_locked",),) for _, args in statements)
    assert any("tg_tomorrow_sent" in query and args == (("2030-01-02",),) for query, args in statements)
    conn.close.assert_called_once()


def test_health_failure_does_not_open_or_notify(run_env):
    conn, _, _, resume, send = run_env
    resume.side_effect = RuntimeError("Not ready")
    with pytest.raises(RuntimeError):
        cron.run()
    conn.commit.assert_not_called()
    send.assert_not_called()


def test_duplicate_date_is_skipped(run_env):
    _, cursor, matches, resume, send = run_env
    cursor.fetchone.side_effect = [(True,), ("2030-01-02",)]
    cron.run(datetime(2030, 1, 1, 15, tzinfo=timezone.utc))
    matches.assert_not_called()
    resume.assert_not_called()
    send.assert_not_called()


def test_delivery_failure_does_not_mark_success(run_env):
    _, cursor, _, _, send = run_env
    send.side_effect = requests.Timeout()
    with pytest.raises(requests.Timeout):
        cron.run()
    assert not any(
        "INSERT INTO settings (key, value) VALUES ('tg_tomorrow_sent'" in str(call.args[0])
        for call in cursor.execute.call_args_list
    )
