from datetime import datetime, timedelta
from sqlalchemy import func
from ..models import db, Match, Tour, Setting, Commentary, Prediction, User

MINSK_OFFSET = timedelta(hours=3)


def _sv(key, default=None):
    row = Setting.query.get(key)
    return row.value if row else default


def _ss(key, value):
    row = Setting.query.get(key) or Setting(key=key)
    row.value = str(value)
    db.session.merge(row)


def get_auto_settings(league):
    return {
        "enabled":     _sv(f"featured_auto_enabled_{league}", "0"),
        "from_time":   _sv(f"featured_auto_from_{league}",    "10:00"),
        "to_time":     _sv(f"featured_auto_to_{league}",      "03:00"),
        "days":        _sv(f"featured_auto_days_{league}",    "2"),
        "manual_lock": _sv(f"featured_manual_lock_{league}",  "0"),
    }


def save_auto_settings(league, enabled, from_time, to_time, days):
    _ss(f"featured_auto_enabled_{league}", "1" if enabled else "0")
    _ss(f"featured_auto_from_{league}", from_time)
    _ss(f"featured_auto_to_{league}", to_time)
    _ss(f"featured_auto_days_{league}", str(max(1, min(7, int(days)))))
    db.session.commit()


def compute_auto_match_ids(league):
    """Return list of scheduled match IDs matching the saved pattern, or [] if none found."""
    s = get_auto_settings(league)
    from_str = s["from_time"] or "10:00"
    to_str   = s["to_time"]   or "03:00"
    days     = max(1, int(s["days"] or 2))

    now = datetime.utcnow()
    first = (
        Match.query.join(Tour)
        .filter(Tour.league == league, Match.status == "scheduled", Match.kickoff_time >= now)
        .order_by(Match.kickoff_time.asc())
        .first()
    )
    if not first:
        return []

    anchor = first.kickoff_time + MINSK_OFFSET
    ay, am, ad = anchor.year, anchor.month, anchor.day

    from_h, from_m = map(int, from_str.split(":"))
    to_h, to_m     = map(int, to_str.split(":"))

    ids, seen = [], set()
    for i in range(days):
        base      = datetime(ay, am, ad) + timedelta(days=i)
        win_from  = base + timedelta(hours=from_h, minutes=from_m) - MINSK_OFFSET
        win_to    = base + timedelta(hours=to_h,   minutes=to_m)   - MINSK_OFFSET
        if win_to <= win_from:
            win_to += timedelta(days=1)
        hits = (
            Match.query.join(Tour)
            .filter(
                Tour.league == league,
                Match.status == "scheduled",
                Match.kickoff_time >= win_from,
                Match.kickoff_time <= win_to,
            )
            .all()
        )
        for m in hits:
            if m.id not in seen:
                seen.add(m.id)
                ids.append(m.id)
    return ids


def _do_apply(league, app, force=False):
    """Apply saved pattern. Must be called within an active app context.
    force=True bypasses the manual lock (used when admin clicks «Применить сейчас»).
    """
    s = get_auto_settings(league)
    if s["enabled"] != "1":
        return 0

    if not force and s["manual_lock"] == "1":
        # Manual override is active — skip unless all featured scheduled matches are done
        still_open = Match.query.join(Tour).filter(
            Tour.league == league,
            Match.featured == True,
            Match.status == "scheduled",
        ).count()
        if still_open > 0:
            return 0  # manual selection still in play
        # All matches finished — auto-clear the lock
        _ss(f"featured_manual_lock_{league}", "0")
        db.session.commit()

    new_ids = compute_auto_match_ids(league)
    new_set = set(new_ids)

    current = {
        m.id for m in Match.query.join(Tour).filter(
            Tour.league == league, Match.featured == True, Match.status == "scheduled"
        ).all()
    }
    if new_set == current:
        return len(new_ids)  # no change — skip Bender re-run

    max_round = (
        db.session.query(func.max(Match.featured_round))
        .join(Tour)
        .filter(Tour.league == league, Match.featured_round.isnot(None))
        .scalar()
    )
    next_round = (max_round + 1) if max_round else 1

    scheduled = (
        Match.query.join(Tour)
        .filter(Tour.league == league, Match.status == "scheduled")
        .all()
    )
    for m in scheduled:
        m.featured       = m.id in new_set
        m.featured_round = next_round if m.id in new_set else None
    db.session.commit()

    return len(new_ids)


