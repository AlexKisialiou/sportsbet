from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os
from html import escape

import requests

from ..models import Match, Prediction, Tour

MINSK = timezone(timedelta(hours=3))


def build_day_results(league):
    from .points import get_leaderboard

    labels = {"UCL": "Лига чемпионов", "UCL2627": "ЛЧ 26/27", "PL": "АПЛ", "WC": "ЧМ 2026"}
    matches = Match.query.join(Tour).filter(
        Tour.league == league, Match.featured == True, Match.featured_round > 0,
    ).all()
    rounds = defaultdict(list)
    for match in matches:
        rounds[match.featured_round].append(match)
    completed = [
        rnd for rnd, group in rounds.items()
        if all(match.status == "finished" and match.score is not None for match in group)
    ]
    if not completed:
        return f"<b>🏆 {escape(labels[league])}</b>\nПока нет завершённых игровых дней."
    day = max(completed)
    leaderboard = get_leaderboard(last_rounds=[day], league=league)
    previous = sorted(leaderboard, key=lambda row: -(row["total"] - row["days"][0]["pts"]))
    previous_ranks = {row["user"].id: i for i, row in enumerate(previous, 1)}
    table = [f"{'№':>2}   {'Участник':<11} {'Всего':>5} {'+День':>5}"]
    for rank, row in enumerate(leaderboard, 1):
        old_rank = previous_ranks[row["user"].id]
        arrow = "↑" if rank < old_rank else ("↓" if rank > old_rank else "→")
        name = row["user"].display_name.replace("\n", " ").replace("\r", " ")
        if len(name) > 11:
            name = name[:10] + "…"
        table.append(f"{rank:>2} {arrow} {name:<11} {row['total']:>5} {row['days'][0]['pts']:>+5}")
    best = max((row["days"][0]["pts"] for row in leaderboard), default=0)
    winners = ", ".join(
        row["user"].display_name for row in leaderboard if row["days"][0]["pts"] == best
    )
    winner_text = (
        f"👑 Лидер игрового дня: {escape(winners)} — {best} оч."
        if best > 0 else "За этот игровой день никто не набрал очков."
    )
    return (
        f"<b>🏆 Итоги последнего игрового дня</b>\n{escape(labels[league])} · День {day}\n\n"
        f"<pre>{escape(chr(10).join(table))}</pre>\n"
        f"Всего — общие очки.\n+День — очки за последний завершённый игровой день ({day}).\n\n"
        f"↑ поднялся · ↓ опустился · → без изменений\n\n{winner_text}"
    )


def send_maintenance_notification(enabled, *, league=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы")
    text = (
        "🔴 Сайт временно недоступен. Идут технические работы. Сообщим, когда можно будет делать прогнозы."
        if enabled else
        "🟢 Сайт снова доступен — можно делать прогнозы!"
    )
    if os.environ.get("APP_ENV") == "sandbox":
        text = f"[SANDBOX] {text}"
    if os.environ.get("APP_URL"):
        text += f"\n\n👉 {os.environ['APP_URL']}"
    messages = [escape(text)]
    if enabled and league:
        messages.append(build_day_results(league))
    for message in messages:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        response.raise_for_status()
        if not response.json().get("ok"):
            raise RuntimeError("Telegram не подтвердил доставку сообщения")


def _match_word(count: int) -> str:
    remainder_100 = count % 100
    if 11 <= remainder_100 <= 14:
        return "матчей"
    remainder_10 = count % 10
    if remainder_10 == 1:
        return "матч"
    if 2 <= remainder_10 <= 4:
        return "матча"
    return "матчей"


def _russian_count(count: int, forms: tuple[str, str, str]) -> str:
    remainder_100 = count % 100
    if 11 <= remainder_100 <= 14:
        word = forms[2]
    elif count % 10 == 1:
        word = forms[0]
    elif 2 <= count % 10 <= 4:
        word = forms[1]
    else:
        word = forms[2]
    return f"{count} {word}"


def format_time_until(kickoff_time, now=None) -> str:
    if kickoff_time.tzinfo is None:
        kickoff_time = kickoff_time.replace(tzinfo=timezone.utc)
    kickoff_minsk = kickoff_time.astimezone(MINSK)
    now = now or datetime.now(MINSK)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MINSK)

    minutes_left = max(0, int((kickoff_minsk - now).total_seconds() + 59) // 60)
    hours, minutes = divmod(minutes_left, 60)
    if hours and minutes:
        remaining = (
            f"{_russian_count(hours, ('час', 'часа', 'часов'))} "
            f"{_russian_count(minutes, ('минута', 'минуты', 'минут'))}"
        )
    elif hours:
        remaining = _russian_count(hours, ("час", "часа", "часов"))
    elif minutes:
        remaining = _russian_count(minutes, ("минута", "минуты", "минут"))
    else:
        remaining = "менее минуты"
    return f"⏳ До первого матча осталось: {remaining}"


def build_bet_reminder(matches, users, app_url="", now=None):
    """Return a Russian summary of users missing predictions, or None if all have bet."""
    match_ids = [match.id for match in matches]
    if not match_ids:
        return None

    predictions = Prediction.query.filter(Prediction.match_id.in_(match_ids)).all()
    users_by_id = {user.id: user for user in users}
    placed_by_match = defaultdict(set)
    for prediction in predictions:
        placed_by_match[prediction.match_id].add(prediction.user_id)

    missing_counts = defaultdict(int)
    for match in matches:
        for user_id in users_by_id:
            if user_id not in placed_by_match[match.id]:
                missing_counts[user_id] += 1

    if not missing_counts:
        return None

    first_match = min(matches, key=lambda match: match.kickoff_time)
    lines = [
        "🔔 На сегодняшние матчи не все успели сделать прогнозы:",
        format_time_until(first_match.kickoff_time, now=now),
        "",
    ]
    for user_id, count in sorted(
        missing_counts.items(),
        key=lambda item: users_by_id[item[0]].display_name.casefold(),
    ):
        name = users_by_id[user_id].display_name
        lines.append(f"{name} — не хватает прогнозов на {count} {_match_word(count)}")

    if app_url:
        lines.append(f"\n👉 Сделать прогноз: {app_url}")

    return "\n".join(lines)
