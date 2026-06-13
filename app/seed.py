import os
from werkzeug.security import generate_password_hash
from .models import db, User, PromptHint


BENDER_USERNAME = "bender"

TOURNAMENT_LABELS = {"WC2026": "ЧМ 2026", "UCL": "ЛЧ", "PL": "АПЛ"}

# Maps league codes used in DB (Match.tour.league) to tournament IDs
LEAGUE_TO_TOURNAMENT = {"UCL": "UCL", "PL": "PL", "WC": "WC2026"}

_STANDINGS_PROMPT = (
    "Ты — Бендер Родригез из «Футурамы». Вот текущие результаты турнира по ставкам на футбол:\n\n"
    "{standings_text}\n\n"
    "Напиши на русском языке короткий (3-4 предложения) смешной комментарий в характере Бендера: "
    "кто молодец, кто лузер, что думаешь о расстановке сил и о себе. "
    "Упомяни конкретные имена и цифры. Без markdown."
)

PROMPT_TEMPLATES = {
    "WC2026": {
        "prompt": (
            "Матч Чемпионат Мира 2026 (FIFA World Cup 2026): {home_team} — {away_team}.\n\n"
            "Страны-хозяйки турнира: США, Канада, Мексика — их сборные играют при поддержке "
            "домашних трибун на протяжении всего чемпионата.\n"
            "Климат по городам: жаркий и влажный — Хьюстон, Майами, Монтеррей, Гвадалахара; "
            "умеренный — Нью-Йорк, Лос-Анджелес, Сан-Франциско, Сиэтл, Бостон, Канзас-Сити, "
            "Торонто, Ванкувер; высокогорье — стадион Ацтека, Мехико (~2240 м, разреженный воздух "
            "снижает выносливость игроков).\n\n"
            "Ты — профессиональный футбольный аналитик. Напиши на русском языке краткий "
            "аналитический прогноз (3–4 предложения): оцени текущую форму команд, ключевые "
            "преимущества и слабые стороны каждой, тактические особенности матча, обоснуй "
            "наиболее вероятный исход и счёт. Если одна из команд является страной-хозяйкой "
            "(США, Канада или Мексика) — обязательно упомяни поддержку домашних трибун "
            "как значимый фактор матча.\n\n"
            "Строго выдай только две строки:\n"
            "АНАЛИЗ: <3-4 предложения аналитики>\n"
            "СЧЁТ: X:Y"
        ),
        "standings": _STANDINGS_PROMPT,
    },
    "UCL": {
        "prompt": (
            "Матч Лига Чемпионов УЕФА: {home_team} (хозяева) — {away_team} (гости).\n\n"
            "Ты — профессиональный футбольный аналитик. Напиши на русском языке краткий "
            "аналитический прогноз (3–4 предложения): оцени текущую форму команд, ключевые "
            "преимущества и слабые стороны каждой, тактические особенности матча, обоснуй "
            "наиболее вероятный исход и счёт.\n\n"
            "Строго выдай только две строки:\n"
            "АНАЛИЗ: <3-4 предложения аналитики>\n"
            "СЧЁТ: X:Y"
        ),
        "standings": _STANDINGS_PROMPT,
    },
    "PL": {
        "prompt": (
            "Матч Английская Премьер-лига: {home_team} (хозяева) — {away_team} (гости).\n\n"
            "Ты — профессиональный футбольный аналитик. Напиши на русском языке краткий "
            "аналитический прогноз (3–4 предложения): оцени текущую форму команд, ключевые "
            "преимущества и слабые стороны каждой, тактические особенности матча, обоснуй "
            "наиболее вероятный исход и счёт.\n\n"
            "Строго выдай только две строки:\n"
            "АНАЛИЗ: <3-4 предложения аналитики>\n"
            "СЧЁТ: X:Y"
        ),
        "standings": _STANDINGS_PROMPT,
    },
}


def run():
    if not User.query.filter_by(username=BENDER_USERNAME).first():
        db.session.add(User(
            username=BENDER_USERNAME,
            password_hash="",
            nickname="Бендер",
            is_bot=True,
        ))
        db.session.commit()

    admin_username = os.environ.get("ADMIN_USERNAME", "")
    if admin_username and not User.query.filter_by(username=admin_username).first():
        db.session.add(User(
            username=admin_username,
            password_hash=generate_password_hash(os.environ.get("ADMIN_PASSWORD", "")),
            nickname=os.environ.get("ADMIN_NICKNAME"),
            is_admin=True,
            is_superuser=True,
        ))
        db.session.commit()


def seed_prompt_hints():
    # Remove legacy hint-style records (general/weather)
    PromptHint.query.filter(
        PromptHint.hint_type.notin_(["prompt", "standings"])
    ).delete(synchronize_session=False)
    db.session.commit()

    for tournament, templates in PROMPT_TEMPLATES.items():
        for hint_type, content in templates.items():
            existing = PromptHint.query.filter_by(
                tournament=tournament, hint_type=hint_type
            ).first()
            if not existing:
                db.session.add(PromptHint(
                    tournament=tournament,
                    hint_type=hint_type,
                    content=content,
                    active=True,
                    sort_order=0 if hint_type == "prompt" else 1,
                ))
    db.session.commit()
