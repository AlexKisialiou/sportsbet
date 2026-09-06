"""
Telegram bet reminder job.
Run every 30 minutes via Render cron: */30 * * * *

Finds featured matches starting in 30-60 minutes and sends a reminder.
The 30-minute detection window aligns with the 30-minute cron interval,
so each match triggers exactly one notification.
"""

import os
from datetime import datetime, timedelta, timezone

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DATABASE_URL = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
APP_URL = os.environ.get("APP_URL", "")

MINSK = timezone(timedelta(hours=3))
SCHEMA = "bet"
WINDOW_FROM = timedelta(minutes=30)
WINDOW_TO = timedelta(minutes=60)


def get_db():
    url = DATABASE_URL.replace("postgresql://", "postgres://", 1)
    return psycopg2.connect(url, options=f"-csearch_path={SCHEMA}")


def is_betting_locked(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM setting WHERE key = 'betting_locked'")
        row = cur.fetchone()
        return bool(row and row[0] == "1")


def get_upcoming_matches(conn) -> list[dict]:
    now = datetime.now(timezone.utc)
    window_start = now + WINDOW_FROM
    window_end = now + WINDOW_TO

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.kickoff_time, ht.name_ru, at.name_ru, ht.name, at.name
            FROM match m
            JOIN team ht ON m.home_team_id = ht.id
            JOIN team at ON m.away_team_id = at.id
            WHERE m.featured = true
              AND m.status = 'scheduled'
              AND m.kickoff_time >= %s
              AND m.kickoff_time < %s
            ORDER BY m.kickoff_time
            """,
            (window_start, window_end),
        )
        rows = cur.fetchall()

    result = []
    for kickoff_utc, home_ru, away_ru, home_en, away_en in rows:
        kickoff_utc = kickoff_utc.replace(tzinfo=timezone.utc)
        kickoff_minsk = kickoff_utc.astimezone(MINSK)
        result.append({
            "time": kickoff_minsk.strftime("%H:%M"),
            "home": home_ru or home_en,
            "away": away_ru or away_en,
        })
    return result


def build_message(matches: list[dict]) -> str:
    count = len(matches)
    header = "⚽ Через час матч!" if count == 1 else f"⚽ Через час {count} матча!"
    lines = [header, "Успей поставить!\n"]
    for m in matches:
        lines.append(f"🕐 {m['time']} — {m['home']} vs {m['away']}")
    if APP_URL:
        lines.append(f"\n👉 {APP_URL}")
    return "\n".join(lines)


def send_message(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
    resp.raise_for_status()
    print(f"Sent: {resp.json()['result']['message_id']}")


if __name__ == "__main__":
    conn = get_db()
    try:
        if is_betting_locked(conn):
            print("Betting locked, skipping.")
            raise SystemExit(0)

        matches = get_upcoming_matches(conn)
        if not matches:
            print(f"No matches in window +{WINDOW_FROM.seconds//60}–{WINDOW_TO.seconds//60} min, skipping.")
            raise SystemExit(0)

        msg = build_message(matches)
        print(msg)
        send_message(msg)
    finally:
        conn.close()
