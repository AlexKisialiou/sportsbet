import os

STANDINGS_LABEL_UCL = "__standings_ucl__"
STANDINGS_LABEL_UCL2627 = "__standings_ucl2627__"
STANDINGS_LABEL_PL = "__standings_pl__"
STANDINGS_LABEL_WC = "__standings_wc__"


def _client():
    from groq import Groq
    return Groq(api_key=os.environ.get("GROQ_API_KEY"))


def _load_prompt(tournament, hint_type):
    """Load prompt template from DB. Returns None if not found or inactive."""
    try:
        from ..models import PromptHint
        hint = PromptHint.query.filter_by(
            tournament=tournament, hint_type=hint_type, active=True
        ).first()
        return hint.content if hint else None
    except Exception as e:
        print(f"[bender] _load_prompt({tournament}, {hint_type}) error: {e}")
        return None


def _odds_context(home_team, away_team, odds):
    """Build a Russian odds context string to append to Bender's prompt."""
    if not odds:
        return ""
    h, d, a = odds.get("home"), odds.get("draw"), odds.get("away")
    if not (h and a):
        return ""
    if d:
        return (
            f"\nКоэффициенты букмекеров: победа {home_team} — {h:.2f}, "
            f"ничья — {d:.2f}, победа {away_team} — {a:.2f}. "
            "Учти эти данные при составлении прогноза."
        )
    return (
        f"\nКоэффициенты букмекеров: победа {home_team} — {h:.2f}, "
        f"победа {away_team} — {a:.2f}. "
        "Учти эти данные при составлении прогноза."
    )


def generate_bender_pick(home_team, away_team, tournament="UCL", odds=None):
    """Returns (home_score, away_score, text)."""
    if not os.environ.get("GROQ_API_KEY"):
        return None

    ctx = _odds_context(home_team, away_team, odds)

    template = _load_prompt(tournament, "prompt")
    if template:
        if "{odds_context}" in template:
            prompt = template.format(home_team=home_team, away_team=away_team, odds_context=ctx)
        else:
            prompt = template.format(home_team=home_team, away_team=away_team) + ctx
        print(f"[bender] {tournament} промпт из БД: {home_team} vs {away_team}" + (" +odds" if ctx else ""))
        try:
            from .activity import log_action
            log_action(None, "prompt_hint_applied",
                       f"{tournament} промпт → {home_team} vs {away_team}")
        except Exception:
            pass
    else:
        print(f"[bender] {tournament}: промпт не найден в БД, используется fallback" + (" +odds" if ctx else ""))
        prompt = (
            f"Матч {tournament}: {home_team} — {away_team}."
            + ctx + "\n\n"
            "Ты — профессиональный футбольный аналитик. Напиши на русском языке краткий "
            "аналитический прогноз (3–4 предложения): оцени форму команд, преимущества "
            "и слабые стороны, тактику, обоснуй исход и счёт.\n\n"
            "Строго выдай только две строки:\n"
            "АНАЛИЗ: <3-4 предложения аналитики>\n"
            "СЧЁТ: X:Y"
        )

    resp = _client().chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="qwen/qwen3.8-27b",
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


def translate_team_names(team_names):
    """Returns {english_name: russian_name} for a list of club/national team names."""
    if not os.environ.get("GROQ_API_KEY") or not team_names:
        return {}

    numbered = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(team_names))
    prompt = (
        "Переведи или транслитерируй на русский язык названия следующих футбольных клубов и национальных сборных.\n"
        "Используй общепринятые русские названия (Arsenal FC → Арсенал, Brazil → Бразилия, France → Франция).\n"
        "Ответ строго в формате — одна строка на команду: НОМЕР|РУССКОЕ_НАЗВАНИЕ\n"
        "Пример: 1|Арсенал\n"
        "Без лишнего текста, только строки в указанном формате.\n\n"
        f"{numbered}"
    )

    resp = _client().chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="qwen/qwen3.6-27b",
    )
    raw = resp.choices[0].message.content.strip()

    result = {}
    for line in raw.splitlines():
        line = line.strip()
        if "|" in line:
            idx_str, ru = line.split("|", 1)
            ru = ru.strip()
            try:
                idx = int(idx_str.strip()) - 1
                if 0 <= idx < len(team_names) and ru:
                    result[team_names[idx]] = ru
            except ValueError:
                pass
    return result


def generate_bender_standings(standings_text, tournament="UCL"):
    """Returns a Бендер comment about current standings."""
    if not os.environ.get("GROQ_API_KEY"):
        return None

    template = _load_prompt(tournament, "standings")
    if template:
        prompt = template.format(standings_text=standings_text)
    else:
        prompt = (
            "Ты — Бендер Родригез из «Футурамы». Вот текущие результаты турнира по ставкам на футбол:\n\n"
            f"{standings_text}\n\n"
            "Напиши на русском языке короткий (3-4 предложения) смешной комментарий в характере Бендера: "
            "кто молодец, кто лузер, что думаешь о расстановке сил и о себе. "
            "Упомяни конкретные имена и цифры. Без markdown."
        )

    resp = _client().chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="qwen/qwen3.6-27b",
    )
    return resp.choices[0].message.content.strip()
