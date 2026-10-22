from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from .conftest import make_match, make_user


def test_bet_reminder_summarizes_missing_predictions_in_russian(app):
    alexey_id = make_user(app, "alexey")
    artem_id = make_user(app, "artem")
    first_match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    second_match_id = make_match(app, kickoff=datetime(2030, 1, 1, 18, 0))
    third_match_id = make_match(app, kickoff=datetime(2030, 1, 1, 21, 0))

    from app.models import db, Match, Prediction, User
    from app.services.telegram import build_bet_reminder

    with app.app_context():
        alexey = db.session.get(User, alexey_id)
        artem = db.session.get(User, artem_id)
        alexey.nickname = "Алексей"
        artem.nickname = "Артем"
        db.session.add_all([
            Prediction(user_id=alexey_id, match_id=first_match_id, home_score=1, away_score=0),
            Prediction(user_id=alexey_id, match_id=second_match_id, home_score=1, away_score=0),
        ])
        matches = [
            db.session.get(Match, first_match_id),
            db.session.get(Match, second_match_id),
            db.session.get(Match, third_match_id),
        ]
        db.session.commit()

        message = build_bet_reminder(matches, [alexey, artem])

    assert message.startswith(
        "🔔 На сегодняшние матчи не все успели сделать прогнозы:\n"
        "⏳ До первого матча осталось:"
    )
    assert "Алексей — не хватает прогнозов на 1 матч" in message
    assert "Артем — не хватает прогнозов на 3 матча" in message


def test_bet_reminder_returns_none_when_everyone_has_predicted(app):
    user_id = make_user(app, "alexey")
    match_id = make_match(app)

    from app.models import db, Match, Prediction, User
    from app.services.telegram import build_bet_reminder

    with app.app_context():
        db.session.add(Prediction(
            user_id=user_id,
            match_id=match_id,
            home_score=1,
            away_score=0,
        ))
        db.session.commit()
        message = build_bet_reminder(
            [db.session.get(Match, match_id)],
            [db.session.get(User, user_id)],
        )

    assert message is None


def test_format_time_until_first_match_uses_russian_count_words():
    from app.services.telegram import format_time_until

    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    kickoff = now + timedelta(hours=2, minutes=15)

    assert format_time_until(kickoff, now=now) == "⏳ До первого матча осталось: 2 часа 15 минут"


@pytest.fixture
def reminder_job(app, monkeypatch):
    from app import scheduler
    from app.models import db, Setting

    monkeypatch.setattr(scheduler, "_app", app)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    with app.app_context():
        db.session.add(Setting(key="tg_remind_enabled", value="1"))
        db.session.commit()

    with patch("app.scheduler.datetime") as clock, patch("requests.post") as send:
        def run(hour, minute=0, day=1):
            clock.now.return_value = datetime(2030, 1, day, hour, minute, tzinfo=scheduler.MINSK)
            scheduler._auto_tg_remind_job()

        yield run, send


@pytest.mark.parametrize(
    "kickoff_hour,run_hour,run_minute,expected",
    [
        (18, 11, 59, 0),
        (18, 12, 0, 1),
        (18, 12, 4, 1),
        (18, 12, 5, 0),
        (18, 16, 59, 0),
        (18, 17, 0, 1),
        (18, 18, 0, 0),
        (9, 8, 0, 1),
        (8, 7, 0, 0),
        (23, 22, 0, 0),
        (10, 12, 0, 0),
        (18, 23, 0, 0),
    ],
)
def test_reminder_schedule(app, reminder_job, kickoff_hour, run_hour, run_minute, expected):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, kickoff_hour - 3))
    run, send = reminder_job

    run(run_hour, run_minute)

    assert send.call_count == expected


def test_reminder_sends_each_slot_once(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    run, send = reminder_job

    for hour in (12, 17):
        run(hour)
        run(hour, 1)

    assert send.call_count == 2
    assert "6 часов" in send.call_args_list[0].kwargs["json"]["text"]
    assert "1 час" in send.call_args_list[1].kwargs["json"]["text"]


def test_reminder_combines_coincident_slots(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 10))
    run, send = reminder_job

    run(12)
    run(12, 1)

    assert send.call_count == 1
    from app.models import db, Setting
    with app.app_context():
        for key in ("tg_remind_noon_day", "tg_remind_hour_day"):
            assert db.session.get(Setting, key).value == "2030-01-01"


def test_reminder_does_not_send_for_tomorrow(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 2, 15))
    run, send = reminder_job

    run(12)
    run(17)

    send.assert_not_called()


def test_reminder_only_counts_todays_matches(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    make_match(app, kickoff=datetime(2030, 1, 2, 15))
    run, send = reminder_job

    run(12)

    assert "на 1 матч" in send.call_args.kwargs["json"]["text"]


def test_reminder_skips_complete_predictions_in_both_slots(app, reminder_job):
    user_id = make_user(app, "player")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15))
    from app.models import db, Prediction
    with app.app_context():
        db.session.add(Prediction(user_id=user_id, match_id=match_id, home_score=0, away_score=0))
        db.session.commit()
    run, send = reminder_job

    run(12)
    run(17)

    send.assert_not_called()


def test_reminder_does_not_restart_after_first_match_finishes(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 7), status="finished")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    run, send = reminder_job

    run(12)
    run(17)

    send.assert_not_called()


def test_reminder_respects_disabled_toggle(app, reminder_job):
    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    from app.models import db, Setting
    with app.app_context():
        db.session.get(Setting, "tg_remind_enabled").value = "0"
        db.session.commit()
    run, send = reminder_job

    run(12)
    run(17)

    send.assert_not_called()


def test_reminder_retries_failed_delivery_without_marking_slot(app, reminder_job):
    import requests
    from app.models import db, Setting

    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    run, send = reminder_job
    send.side_effect = requests.Timeout("Delivery timed out")

    run(12)
    with app.app_context():
        assert db.session.get(Setting, "tg_remind_noon_day") is None

    send.side_effect = None
    run(12, 1)
    assert send.call_count == 2
    with app.app_context():
        assert db.session.get(Setting, "tg_remind_noon_day").value == "2030-01-01"


def test_reminder_can_send_hour_slot_after_complete_noon(app, reminder_job):
    from app.models import db, Prediction

    user_id = make_user(app, "player")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15))
    with app.app_context():
        db.session.add(Prediction(user_id=user_id, match_id=match_id, home_score=0, away_score=0))
        db.session.commit()
    run, send = reminder_job

    run(12)
    send.assert_not_called()
    make_user(app, "new-player")
    run(17)

    send.assert_called_once()
    assert "new-player" in send.call_args.kwargs["json"]["text"]


def test_automatic_reminders_pause_during_maintenance(app, reminder_job):
    from app.models import db, Setting

    make_user(app, "player")
    make_match(app, kickoff=datetime(2030, 1, 1, 15))
    with app.app_context():
        db.session.add(Setting(key="maintenance_enabled", value="1"))
        db.session.commit()
    run, send = reminder_job
    run(12)
    run(17)
    send.assert_not_called()
