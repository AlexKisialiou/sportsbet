import threading
from collections import defaultdict


def maybe_generate_standings(league, app):
    """
    After a fetch: check if all featured matches for the latest game round are finished.
    If yes and not yet processed, generate Bender standings commentary in background.
    """
    def _run():
        with app.app_context():
            from ..models import Match, Tour, Setting, Commentary, db
            from .points import get_leaderboard
            from .groq_api import (generate_bender_standings,
                                   STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL, STANDINGS_LABEL_WC)
            from ..seed import LEAGUE_TO_TOURNAMENT

            featured = (Match.query.join(Tour)
                        .filter(Tour.league == league, Match.featured == True)
                        .all())
            if not featured:
                return

            # Group by featured_round; skip matches without a round assigned
            round_matches = defaultdict(list)
            for m in featured:
                if m.featured_round is not None:
                    round_matches[m.featured_round].append(m)

            if not round_matches:
                return

            # Latest round where ALL matches are finished
            complete_rounds = sorted(
                [rnd for rnd, matches in round_matches.items()
                 if all(m.status == 'finished' for m in matches)],
                reverse=True
            )
            if not complete_rounds:
                return

            latest_round = complete_rounds[0]
            setting_key = f"standings_day_{league.lower()}"
            s = Setting.query.get(setting_key)
            if s and s.value == str(latest_round):
                return  # already generated for this round

            if not s:
                s = Setting(key=setting_key)
            s.value = str(latest_round)
            db.session.add(s)
            db.session.commit()

            league_names = {"UCL": "ЛЧ", "PL": "АПЛ", "WC": "ЧМ"}
            label_keys = {"UCL": STANDINGS_LABEL_UCL, "PL": STANDINGS_LABEL_PL, "WC": STANDINGS_LABEL_WC}
            league_name = league_names.get(league, league)
            label_key = label_keys.get(league, f"__standings_{league.lower()}__")

            lb = get_leaderboard(league=league)
            lines = [f"Турнирная таблица ({league_name}):"]
            for i, row in enumerate(lb, 1):
                if not row["user"].is_bot:
                    lines.append(f"  {i}. {row['user'].display_name} — {row['total']} очков")

            lb_round = get_leaderboard(last_rounds=[latest_round], league=league)
            lines.append(f"\nИгровой день {latest_round}:")
            for row in lb_round:
                if row["user"].is_bot:
                    continue
                d = row["days"][0] if row["days"] else {"pts": 0, "has_pred": False}
                if d["has_pred"]:
                    lines.append(f"  {row['user'].display_name}: +{d['pts']}")
                else:
                    lines.append(f"  {row['user'].display_name}: не ставил")

            try:
                text = generate_bender_standings("\n".join(lines),
                                                tournament=LEAGUE_TO_TOURNAMENT.get(league, league))
                if text:
                    Commentary.query.filter_by(match_label=label_key).delete()
                    db.session.add(Commentary(match_label=label_key, text=text))
                    db.session.commit()
                    print(f"[standings] generated for {league} round {latest_round}")
            except Exception as e:
                print(f"[standings] generation failed for {league}: {e}")

    threading.Thread(target=_run, daemon=True).start()
