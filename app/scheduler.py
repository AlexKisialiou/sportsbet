from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta, timezone, date

_scheduler = BackgroundScheduler(daemon=True)
_app = None

_ODDS_HOUR_START = 7   # Minsk time
_ODDS_HOUR_END   = 24  # до 23:59 включительно

MINSK = timezone(timedelta(hours=3))


def _auto_fetch_job():
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[scheduler] {ts} — auto-fetch started")
    with _app.app_context():
        from .services.football_api import (
            fetch_and_save_cl_matches,
            fetch_and_save_pl_matches,
            fetch_and_save_wc_matches,
        )
        from .services.standings import maybe_generate_standings

        from .models import Setting

        for fn, league in [
            (fetch_and_save_cl_matches, "UCL"),
            (fetch_and_save_pl_matches, "PL"),
            (fetch_and_save_wc_matches, "WC"),
        ]:
            s = Setting.query.get(f"league_enabled_{league}")
            if s is not None and s.value == "0":
                print(f"[scheduler] {ts} {league}: skipped (disabled)")
                continue
            try:
                added, updated, existing = fn()
                print(f"[scheduler] {ts} {league}: {existing} existing, +{added} new, {updated} changed")
                maybe_generate_standings(league, _app)
            except Exception as e:
                print(f"[scheduler] {ts} {league} fetch failed: {e}")
                continue
            try:
                from .services.auto_featured import apply_featured_auto
                n = apply_featured_auto(league, _app)
                if n:
                    print(f"[scheduler] {ts} {league}: auto-featured {n} matches")
            except Exception as e:
                print(f"[scheduler] {ts} {league} auto-featured failed: {e}")
    print(f"[scheduler] {ts} — auto-fetch done")


def _auto_odds_job():
    """Refresh bookmaker odds for featured scheduled matches. Minsk 07:00–24:00 only."""
    now_minsk = datetime.utcnow() + timedelta(hours=3)
    if not (_ODDS_HOUR_START <= now_minsk.hour < _ODDS_HOUR_END):
        return

    ts = now_minsk.strftime("%Y-%m-%d %H:%M")
    print(f"[odds-scheduler] {ts} — odds refresh started")
    with _app.app_context():
        from .services.odds_api import fetch_odds_for_matches
        from .models import Match, Tour, Setting, db

        odds_enabled_s = Setting.query.get("odds_fetch_enabled")
        if odds_enabled_s is not None and odds_enabled_s.value == "0":
            print(f"[odds-scheduler] {ts} — disabled in settings, skipped")
            return

        for league in ("UCL", "PL", "WC"):
            s = Setting.query.get(f"league_enabled_{league}")
            if s is not None and s.value == "0":
                continue

            featured = (
                Match.query.join(Tour)
                .filter(Tour.league == league, Match.status == "scheduled", Match.featured == True)
                .all()
            )
            if not featured:
                print(f"[odds-scheduler] {ts} {league}: no featured matches, skipped")
                continue

            odds_input = [(m.id, m.home_team.name, m.away_team.name) for m in featured]
            try:
                odds_map = fetch_odds_for_matches(odds_input, league)
            except Exception as e:
                print(f"[odds-scheduler] {ts} {league} fetch failed: {e}")
                continue

            updated = 0
            for m in featured:
                odds = odds_map.get(m.id)
                if odds:
                    m.odds_home = odds.get("home")
                    m.odds_draw = odds.get("draw")
                    m.odds_away = odds.get("away")
                    updated += 1
            db.session.commit()
            print(f"[odds-scheduler] {ts} {league}: updated {updated}/{len(featured)} matches")

    print(f"[odds-scheduler] {ts} — odds refresh done")


def _is_quiet(hour: int, q_from: int, q_to: int) -> bool:
    if q_from > q_to:  # spans midnight, e.g. 23-07
        return hour >= q_from or hour < q_to
    return q_from <= hour < q_to


