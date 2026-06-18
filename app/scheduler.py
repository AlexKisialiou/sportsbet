from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = BackgroundScheduler(daemon=True)
_app = None


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
    print(f"[scheduler] started — auto_fetch={'on' if enabled else 'off'}, interval={interval}min")


def update_auto_fetch(enabled: bool, interval_min: int):
    if _scheduler.get_job("auto_fetch"):
        _scheduler.remove_job("auto_fetch")
    if enabled:
        _scheduler.add_job(
            _auto_fetch_job, "interval", minutes=interval_min,
            id="auto_fetch", replace_existing=True,
        )
