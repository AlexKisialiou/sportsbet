from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

_scheduler = BackgroundScheduler(daemon=True)
_app = None

_ODDS_HOUR_START = 7   # Minsk time
_ODDS_HOUR_END   = 24  # до 23:59 включительно


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
    print(f"[scheduler] started — auto_fetch={'on' if enabled else 'off'}, interval={interval}min, odds=every 3h (07-24 Minsk)")


def update_auto_fetch(enabled: bool, interval_min: int):
    if _scheduler.get_job("auto_fetch"):
        _scheduler.remove_job("auto_fetch")
    if enabled:
        _scheduler.add_job(
            _auto_fetch_job, "interval", minutes=interval_min,
            id="auto_fetch", replace_existing=True,
        )
