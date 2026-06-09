import threading
from collections import defaultdict
from datetime import timedelta, date as date_type
from sqlalchemy import func as sqlfunc


def maybe_generate_standings(league, app):
    """
    After a fetch: check if all featured matches for the latest game day are finished.
    If yes and not yet processed, generate Bender standings commentary in background.
    """
    def _run():
        with app.app_context():
            from ..models import Match, Tour, Setting, Commentary, db
            from .points import get_leaderboard
            from .groq_api import (generate_bender_standings,
                                   STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL, STANDINGS_LABEL_WC)

            featured = (Match.query.join(Tour)
                        .filter(Tour.league == league, Match.featured == True)
                        .all())
            if not featured:
                return

            # Group by Minsk date (UTC+3)
            day_matches = defaultdict(list)
            for m in featured:
                if m.kickoff_time:
                    day = (m.kickoff_time + timedelta(hours=3)).date()
                    day_matches[day].append(m)

            if not day_matches:
                return

            # Latest day where ALL featured matches are finished
            complete_days = sorted(
                [day for day, matches in day_matches.items()
                 if all(m.status == 'finished' for m in matches)],
                reverse=True
            )
            if not complete_days:
                return

            latest_str = complete_days[0].isoformat()
            setting_key = f"standings_day_{league.lower()}"
            s = Setting.query.get(setting_key)
            if s and s.value == latest_str:
                return  # already generated for this game day

            # Mark as processed before generating (avoid duplicate runs)
            if not s:
                s = Setting(key=setting_key)
            s.value = latest_str
            db.session.add(s)
            db.session.commit()

            # Build standings text
            league_names = {"UCL": "ЛЧ", "PL": "АПЛ", "WC": "ЧМ"}
            label_keys = {"UCL": STANDINGS_LABEL_UCL, "PL": STANDINGS_LABEL_PL, "WC": STANDINGS_LABEL_WC}
            league_name = league_names.get(league, league)
            label_key = label_keys.get(league, f"__standings_{league.lower()}__")

            lb = get_leaderboard(league=league)
            lines = [f"Турнирная таблица ({league_name}):"]
            for i, row in enumerate(lb, 1):
                if not row["user"].is_bot:
                    lines.append(f"  {i}. {row['user'].display_name} — {row['total']} очков")

            # Last game day breakdown (UTC date from DB)
            day_row = (
                db.session.query(sqlfunc.date(Match.kickoff_time))
                .join(Tour, Match.tour_id == Tour.id)
                .filter(Match.status == "finished", Tour.league == league,
                        Match.featured == True)
                .group_by(sqlfunc.date(Match.kickoff_time))
                .order_by(sqlfunc.date(Match.kickoff_time).desc())
                .first()
            )
            if day_row:
                last_day = day_row[0]
                lb_day = get_leaderboard(
                    last_days=[date_type.fromisoformat(last_day)
                               if isinstance(last_day, str) else last_day],
                    league=league
                )
                lines.append(f"\nПоследний игровой день:")
                for row in lb_day:
                    if row["user"].is_bot:
                        continue
                    d = row["days"][0] if row["days"] else {"pts": 0, "has_pred": False}
                    if d["has_pred"]:
                        lines.append(f"  {row['user'].display_name}: +{d['pts']}")
                    else:
                        lines.append(f"  {row['user'].display_name}: не ставил")

            try:
                text = generate_bender_standings("\n".join(lines))
                if text:
                    Commentary.query.filter_by(match_label=label_key).delete()
                    db.session.add(Commentary(match_label=label_key, text=text))
                    db.session.commit()
                    print(f"[standings] generated for {league} game day {latest_str}")
            except Exception as e:
                print(f"[standings] generation failed for {league}: {e}")

    threading.Thread(target=_run, daemon=True).start()
