from collections import defaultdict
from datetime import date as date_type, datetime, timedelta
from flask import render_template, request
from sqlalchemy import func, case as sa_case
from ..models import db, Match, Tour, Prediction, PredictionPoints, User, Commentary, ActivityLog, Setting, ReleaseNote, Score, Team, MatchComment, CommentRead
from ..services.points import get_leaderboard
from ..services.activity import ACTION_LABELS
from ..services.groq_api import STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL, STANDINGS_LABEL_WC
from ..auth import get_current_user, login_required, admin_required, superuser_required

from flask import Blueprint
main_bp = Blueprint("main", __name__)


def _score_display(sc):
    if not sc or sc.home_score is None:
        return "—"
    s = f"{sc.home_score}:{sc.away_score}"
    if sc.win_type == "aet":
        if sc.extra_time_home is not None:
            s += f" (д.в. {sc.extra_time_home}:{sc.extra_time_away})"
        else:
            s += " (д.в.)"
    elif sc.win_type == "pen":
        if sc.extra_time_home is not None and (sc.extra_time_home > 0 or sc.extra_time_away > 0):
            s += f" (д.в. {sc.home_score + sc.extra_time_home}:{sc.away_score + sc.extra_time_away})"
        if sc.penalties_home is not None:
            s += f" (пен. {sc.penalties_home}:{sc.penalties_away})"
    return s


def _get_league_config():
    order_s = Setting.query.get("league_order")
    order = order_s.value.split(",") if order_s and order_s.value else ["UCL", "PL", "WC"]
    order = [lg for lg in order if lg in ("UCL", "PL", "WC")]
    for lg in ("UCL", "PL", "WC"):
        if lg not in order:
            order.append(lg)
    enabled = {}
    for lg in ("UCL", "PL", "WC"):
        s = Setting.query.get(f"league_enabled_{lg}")
        enabled[lg] = s is None or s.value != "0"
    return order, enabled


def _build_team_form_data(league, team_ids, limit):
    team_ids = set(team_ids)
    if not team_ids:
        return {}
    matches = (
        Match.query.join(Tour)
        .filter(
            Tour.league == league,
            Match.status == "finished",
            db.or_(Match.home_team_id.in_(team_ids), Match.away_team_id.in_(team_ids))
        )
        .order_by(Match.kickoff_time.desc())
        .all()
    )
    match_ids = [m.id for m in matches]
    scores = {s.match_id: s for s in Score.query.filter(Score.match_id.in_(match_ids)).all()} if match_ids else {}
    team_lists = {tid: [] for tid in team_ids}
    for m in matches:
        score = scores.get(m.id)
        if not score:
            continue
        for tid, is_home in ((m.home_team_id, True), (m.away_team_id, False)):
            if tid not in team_ids:
                continue
            lst = team_lists[tid]
            if limit > 0 and len(lst) >= limit:
                continue
            opponent = m.away_team if is_home else m.home_team
            tg = score.home_score if is_home else score.away_score
            og = score.away_score if is_home else score.home_score
            res = "W" if tg > og else ("D" if tg == og else "L")
            dt = (m.kickoff_time + timedelta(hours=3)).strftime("%d.%m") if m.kickoff_time else "—"
            score_str = f"{tg}:{og}"
            if score.win_type == "aet":
                if score.extra_time_home is not None:
                    et_mine = score.extra_time_home if is_home else score.extra_time_away
                    et_theirs = score.extra_time_away if is_home else score.extra_time_home
                    score_str += f" (д.в. {et_mine}:{et_theirs})"
                else:
                    score_str += " (д.в.)"
            elif score.win_type == "pen":
                if score.extra_time_home is not None and (score.extra_time_home > 0 or score.extra_time_away > 0):
                    et_mine = score.extra_time_home if is_home else score.extra_time_away
                    et_theirs = score.extra_time_away if is_home else score.extra_time_home
                    score_str += f" (д.в. {tg + et_mine}:{og + et_theirs})"
                if score.penalties_home is not None:
                    ph = score.penalties_home if is_home else score.penalties_away
                    pa = score.penalties_away if is_home else score.penalties_home
                    score_str += f" (пен. {ph}:{pa})"
            lst.append({
                "date": dt,
                "opponent": opponent.display_name,
                "opponent_crest": opponent.crest or "",
                "score": score_str,
                "is_home": is_home,
                "result": res,
            })
    teams_map = {t.id: t for t in Team.query.filter(Team.id.in_(team_ids)).all()}
    result = {}
    for tid in team_ids:
        team = teams_map.get(tid)
        if not team:
            continue
        result[f"{tid}_{league}"] = {
            "team_name": team.display_name,
            "team_crest": team.crest or "",
            "matches": team_lists[tid],
        }
    return result