def _auto_tg_remind_job():
    """Send Telegram bet reminder before the first match of the nearest game day."""
    import os
    now_minsk = datetime.now(MINSK)

    with _app.app_context():
        from .models import Setting, Match, User, Prediction, db

        enabled_s = Setting.query.get("tg_remind_enabled")
        if not (enabled_s and enabled_s.value == "1"):
            return

        def _sv(key, default):
            s = Setting.query.get(key)
            try:
                return int(s.value) if s else default
            except (ValueError, TypeError):
                return default

        before_min = max(1, _sv("tg_remind_before_min", 60))
        q_from = max(0, min(_sv("tg_remind_quiet_from", 23), 23))
        q_to = max(0, min(_sv("tg_remind_quiet_to", 7), 23))

        if _is_quiet(now_minsk.hour, q_from, q_to):
            return

        featured = (
            Match.query
            .filter(Match.featured == True, Match.status == "scheduled")
            .order_by(Match.kickoff_time)
            .all()
        )
        if not featured:
            return

        # Find the first match of the nearest upcoming game day
        first_match = featured[0]
        first_ko_utc = first_match.kickoff_time.replace(tzinfo=timezone.utc)
        first_ko_minsk = first_ko_utc.astimezone(MINSK)
        game_day = first_ko_minsk.date()

        send_at = first_ko_minsk - timedelta(minutes=before_min)

        if now_minsk < send_at:
            return
        if now_minsk >= first_ko_minsk:
            return

        last_sent_s = Setting.query.get("tg_remind_last_sent")
        last_sent = last_sent_s.value if last_sent_s else ""
        if last_sent == game_day.isoformat():
            return

        # Build and send message
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            print("[tg-remind] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipped")
            return

        all_users = User.query.filter(User.is_bot == False).all()
        placed_ids = {p.user_id for p in Prediction.query.filter(
            Prediction.match_id.in_([m.id for m in featured])
        ).all()}

        lines = ["🔔 Напоминание о ставках!\n"]
        any_missing = False
        for m in featured:
            ko_minsk = m.kickoff_time.replace(tzinfo=timezone.utc).astimezone(MINSK)
            home = m.home_team.display_name if m.home_team else "?"
            away = m.away_team.display_name if m.away_team else "?"
            match_placed = {p.user_id for p in Prediction.query.filter_by(match_id=m.id).all()}
            missing = [u.display_name for u in all_users if u.id not in match_placed]
            lines.append(f"⚽ {ko_minsk.strftime('%H:%M')} — {home} vs {away}")
            if missing:
                any_missing = True
                lines.append(f"❌ Не поставили: {', '.join(missing)}")
            else:
                lines.append("✅ Все поставили")

        if not any_missing:
            # Still record as sent to avoid rechecking
            row = Setting.query.get("tg_remind_last_sent") or Setting(key="tg_remind_last_sent")
            row.value = game_day.isoformat()
            db.session.add(row)
            db.session.commit()
            return

        app_url = os.environ.get("APP_URL", "")
        if app_url:
            lines.append(f"\n👉 {app_url}")

        text = "\n".join(lines)
        try:
            import requests as req_lib
            resp = req_lib.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            resp.raise_for_status()
            ts = now_minsk.strftime("%Y-%m-%d %H:%M")
            print(f"[tg-remind] {ts} — sent for game_day={game_day}")
        except Exception as e:
            print(f"[tg-remind] send failed: {e}")
            return

        row = Setting.query.get("tg_remind_last_sent") or Setting(key="tg_remind_last_sent")
        row.value = game_day.isoformat()
        db.session.merge(row)
        db.session.commit()


def init_scheduler(app):
    global _app
    _app = app

    if not _scheduler.running:
        _scheduler.start()

    with app.app_context():
        from .models import Setting
        enabled_s = Setting.query.get("auto_fetch_enabled")
        interval_s = Setting.query.get("auto_fetch_interval_min")
        enabled = enabled_s is not None and enabled_s.value == "1"
        try:
            interval = max(5, min(int(interval_s.value), 120)) if interval_s else 15
        except (ValueError, TypeError):
            interval = 15

    if enabled:
        _scheduler.add_job(
            _auto_fetch_job, "interval", minutes=interval,
            id="auto_fetch", replace_existing=True,
        )

    _scheduler.add_job(
        _auto_odds_job, "interval", minutes=180,
        id="auto_odds", replace_existing=True,
    )

    _scheduler.add_job(
        _auto_tg_remind_job, "interval", minutes=5,
        id="auto_tg_remind", replace_existing=True,
    )

    with app.app_context():
        from .models import Setting
        tg_s = Setting.query.get("tg_remind_enabled")
        tg_on = tg_s is not None and tg_s.value == "1"

    print(f"[scheduler] started — auto_fetch={'on' if enabled else 'off'}, interval={interval}min, odds=every 3h (07-24 Minsk), tg_remind={'on' if tg_on else 'off'}")


def update_tg_remind(enabled: bool):
    """Called live when tg_remind settings change."""
    # The job always runs every 5 min and reads settings itself; no add/remove needed.
    ts = datetime.now(MINSK).strftime("%Y-%m-%d %H:%M")
    print(f"[tg-remind] {ts} — setting updated: enabled={enabled}")


def update_auto_fetch(enabled: bool, interval_min: int):
    if _scheduler.get_job("auto_fetch"):
        _scheduler.remove_job("auto_fetch")
    if enabled:
        _scheduler.add_job(
            _auto_fetch_job, "interval", minutes=interval_min,
            id="auto_fetch", replace_existing=True,
        )
