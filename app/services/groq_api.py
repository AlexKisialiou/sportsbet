import os

STANDINGS_LABEL = "__standings__"


def _client():
    from groq import Groq
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))


def generate_bender_pick(home_team, away_team):
    """Returns (home_score, away_score, text)."""
    if not os.environ.get("GROQ_API_KEY"):
        return None

    prompt = (
        f"Матч Лиги Чемпионов УЕФА: {home_team} (хозяева) — {away_team} (гости).\n\n"
        "Ты — профессиональный футбольный аналитик. Напиши на русском языке краткий аналитический прогноз "
        "(3–4 предложения): оцени текущую форму команд, ключевые преимущества и слабые стороны каждой, "
        "тактические особенности матча, обоснуй наиболее вероятный исход и счёт.\n\n"
        "Строго выдай только две строки:\n"
        "АНАЛИЗ: <3-4 предложения аналитики>\n"
        "СЧЁТ: X:Y"
    )

    resp = _client().chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.3-70b-versatile",
    )
    raw = resp.choices[0].message.content.strip()

    text = raw
    home_score, away_score = 1, 0
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("АНАЛИЗ:"):
            text = line.split(":", 1)[1].strip()
        elif line.upper().startswith("СЧЁТ:"):
            parts = line.split(":", 1)[1].strip().split(":")
            if len(parts) == 2:
                try:
                    home_score = int(parts[0].strip())
                    away_score = int(parts[1].strip())
                except ValueError:
                    pass

    return home_score, away_score, text


def generate_bender_standings(standings_text):
    """
    Returns a funny Бендер comment about current standings and last game day results.
    standings_text — pre-formatted string with table and last day data.
    """
    if not os.environ.get("GROQ_API_KEY"):
        return None

    prompt = (
        "Ты — Бендер Родригез из «Футурамы». Вот текущие результаты турнира по ставкам на футбол:\n\n"
        f"{standings_text}\n\n"
        "Напиши на русском языке короткий (3-4 предложения) смешной комментарий в характере Бендера: "
        "кто молодец, кто лузер, что думаешь о расстановке сил и о себе. "
        "Упомяни конкретные имена и цифры. Без markdown."
    )

    resp = _client().chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.1-8b-instant",
    )
    return resp.choices[0].message.content.strip()