@main_bp.route("/")
@login_required
def index():
    user = get_current_user()
    all_users = User.query.order_by(User.username).all()

    predictions = {}
    if user:
        for p in Prediction.query.filter_by(user_id=user.id).all():
            predictions[p.match_id] = p

    def _get_pred_limit(league):
        s = Setting.query.get(f"pred_days_{league}")
        try:
            return max(1, min(int(s.value), 20)) if s else 4
        except (ValueError, TypeError):
            return 4

    def _build(league):
        pred_rounds = [
            r[0] for r in (
                db.session.query(Match.featured_round)
                .join(Tour)
                .filter(
                    Tour.league == league,
                    Match.status == "finished",
                    Match.featured == True,
                    Match.featured_round.isnot(None),
                    Match.featured_round != 0,
                )
                .group_by(Match.featured_round)
                .order_by(Match.featured_round.desc())
                .limit(_get_pred_limit(league))
                .all()
            )
        ]
        if len(pred_rounds) < _get_pred_limit(league):
            r0 = db.session.query(Match.id).join(Tour).filter(
                Tour.league == league,
                Match.status == "finished",
                Match.featured == True,
                Match.featured_round == 0,
            ).first()
            if r0:
                pred_rounds.append(0)

        leaderboard = get_leaderboard(last_rounds=pred_rounds, league=league)

        # Streak = consecutive featured_rounds (descending) where user got at least one exact/winner.
        # Round without any hit (or no prediction at all) breaks the streak.
        _all_streak_rounds = [
            r[0] for r in (
                db.session.query(Match.featured_round)
                .join(Tour)
                .filter(
                    Tour.league == league,
                    Match.status == 'finished',
                    Match.featured == True,
                    Match.featured_round.isnot(None),
                    Match.featured_round != 0,
                )
                .group_by(Match.featured_round)
                .order_by(Match.featured_round.desc())
                .all()
            )
        ]
        _streak_q = (
            db.session.query(Prediction.user_id, Match.featured_round, PredictionPoints.reason)
            .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
            .join(Match, Prediction.match_id == Match.id)
            .join(Tour, Match.tour_id == Tour.id)
            .filter(
                Match.status == 'finished', Match.featured == True,
                Match.featured_round.isnot(None), Match.featured_round != 0,
                Tour.league == league,
            )
            .all()
        )
        _pred_by_round = defaultdict(lambda: defaultdict(list))
        for _r in _streak_q:
            _pred_by_round[_r.user_id][_r.featured_round].append(_r.reason)
        _user_streaks = {}
        for _uid, _rounds_map in _pred_by_round.items():
            _s = 0
            for _rnd in _all_streak_rounds:
                if any(r == 'exact' for r in _rounds_map.get(_rnd, [])):
                    _s += 1
                else:
                    break
            _user_streaks[_uid] = _s
        for row in leaderboard:
            row['streak'] = _user_streaks.get(row['user'].id, 0)

        # Accuracy: (exact + winner) / total featured finished predictions
        _acc_q = (
            db.session.query(Prediction.user_id, PredictionPoints.reason)
            .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
            .join(Match, Prediction.match_id == Match.id)
            .join(Tour, Match.tour_id == Tour.id)
            .filter(Tour.league == league, Match.featured == True, Match.status == 'finished')
            .all()
        )
        _acc_raw = {}
        for _r in _acc_q:
            if _r.user_id not in _acc_raw:
                _acc_raw[_r.user_id] = [0, 0]
            _acc_raw[_r.user_id][1] += 1
            if _r.reason in ('exact', 'winner'):
                _acc_raw[_r.user_id][0] += 1
        for row in leaderboard:
            _hits, _tot = _acc_raw.get(row['user'].id, [0, 0])
            row['accuracy_pct'] = round(100 * _hits / _tot) if _tot > 0 else 0

        _rw_rounds = [r for r in pred_rounds if r != 0]
        _round_winners = {}
        if _rw_rounds:
            _rw_q = (
                db.session.query(
                    Prediction.user_id,
                    Match.featured_round,
                    func.coalesce(func.sum(PredictionPoints.points), 0).label('pts')
                )
                .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
                .join(Match, Prediction.match_id == Match.id)
                .join(Tour, Match.tour_id == Tour.id)
                .filter(
                    Tour.league == league,
                    Match.featured == True,
                    Match.featured_round.in_(_rw_rounds),
                )
                .group_by(Prediction.user_id, Match.featured_round)
                .all()
            )
            _user_map = {row['user'].id: row['user'] for row in leaderboard}
            _rw_by_round = defaultdict(list)
            for _r in _rw_q:
                _rw_by_round[_r.featured_round].append((_r.user_id, int(_r.pts)))
            for _rnd, _entries in _rw_by_round.items():
                _max_pts = max(e[1] for e in _entries)
                if _max_pts == 0:
                    continue
                _winners = [_user_map[_uid] for _uid, _pts in _entries if _pts == _max_pts and _uid in _user_map]
                if _winners:
                    _round_winners[_rnd] = {'users': _winners, 'pts': _max_pts}

        _latest_round = next((r for r in pred_rounds if r != 0), None)
        _latest_winner_ids = set()
        if _latest_round is not None and _round_winners.get(_latest_round):
            _latest_winner_ids = {u.id for u in _round_winners[_latest_round]['users']}
        for row in leaderboard:
            row['is_latest_round_winner'] = row['user'].id in _latest_winner_ids

        _months_short = ["", "янв", "фев", "мар", "апр", "май", "июн",
                         "июл", "авг", "сен", "окт", "ноя", "дек"]
        _months_long  = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
                         "июля", "августа", "сентября", "октября", "ноября", "декабря"]

        def _fmt_range(min_d, max_d, short=False):
            m = _months_short if short else _months_long
            if min_d == max_d:
                return f"{min_d.day} {m[min_d.month]}"
            if min_d.month == max_d.month:
                return f"{min_d.day}–{max_d.day} {m[max_d.month]}"
            return f"{min_d.day} {m[min_d.month]} – {max_d.day} {m[max_d.month]}"

        round_date_labels = {}   # {round: "12–14 июня"}
        round_date_short  = {}   # {round: "12–14 июн"}
        date_rows = (
            db.session.query(
                Match.featured_round,
                func.min(Match.kickoff_time),
                func.max(Match.kickoff_time),
            )
            .join(Tour)
            .filter(Tour.league == league, Match.featured == True, Match.featured_round.isnot(None))
            .group_by(Match.featured_round)
            .all()
        )
        for rnd, min_dt, max_dt in date_rows:
            if min_dt and max_dt:
                min_d = (min_dt + timedelta(hours=3)).date()
                max_d = (max_dt + timedelta(hours=3)).date()
                round_date_labels[rnd] = _fmt_range(min_d, max_d, short=False)
                round_date_short[rnd]  = _fmt_range(min_d, max_d, short=True)

        scheduled_all = (
            Match.query
            .join(Tour)
            .filter(Tour.league == league, Match.status == "scheduled", Match.featured == True)
            .order_by(Match.kickoff_time.asc())
            .all()
        )

        # Show only the earliest (lowest featured_round) active game day.
        # This way, after Day 1 finishes, Day 2 appears immediately — regardless of its calendar date.
        _active_rounds = {m.featured_round for m in scheduled_all if m.featured_round is not None}
        _min_round = min(_active_rounds) if _active_rounds else None
        scheduled = [
            m for m in scheduled_all
            if m.featured_round is None or m.featured_round == _min_round
        ]

        finished_recent = []
        pred_map = {}
        comment_map = {}
        unread_map = {}
        reads_map = {}
        if pred_rounds:
            finished_recent = (
                Match.query.join(Tour)
                .filter(
                    Tour.league == league,
                    Match.status == "finished",
                    Match.featured == True,
                    Match.featured_round.in_(pred_rounds),
                )
                .order_by(Match.featured_round.desc(), Match.kickoff_time.desc())
                .limit(30)
                .all()
            )
            match_ids = [m.id for m in finished_recent]
            if match_ids:
                for p in Prediction.query.filter(Prediction.match_id.in_(match_ids)).all():
                    pred_map[(p.match_id, p.user_id)] = p
                for c in MatchComment.query.filter(MatchComment.match_id.in_(match_ids)).order_by(MatchComment.created_at.asc()).all():
                    comment_map.setdefault(c.match_id, []).append(c)
                cu = user
                if cu:
                    reads_map = {r.match_id: r.last_read_comment_id
                                 for r in CommentRead.query.filter(
                                     CommentRead.user_id == cu.id,
                                     CommentRead.match_id.in_(match_ids)
                                 ).all()}
                    for mid, cmts in comment_map.items():
                        last_read = reads_map.get(mid, 0)
                        n = sum(1 for c in cmts if c.id > last_read and c.user_id != cu.id)
                        if n:
                            unread_map[mid] = n

        live_matches = (
            Match.query.join(Tour)
            .filter(Tour.league == league, Match.status == "live", Match.featured == True)
            .order_by(Match.kickoff_time.asc())
            .all()
        )
        live_pred_map = {}
        if live_matches:
            live_ids = [m.id for m in live_matches]
            for p in Prediction.query.filter(Prediction.match_id.in_(live_ids)).all():
                live_pred_map[(p.match_id, p.user_id)] = p

        scheduled_total = len(scheduled)
        unfilled_count = sum(1 for m in scheduled if m.id not in predictions)

        all_sched_ids = [m.id for m in scheduled]
        all_preds_sched = set()
        if all_sched_ids:
            for p in Prediction.query.filter(Prediction.match_id.in_(all_sched_ids)).all():
                all_preds_sched.add((p.match_id, p.user_id))

        user_fill_status = []
        for lb_row in leaderboard:
            u = lb_row["user"]
            filled = sum(1 for m in scheduled if (m.id, u.id) in all_preds_sched)
            user_fill_status.append({"user": u, "filled": filled, "total": scheduled_total})

        non_bot_user_ids = [row["user"].id for row in leaderboard if not row["user"].is_bot]
        non_bot_total = len(non_bot_user_ids)
        match_fill_counts = {
            m.id: sum(1 for uid in non_bot_user_ids if (m.id, uid) in all_preds_sched)
            for m in scheduled
        }

        return {
            "leaderboard": leaderboard,
            "pred_rounds": pred_rounds,
            "round_winners": _round_winners,
            "round_date_labels": round_date_labels,
            "round_date_short": round_date_short,
            "scheduled_matches": scheduled,
            "finished_recent": finished_recent,
            "pred_map": pred_map,
            "comment_map": comment_map,
            "unread_map": unread_map,
            "reads_map": reads_map,
            "live_matches": live_matches,
            "live_pred_map": live_pred_map,
            "scheduled_total": scheduled_total,
            "unfilled_count": unfilled_count,
            "user_fill_status": user_fill_status,
            "match_fill_counts": match_fill_counts,
            "non_bot_total": non_bot_total,
        }

    league_order, league_enabled = _get_league_config()

    ucl_data = _build("UCL")
    pl_data = _build("PL")
    wc_data = _build("WC")

    def _collect_unread_notifications(data, tab_id):
        notifs = []
        if not user:
            return notifs
        match_lookup = {m.id: m for m in data["finished_recent"]}
        for mid, count in data["unread_map"].items():
            match = match_lookup.get(mid)
            if not match:
                continue
            last_read = data["reads_map"].get(mid, 0)
            unread_cmts = [c for c in data["comment_map"].get(mid, [])
                           if c.id > last_read and c.user_id != user.id]
            if not unread_cmts:
                continue
            seen, authors = set(), []
            for c in reversed(unread_cmts):
                if c.user_id not in seen:
                    seen.add(c.user_id)
                    authors.append(c.user.display_name)
            notifs.append({
                "match_id": mid,
                "tab_id": tab_id,
                "home": match.home_team.display_name,
                "away": match.away_team.display_name,
                "count": count,
                "authors": authors[:3],
            })
        return notifs

    unread_notifications = []
    for _lg, _tid, _d in (("UCL", "ucl", ucl_data), ("PL", "pl", pl_data), ("WC", "wc", wc_data)):
        if league_enabled.get(_lg):
            unread_notifications.extend(_collect_unread_notifications(_d, _tid))

    lock_s = Setting.query.get("betting_locked")
    betting_locked = lock_s is not None and lock_s.value == "1"

    reveal_live = True

    def _first_kickoff(matches):
        t = None
        for m in matches:
            if m.kickoff_time and (t is None or m.kickoff_time < t):
                t = m.kickoff_time
        return t.strftime('%Y-%m-%dT%H:%M:%SZ') if t else None

    ucl_first_match_iso = _first_kickoff(ucl_data["scheduled_matches"]) if league_enabled["UCL"] else None
    pl_first_match_iso = _first_kickoff(pl_data["scheduled_matches"]) if league_enabled["PL"] else None
    wc_first_match_iso = _first_kickoff(wc_data["scheduled_matches"]) if league_enabled["WC"] else None

    ucl_commentaries = Commentary.query.filter(
        Commentary.match_label.like("UCL:%")
    ).order_by(Commentary.created_at.asc()).all()
    pl_commentaries = Commentary.query.filter(
        Commentary.match_label.like("PL:%")
    ).order_by(Commentary.created_at.asc()).all()
    wc_commentaries = Commentary.query.filter(
        Commentary.match_label.like("WC:%")
    ).order_by(Commentary.created_at.asc()).all()
    ucl_standings = Commentary.query.filter_by(match_label=STANDINGS_LABEL_UCL).first()
    pl_standings = Commentary.query.filter_by(match_label=STANDINGS_LABEL_PL).first()
    wc_standings = Commentary.query.filter_by(match_label=STANDINGS_LABEL_WC).first()

    form_count_s = Setting.query.get("team_form_matches_count")
    try:
        form_limit = int(form_count_s.value) if form_count_s else 0
    except (ValueError, TypeError):
        form_limit = 0
    team_form_data = {}
    for _league, _data in (("UCL", ucl_data), ("PL", pl_data), ("WC", wc_data)):
        if not league_enabled.get(_league):
            continue
        ids = set()
        for _ms in (_data["scheduled_matches"], _data["finished_recent"], _data["live_matches"]):
            for m in _ms:
                ids.add(m.home_team_id)
                ids.add(m.away_team_id)
        if ids:
            team_form_data.update(_build_team_form_data(_league, ids, form_limit))

    cml_s = Setting.query.get("comment_max_length")
    try:
        comment_max_length = max(1, int(cml_s.value)) if cml_s else 10
    except (ValueError, TypeError):
        comment_max_length = 10

    user_is_day_winner = False
    day_winner_info = None
    for _lg, _data in (("UCL", ucl_data), ("PL", pl_data), ("WC", wc_data)):
        if not league_enabled.get(_lg) or not _data["pred_rounds"]:
            continue
        _latest_rnd = _data["pred_rounds"][0]
        if _latest_rnd == 0:
            continue
        _w = _data.get("round_winners", {}).get(_latest_rnd)
        if _w and len(_w["users"]) <= 2:
            day_winner_info = {
                "names": [u.display_name for u in _w["users"]],
                "pts": _w["pts"],
                "round": _latest_rnd,
                "league": _lg,
            }
            if user and any(u.id == user.id for u in _w["users"]):
                user_is_day_winner = True
            break

    now = datetime.utcnow()
    release_note = ReleaseNote.query.filter_by(active=True).filter(
        ReleaseNote.deployed_at <= now,
        ReleaseNote.deployed_at >= now - timedelta(days=3),
    ).order_by(ReleaseNote.deployed_at.desc()).first()

    return render_template("index.html",
                           ucl=ucl_data,
                           pl=pl_data,
                           wc=wc_data,
                           all_users=all_users,
                           predictions=predictions,
                           ucl_commentaries=ucl_commentaries,
                           pl_commentaries=pl_commentaries,
                           wc_commentaries=wc_commentaries,
                           ucl_standings=ucl_standings,
                           pl_standings=pl_standings,
                           wc_standings=wc_standings,
                           betting_locked=betting_locked,
                           reveal_live=reveal_live,
                           ucl_first_match_iso=ucl_first_match_iso,
                           pl_first_match_iso=pl_first_match_iso,
                           wc_first_match_iso=wc_first_match_iso,
                           league_order=league_order,
                           league_enabled=league_enabled,
                           release_note=release_note,
                           team_form_data=team_form_data,
                           comment_max_length=comment_max_length,
                           unread_notifications=unread_notifications,
                           user_is_day_winner=user_is_day_winner,
                           day_winner_info=day_winner_info)


