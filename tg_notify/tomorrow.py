"""Render cron: 0 15 * * * (18:00 Minsk). Does not import the Flask app."""

import logging
import os
import time
from contextlib import closing
from datetime import datetime, time as day_time, timedelta, timezone
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql
import requests
from dotenv import load_dotenv

MINSK = timezone(timedelta(hours=3))
LOG = logging.getLogger(__name__)


def tomorrow_window(now):
    tomorrow = now.astimezone(MINSK).date() + timedelta(days=1)
    start = datetime.combine(tomorrow, day_time(), tzinfo=MINSK)
    return tomorrow, start.astimezone(timezone.utc).replace(tzinfo=None), (
        start + timedelta(days=1)
    ).astimezone(timezone.utc).replace(tzinfo=None)


def get_matches(conn, start, end):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.kickoff_time
            FROM matches m
            JOIN tours t ON t.id = m.tour_id
            LEFT JOIN settings s ON s.key = 'league_enabled_' || t.league
            WHERE m.featured = true AND m.status = 'scheduled'
              AND m.kickoff_time >= %s AND m.kickoff_time < %s
              AND t.league IN ('UCL', 'UCL2627', 'PL', 'WC')
              AND (s.value IS NULL OR s.value <> '0')
              AND (
                  m.featured_round IS NULL OR m.featured_round = (
                      SELECT min(active.featured_round)
                      FROM matches active JOIN tours at ON at.id = active.tour_id
                      WHERE at.league = t.league AND active.featured = true
                        AND active.status = 'scheduled'
                  )
              )
            ORDER BY m.kickoff_time
        """, (start, end))
        return [row[0] for row in cur.fetchall()]


def resume_and_wait(api_key, service_id, app_url):
    if not service_id.startswith("srv-") or not service_id.replace("-", "").isalnum():
        raise ValueError("RENDER_SERVICE_ID must be a Render web service ID")
    parsed = urlparse(app_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("APP_URL must be an HTTPS site URL without query or fragment")
    service_url = f"https://api.render.com/v1/services/{service_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(service_url, headers=headers, timeout=15)
    response.raise_for_status()
    suspended = response.json().get("suspended")
    if suspended == "suspended":
        response = requests.post(f"{service_url}/resume", headers=headers, timeout=15)
        response.raise_for_status()
        LOG.info("Requested web service resume")
    elif suspended != "not_suspended":
        raise RuntimeError("Render returned an unknown suspension state")

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{app_url.rstrip('/')}/health", timeout=10)
            if response.status_code == 200 and response.json().get("ok") is True:
                return
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.JSONDecodeError):
            pass
        time.sleep(5)
    raise RuntimeError("Web service did not become ready within 10 minutes")


def send_notice(token, chat_id, tomorrow, first_match, app_url):
    kickoff = first_match.replace(tzinfo=timezone.utc).astimezone(MINSK)
    text = (
        f"⚽ Завтра, {tomorrow:%d.%m}, будут матчи!\n"
        f"Первый матч — в {kickoff:%H:%M} по Минску.\n\n"
        "🟢 Сайт доступен, приём прогнозов открыт. Не забудьте сделать прогнозы!"
        f"\n\n👉 {app_url}"
    )
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    response.raise_for_status()
    if response.json().get("ok") is not True:
        raise RuntimeError("Telegram did not confirm delivery")


def run(now=None):
    load_dotenv()
    required = ("DATABASE_URL", "RENDER_API_KEY", "RENDER_SERVICE_ID", "APP_URL",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("Missing environment variables: " + ", ".join(missing))
    tomorrow, start, end = tomorrow_window(now or datetime.now(MINSK))
    schema = "bet_develop" if os.environ.get("APP_ENV") == "sandbox" else "bet"
    with closing(psycopg2.connect(os.environ["DATABASE_URL"])) as conn, conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            # Serialize manual retries/overlapping cron runs for this schema.
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"{schema}:tomorrow-cron",))
            if not cur.fetchone()[0]:
                LOG.info("Another tomorrow cron run is active; skipped")
                return
            cur.execute("SELECT value FROM settings WHERE key = 'tg_tomorrow_sent'")
            last_sent = cur.fetchone()
            if last_sent and last_sent[0] == tomorrow.isoformat():
                LOG.info("Tomorrow reminder already sent; skipped")
                return
        matches = get_matches(conn, start, end)
        if not matches:
            LOG.info("No active featured matches tomorrow; no actions taken")
            return

        resume_and_wait(os.environ["RENDER_API_KEY"], os.environ["RENDER_SERVICE_ID"], os.environ["APP_URL"])
        # Opening is committed before Telegram delivery so the message never advertises a closed site.
        with conn.cursor() as cur:
            for key in ("maintenance_enabled", "betting_locked"):
                cur.execute("""
                    INSERT INTO settings (key, value) VALUES (%s, '0')
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """, (key,))
        conn.commit()
        send_notice(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
                    tomorrow, matches[0], os.environ["APP_URL"])
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO settings (key, value) VALUES ('tg_tomorrow_sent', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """, (tomorrow.isoformat(),))
        LOG.info("Web service ready, predictions opened, tomorrow reminder sent")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        run()
    except (requests.RequestException, psycopg2.Error, RuntimeError, ValueError) as error:
        # HTTP/DB exception strings can contain tokens or connection credentials.
        LOG.error("Tomorrow cron failed (%s). Check configuration and service availability.", type(error).__name__)
        raise SystemExit(1)