def apply_featured_auto(league, app):
    """Scheduler entry point — wraps in app_context."""
    with app.app_context():
        return _do_apply(league, app)


def run_bender_for_league(app, league, active_only=False):
    """Запустить Бендера для лиги.
    active_only=True — только активный игровой день (минимальный featured_round).
    """
    with app.app_context():
        from ..models import Match, Tour
        matches = (
            Match.query.join(Tour)
            .filter(Tour.league == league, Match.status == "scheduled", Match.featured == True)
            .all()
        )
        if not matches:
            return 0

        if active_only:
            # Берём только матчи активного дня — минимальный featured_round среди scheduled
            rounds = [m.featured_round for m in matches if m.featured_round is not None]
            if rounds:
                min_round = min(rounds)
                matches = [m for m in matches if m.featured_round == min_round]
            else:
                # Матчи без round — все показываем
                matches = [m for m in matches if m.featured_round is None]

        match_data = [
            (
                m.id,
                m.home_team.display_name, m.away_team.display_name,
                f"{league}:{m.home_team.display_name} vs {m.away_team.display_name}",
                m.home_team.name, m.away_team.name,
            )
            for m in matches
        ]
    import threading
    threading.Thread(
        target=_run_bender_and_odds,
        args=(app, league, match_data),
        daemon=True,
    ).start()
    return len(match_data)


def _run_bender_and_odds(app, league, match_data):
    from concurrent.futures import ThreadPoolExecutor
    with app.app_context():
        from .groq_api import generate_bender_pick
        from .odds_api import fetch_odds_for_matches
        from ..seed import BENDER_USERNAME, LEAGUE_TO_TOURNAMENT

        Commentary.query.filter(
            Commentary.match_label.like(f"{league}:%")
        ).delete(synchronize_session=False)
        db.session.commit()

        bender    = User.query.filter_by(username=BENDER_USERNAME).first()
        bender_id = bender.id if bender else None
        tournament = LEAGUE_TO_TOURNAMENT.get(league, league)

        odds_s = Setting.query.get("odds_fetch_enabled")
        if odds_s is None or odds_s.value != "0":
            match_ids = [mid for mid, *_ in match_data]
            already_have_odds = all(
                Match.query.get(mid).odds_home is not None for mid in match_ids
            )
            if already_have_odds:
                print(f"[odds] {league}: коэффициенты уже есть у всех матчей, пропускаем запрос")
                odds_map = {mid: {"home": Match.query.get(mid).odds_home,
                                  "draw": Match.query.get(mid).odds_draw,
                                  "away": Match.query.get(mid).odds_away}
                            for mid in match_ids}
            else:
                print(f"[odds] {league}: запрашиваем коэффициенты для {len(match_ids)} матчей")
                odds_input = [(mid, hen, aen) for mid, _h, _a, _lbl, hen, aen in match_data]
                odds_map = fetch_odds_for_matches(odds_input, league)
        else:
            print(f"[odds] {league}: запросы отключены в настройках (odds_fetch_enabled=0)")
            odds_map = {}

        if odds_map:
            for mid, odds in odds_map.items():
                m = Match.query.get(mid)
                if m:
                    m.odds_home = odds.get("home")
                    m.odds_draw = odds.get("draw")
                    m.odds_away = odds.get("away")
            db.session.commit()

        commentary_s = Setting.query.get("bender_commentary_enabled")
        show_commentary = commentary_s is not None and commentary_s.value == "1"

        def call_groq(item):
            match_id, home, away, label, _hen, _aen = item
            with app.app_context():
                try:
                    result = generate_bender_pick(
                        home, away, tournament=tournament, odds=odds_map.get(match_id),
                    )
                    return (match_id, label, result)
                except Exception as e:
                    print(f"[groq] auto-bender skipped for {label}: {e}")
                    return (match_id, label, None)

        with ThreadPoolExecutor(max_workers=min(len(match_data), 5)) as executor:
            results = list(executor.map(call_groq, match_data))

        for match_id, label, result in results:
            if not result:
                continue
            hs, as_, text = result
            if bender_id:
                pred = Prediction.query.filter_by(user_id=bender_id, match_id=match_id).first()
                if pred:
                    pred.home_score, pred.away_score = hs, as_
                else:
                    db.session.add(Prediction(
                        user_id=bender_id, match_id=match_id,
                        home_score=hs, away_score=as_,
                    ))
            if show_commentary:
                db.session.add(Commentary(match_label=label, text=f"{text} Ставлю {hs}:{as_}."))

        db.session.commit()