@main_bp.route("/admin")
@admin_required
def admin():
    ucl_scheduled = (
        Match.query.join(Tour)
        .filter(Tour.league == "UCL", Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    pl_scheduled = (
        Match.query.join(Tour)
        .filter(Tour.league == "PL", Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    wc_scheduled = (
        Match.query.join(Tour)
        .filter(Tour.league == "WC", Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    league_order, league_enabled = _get_league_config()

    next_round = {}
    for lg in ("UCL", "PL", "WC"):
        row = (
            db.session.query(func.max(Match.featured_round))
            .join(Tour)
            .filter(Tour.league == lg, Match.featured_round.isnot(None))
            .scalar()
        )
        next_round[lg] = (row + 1) if row else 1

    from ..services.auto_featured import get_auto_settings
    auto_featured = {lg: get_auto_settings(lg) for lg in ("UCL", "PL", "WC")}

    return render_template("admin.html", ucl_scheduled=ucl_scheduled, pl_scheduled=pl_scheduled,
                           wc_scheduled=wc_scheduled, league_order=league_order,
                           league_enabled=league_enabled, next_round=next_round,
                           auto_featured=auto_featured)


@main_bp.route("/superadmin")
@superuser_required
def superadmin():
    theme_s = Setting.query.get("theme")
    current_theme = theme_s.value if theme_s else "navy"
    users = User.query.filter_by(is_bot=False).order_by(User.username).all()
    current = get_current_user()
    all_scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league.in_(["UCL", "PL", "WC"]), Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    lock_s = Setting.query.get("betting_locked")
    betting_locked = lock_s is not None and lock_s.value == "1"
    league_order, league_enabled = _get_league_config()

    def _pred_limit(lg):
        s = Setting.query.get(f"pred_days_{lg}")
        try:
            return max(1, min(int(s.value), 20)) if s else 4
        except (ValueError, TypeError):
            return 4

    pred_days_limits = {lg: _pred_limit(lg) for lg in ("UCL", "PL", "WC")}

    edit_matches = (
        Match.query
        .join(Tour)
        .filter(Tour.league.in_(["UCL", "PL", "WC"]), Match.featured == True)
        .order_by(Match.kickoff_time.desc())
        .limit(80)
        .all()
    )
    edit_matches_by_league = {"UCL": [], "PL": [], "WC": []}
    for m in edit_matches:
        edit_matches_by_league[m.tour.league].append(m)

    live_matches = (
        Match.query.join(Tour)
        .filter(Tour.league.in_(["UCL", "PL", "WC"]), Match.status == "live", Match.featured == True)
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    reveal_s = Setting.query.get("reveal_live_predictions")
    reveal_live = reveal_s is not None and reveal_s.value == "1"

    from ..models import PromptHint
    from ..seed import PROMPT_TEMPLATES, TOURNAMENT_LABELS, LEAGUE_TO_TOURNAMENT
    _tournament_to_league = {v: k for k, v in LEAGUE_TO_TOURNAMENT.items()}
    prompt_templates = []
    for tournament_id in PROMPT_TEMPLATES:
        league_code = _tournament_to_league.get(tournament_id, tournament_id)
        if not league_enabled.get(league_code, True):
            continue
        label = TOURNAMENT_LABELS.get(tournament_id, tournament_id)
        rows = {h.hint_type: h for h in
                PromptHint.query.filter_by(tournament=tournament_id).all()}
        prompt_templates.append({
            "tournament": tournament_id,
            "label": label,
            "prompt": rows.get("prompt"),
            "standings": rows.get("standings"),
        })

    release_notes = ReleaseNote.query.order_by(ReleaseNote.deployed_at.desc()).all()

    af_enabled_s = Setting.query.get("auto_fetch_enabled")
    auto_fetch_enabled = af_enabled_s is not None and af_enabled_s.value == "1"
    af_interval_s = Setting.query.get("auto_fetch_interval_min")
    odds_s = Setting.query.get("odds_fetch_enabled")
    odds_fetch_enabled = odds_s is None or odds_s.value != "0"
    try:
        auto_fetch_interval = max(5, min(int(af_interval_s.value), 120)) if af_interval_s else 15
    except (ValueError, TypeError):
        auto_fetch_interval = 15

    tfc_s = Setting.query.get("team_form_matches_count")
    try:
        team_form_count = max(0, int(tfc_s.value)) if tfc_s else 0
    except (ValueError, TypeError):
        team_form_count = 0

    cml_s = Setting.query.get("comment_max_length")
    try:
        sa_comment_max_length = max(1, int(cml_s.value)) if cml_s else 10
    except (ValueError, TypeError):
        sa_comment_max_length = 10

    tg_enabled_s = Setting.query.get("tg_remind_enabled")
    tg_remind_enabled = tg_enabled_s is not None and tg_enabled_s.value == "1"
    try:
        tg_before_min = max(1, int(Setting.query.get("tg_remind_before_min").value)) if Setting.query.get("tg_remind_before_min") else 60
    except (ValueError, TypeError, AttributeError):
        tg_before_min = 60
    try:
        tg_quiet_from = max(0, min(int(Setting.query.get("tg_remind_quiet_from").value), 23)) if Setting.query.get("tg_remind_quiet_from") else 23
    except (ValueError, TypeError, AttributeError):
        tg_quiet_from = 23
    try:
        tg_quiet_to = max(0, min(int(Setting.query.get("tg_remind_quiet_to").value), 23)) if Setting.query.get("tg_remind_quiet_to") else 7
    except (ValueError, TypeError, AttributeError):
        tg_quiet_to = 7
    tg_last_sent_s = Setting.query.get("tg_remind_last_sent")
    tg_last_sent = tg_last_sent_s.value if tg_last_sent_s else ""

    return render_template("superadmin.html", current_theme=current_theme, users=users,
                           current_user=current, all_scheduled=all_scheduled,
                           betting_locked=betting_locked,
                           league_order=league_order, league_enabled=league_enabled,
                           pred_days_limits=pred_days_limits,
                           edit_matches_by_league=edit_matches_by_league,
                           live_matches=live_matches,
                           reveal_live=reveal_live,
                           prompt_templates=prompt_templates,
                           release_notes=release_notes,
                           auto_fetch_enabled=auto_fetch_enabled,
                           auto_fetch_interval=auto_fetch_interval,
                           odds_fetch_enabled=odds_fetch_enabled,
                           team_form_count=team_form_count,
                           comment_max_length=sa_comment_max_length,
                           tg_remind_enabled=tg_remind_enabled,
                           tg_before_min=tg_before_min,
                           tg_quiet_from=tg_quiet_from,
                           tg_quiet_to=tg_quiet_to,
                           tg_last_sent=tg_last_sent)


@main_bp.route("/activity-log")
@superuser_required
def activity_log():
    today = date_type.today()
    date_from_str = request.args.get("date_from", (today - timedelta(days=6)).strftime("%Y-%m-%d"))
    date_to_str = request.args.get("date_to", today.strftime("%Y-%m-%d"))
    filter_user_id = request.args.get("user_id", "", type=int) or None

    try:
        date_from = datetime.strptime(date_from_str, "%Y-%m-%d")
        date_to = datetime.strptime(date_to_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        date_from = datetime.combine(today - timedelta(days=6), datetime.min.time())
        date_to = datetime.combine(today, datetime.max.time())

    q = ActivityLog.query.filter(
        ActivityLog.created_at >= date_from,
        ActivityLog.created_at <= date_to,
    )
    if filter_user_id:
        q = q.filter(ActivityLog.user_id == filter_user_id)

    logs = q.order_by(ActivityLog.created_at.desc()).limit(500).all()
    users = User.query.filter_by(is_bot=False).order_by(User.username).all()

    return render_template("activity_log.html",
                           logs=logs, users=users,
                           action_labels=ACTION_LABELS,
                           date_from=date_from_str,
                           date_to=date_to_str,
                           filter_user_id=filter_user_id)


@main_bp.route("/stats")
@admin_required
def stats():
    users = User.query.order_by(User.id).all()

    league_order, league_enabled = _get_league_config()
    enabled_leagues = [lg for lg in league_order if league_enabled.get(lg)]
    league_names = {"UCL": "ЛЧ", "PL": "АПЛ", "WC": "ЧМ 2026"}

    active_league = request.args.get("league")
    if active_league not in enabled_leagues:
        active_league = enabled_leagues[0] if enabled_leagues else None

    if not active_league:
        return render_template("stats.html", users=users, overall_stats=[],
                               chart_labels=[], chart_datasets=[], rank_datasets=[],
                               bar_labels=[], bar_datasets=[], chart_player_stats=[],
                               enabled_leagues=[], active_league=None,
                               league_names=league_names, rounds_table=[],
                               top_actual_scores=[], top_exact_scores=[], top_pred_scores=[])

    # ── Общая статистика по активной лиге ────────────────────────
    rows = (
        db.session.query(
            Prediction.user_id,
            func.coalesce(func.sum(PredictionPoints.points), 0).label("total_points"),
            func.sum(sa_case((PredictionPoints.reason == "exact",  1), else_=0)).label("exact_count"),
            func.sum(sa_case((PredictionPoints.reason == "winner", 1), else_=0)).label("winner_count"),
            func.sum(sa_case((PredictionPoints.reason == "none",   1), else_=0)).label("none_count"),
            func.count(Prediction.id).label("total_preds"),
        )
        .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
        .join(Match, Prediction.match_id == Match.id)
        .join(Tour, Match.tour_id == Tour.id)
        .filter(Match.status == "finished", Tour.league == active_league)
        .group_by(Prediction.user_id)
        .all()
    )

    overall_map = {r.user_id: r for r in rows}
    overall_stats = []
    for u in users:
        r = overall_map.get(u.id)
        total  = r.total_preds   if r else 0
        exact  = r.exact_count   if r else 0
        winner = r.winner_count  if r else 0
        none_c = r.none_count    if r else 0
        pts    = int(r.total_points) if r else 0
        accuracy = round((exact + winner) / total * 100) if total else 0
        exact_accuracy = round(exact / total * 100) if total else 0
        overall_stats.append({
            "user": u, "points": pts, "exact": exact,
            "winner": winner, "none": none_c,
            "total": total, "accuracy": accuracy, "exact_accuracy": exact_accuracy,
        })
    overall_stats.sort(key=lambda x: x["points"], reverse=True)

    # ── График по игровым турам ───────────────────────────────────
    _months_short = ["", "янв", "фев", "мар", "апр", "май", "июн",
                     "июл", "авг", "сен", "окт", "ноя", "дек"]

    def _fmt_range_short(min_d, max_d):
        if min_d == max_d:
            return f"{min_d.day} {_months_short[min_d.month]}"
        if min_d.month == max_d.month:
            return f"{min_d.day}–{max_d.day} {_months_short[max_d.month]}"
        return f"{min_d.day} {_months_short[min_d.month]} – {max_d.day} {_months_short[max_d.month]}"

    round_order_rows = (
        db.session.query(
            Match.featured_round,
            func.min(Match.kickoff_time).label("min_kt"),
            func.max(Match.kickoff_time).label("max_kt"),
        )
        .join(Tour, Match.tour_id == Tour.id)
        .filter(
            Match.status == "finished",
            Match.featured == True,
            Match.featured_round.isnot(None),
            Match.featured_round != 0,
            Tour.league == active_league,
        )
        .group_by(Match.featured_round)
        .order_by(func.min(Match.kickoff_time))
        .all()
    )

    chart_pts_rows = (
        db.session.query(
            Match.featured_round,
            Prediction.user_id,
            func.sum(PredictionPoints.points).label("pts"),
        )
        .join(Tour, Match.tour_id == Tour.id)
        .join(Prediction, Match.id == Prediction.match_id)
        .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
        .filter(
            Match.status == "finished",
            Match.featured == True,
            Match.featured_round.isnot(None),
            Match.featured_round != 0,
            Tour.league == active_league,
        )
        .group_by(Match.featured_round, Prediction.user_id)
        .all()
    )

    round_pts_map = {}
    for r in chart_pts_rows:
        round_pts_map.setdefault(r.featured_round, {})[r.user_id] = int(r.pts)


    round_chart_labels = []
    for i, (rnd, min_kt, max_kt) in enumerate(round_order_rows, 1):
        if min_kt:
            min_d = (min_kt + timedelta(hours=3)).date()
            max_d = (max_kt + timedelta(hours=3)).date()
            round_chart_labels.append(f"День {i} ({_fmt_range_short(min_d, max_d)})")
        else:
            round_chart_labels.append(f"День {i}")

    _CHART_PALETTE = [
        "#7c7cff", "#ff6b6b", "#00c87a", "#f5a623", "#a78bfa",
        "#38bdf8", "#fb7185", "#34d399", "#fbbf24", "#e879f9",
    ]

    def _chart_color(user, used):
        c = (user.avatar_color or "").strip()
        if c and c not in used:
            used.add(c)
            return c
        for p in _CHART_PALETTE:
            if p not in used:
                used.add(p)
                return p
        return c or _CHART_PALETTE[0]

    chart_labels = ["Старт"] + round_chart_labels
    chart_datasets = []
    cumulative_by_user = {}
    used_colors = set()
    user_color_map = {}
    for u in users:
        col = _chart_color(u, used_colors)
        user_color_map[u.id] = col
        cumulative = 0
        data = [0]
        for rnd, _, __ in round_order_rows:
            cumulative += round_pts_map.get(rnd, {}).get(u.id, 0)
            data.append(cumulative)
        chart_datasets.append({"label": u.display_name, "color": col, "data": data})
        cumulative_by_user[u.id] = data

    # Colors + serializable stats for template charts
    for s in overall_stats:
        s["color"] = user_color_map.get(s["user"].id, "#7c7cff")

    bar_labels = round_chart_labels
    bar_datasets = [
        {"label": u.display_name, "color": user_color_map[u.id],
         "data": [round_pts_map.get(rnd, {}).get(u.id, 0) for rnd, _, __ in round_order_rows]}
        for u in users
    ]

    chart_player_stats = [
        {"name": s["user"].display_name, "color": s["color"],
         "points": s["points"], "exact": s["exact"], "winner": s["winner"],
         "none": s["none"], "total": s["total"],
         "accuracy": s["accuracy"], "exact_accuracy": s["exact_accuracy"]}
        for s in overall_stats
    ]

    # ── График позиций в лидерборде по игровым турам ─────────────
    rank_datasets = []
    for u in users:
        rank_data = [1]
        for t_idx in range(1, len(round_order_rows) + 1):
            my_pts = cumulative_by_user[u.id][t_idx]
            rank = sum(1 for uid, d in cumulative_by_user.items() if d[t_idx] > my_pts) + 1
            rank_data.append(rank)
        rank_datasets.append({"label": u.display_name, "color": user_color_map[u.id], "data": rank_data})

    # ── Таблицы по игровым дням ───────────────────────────────────
    _months_long = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
                    "июля", "августа", "сентября", "октября", "ноября", "декабря"]

    def _fmt_range_long(min_d, max_d):
        if min_d == max_d:
            return f"{min_d.day} {_months_long[min_d.month]}"
        if min_d.month == max_d.month:
            return f"{min_d.day}–{max_d.day} {_months_long[max_d.month]}"
        return f"{min_d.day} {_months_long[min_d.month]} – {max_d.day} {_months_long[max_d.month]}"

    # ── Матрица матчей по игровым дням ───────────────────────────
    all_finished = (
        Match.query
        .join(Tour, Match.tour_id == Tour.id)
        .filter(Match.status == "finished", Match.featured == True, Tour.league == active_league)
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    all_match_ids = [m.id for m in all_finished]
    fm_scores_map = {}
    fm_preds_map = {}
    fm_pts_map = {}
    if all_match_ids:
        fm_scores_map = {s.match_id: s for s in Score.query.filter(Score.match_id.in_(all_match_ids)).all()}
        for p in Prediction.query.filter(Prediction.match_id.in_(all_match_ids)).all():
            fm_preds_map[(p.match_id, p.user_id)] = p
        pred_ids = [p.id for p in fm_preds_map.values()]
        if pred_ids:
            for pp in PredictionPoints.query.filter(PredictionPoints.prediction_id.in_(pred_ids)).all():
                fm_pts_map[pp.prediction_id] = pp

    match_by_round = {}
    for m in all_finished:
        match_by_round.setdefault(m.featured_round, []).append(m)

    rounds_table = []
    for i, (rnd, min_kt, max_kt) in enumerate(round_order_rows, 1):
        date_label = ""
        if min_kt:
            min_d = (min_kt + timedelta(hours=3)).date()
            max_d = (max_kt + timedelta(hours=3)).date()
            date_label = _fmt_range_long(min_d, max_d)
        match_rows = []
        for m in match_by_round.get(rnd, []):
            sc = fm_scores_map.get(m.id)
            user_cells = []
            for u in users:
                pred = fm_preds_map.get((m.id, u.id))
                pp = fm_pts_map.get(pred.id) if pred else None
                user_cells.append({
                    "pred": f"{pred.home_score}:{pred.away_score}" if pred else None,
                    "points": pp.points if pp else None,
                    "reason": pp.reason if pp else None,
                })
            match_rows.append({
                "home": m.home_team.display_name,
                "away": m.away_team.display_name,
                "score": _score_display(sc),
                "user_cells": user_cells,
            })
        rounds_table.append({
            "index": i,
            "round": rnd,
            "date_label": date_label,
            "matches": match_rows,
        })
    rounds_table.reverse()

    archive_matches = match_by_round.get(0, [])
    if archive_matches:
        arc_kts = [m.kickoff_time for m in archive_matches if m.kickoff_time]
        arc_date = ""
        if arc_kts:
            min_d = (min(arc_kts) + timedelta(hours=3)).date()
            max_d = (max(arc_kts) + timedelta(hours=3)).date()
            arc_date = _fmt_range_long(min_d, max_d)
        arc_rows = []
        for m in archive_matches:
            sc = fm_scores_map.get(m.id)
            user_cells = []
            for u in users:
                pred = fm_preds_map.get((m.id, u.id))
                pp = fm_pts_map.get(pred.id) if pred else None
                user_cells.append({
                    "pred": f"{pred.home_score}:{pred.away_score}" if pred else None,
                    "points": pp.points if pp else None,
                    "reason": pp.reason if pp else None,
                })
            arc_rows.append({
                "home": m.home_team.display_name,
                "away": m.away_team.display_name,
                "score": _score_display(sc),
                "user_cells": user_cells,
            })
        rounds_table.append({"index": 0, "round": 0, "date_label": arc_date, "matches": arc_rows})

    # ── Топ-10 счётов матчей по активной лиге ────────────────────
    score_rows = (
        db.session.query(Score.home_score, Score.away_score, func.count(Score.id).label("cnt"))
        .join(Match, Score.match_id == Match.id)
        .join(Tour, Match.tour_id == Tour.id)
        .filter(Match.status == "finished", Score.home_score.isnot(None), Tour.league == active_league)
        .group_by(Score.home_score, Score.away_score)
        .all()
    )
    bucket = defaultdict(int)
    for r in score_rows:
        key = (max(r.home_score, r.away_score), min(r.home_score, r.away_score))
        bucket[key] += r.cnt
    top_actual_scores = []
    for (hi, lo), cnt in sorted(bucket.items(), key=lambda x: x[1], reverse=True)[:10]:
        label = f"{hi}:{lo}" if hi == lo else f"{hi}:{lo} / {lo}:{hi}"
        top_actual_scores.append({"score": label, "count": cnt})

    # ── Топ-10 угаданных счётов (exact) по активной лиге ─────────
    exact_rows = (
        db.session.query(Prediction.home_score, Prediction.away_score, func.count(Prediction.id).label("cnt"))
        .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
        .join(Match, Prediction.match_id == Match.id)
        .join(Tour, Match.tour_id == Tour.id)
        .filter(PredictionPoints.reason == "exact", Tour.league == active_league)
        .group_by(Prediction.home_score, Prediction.away_score)
        .all()
    )
    exact_bucket = defaultdict(int)
    for r in exact_rows:
        key = (max(r.home_score, r.away_score), min(r.home_score, r.away_score))
        exact_bucket[key] += r.cnt
    top_exact_scores = []
    for (hi, lo), cnt in sorted(exact_bucket.items(), key=lambda x: x[1], reverse=True)[:10]:
        label = f"{hi}:{lo}" if hi == lo else f"{hi}:{lo} / {lo}:{hi}"
        top_exact_scores.append({"score": label, "count": cnt})

    # ── Топ-10 прогнозов игроков по активной лиге ────────────────
    pred_rows = (
        db.session.query(Prediction.home_score, Prediction.away_score, func.count(Prediction.id).label("cnt"))
        .join(Match, Prediction.match_id == Match.id)
        .join(Tour, Match.tour_id == Tour.id)
        .filter(Match.status == "finished", Tour.league == active_league)
        .group_by(Prediction.home_score, Prediction.away_score)
        .all()
    )
    pred_bucket = defaultdict(int)
    for r in pred_rows:
        key = (max(r.home_score, r.away_score), min(r.home_score, r.away_score))
        pred_bucket[key] += r.cnt
    top_pred_scores = []
    for (hi, lo), cnt in sorted(pred_bucket.items(), key=lambda x: x[1], reverse=True)[:10]:
        label = f"{hi}:{lo}" if hi == lo else f"{hi}:{lo} / {lo}:{hi}"
        top_pred_scores.append({"score": label, "count": cnt})

    return render_template("stats.html",
                           users=users,
                           overall_stats=overall_stats,
                           chart_labels=chart_labels,
                           chart_datasets=chart_datasets,
                           rank_datasets=rank_datasets,
                           bar_labels=bar_labels,
                           bar_datasets=bar_datasets,
                           chart_player_stats=chart_player_stats,
                           enabled_leagues=enabled_leagues,
                           active_league=active_league,
                           league_names=league_names,
                           top_actual_scores=top_actual_scores,
                           top_exact_scores=top_exact_scores,
                           top_pred_scores=top_pred_scores,
                           rounds_table=rounds_table)
