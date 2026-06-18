from datetime import date as date_type, datetime, timedelta
from flask import render_template, request
from sqlalchemy import func, case as sa_case
from ..models import db, Match, Tour, Prediction, PredictionPoints, User, Commentary, ActivityLog, Setting, ReleaseNote, Score, Team, MatchComment
from ..services.points import get_leaderboard
from ..services.activity import ACTION_LABELS
from ..services.groq_api import STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL, STANDINGS_LABEL_WC
from ..auth import get_current_user, login_required, admin_required, superuser_required

from flask import Blueprint
main_bp = Blueprint("main", __name__)


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
            lst.append({
                "date": dt,
                "opponent": opponent.display_name,
                "opponent_crest": opponent.crest or "",
                "score": f"{tg}:{og}",
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


def _parse_days(rows):
    result = []
    for r in rows:
        raw = r[0]
        result.append(date_type.fromisoformat(raw) if isinstance(raw, str) else raw)
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
        pred_days = _parse_days(
            db.session.query(func.date(Match.kickoff_time))
            .join(Tour)
            .filter(Tour.league == league, Match.status == "finished", Match.featured == True)
            .group_by(func.date(Match.kickoff_time))
            .order_by(func.date(Match.kickoff_time).desc())
            .limit(_get_pred_limit(league))
            .all()
        )

        leaderboard = get_leaderboard(last_days=pred_days, league=league)

        scheduled = (
            Match.query
            .join(Tour)
            .filter(Tour.league == league, Match.status == "scheduled", Match.featured == True)
            .order_by(Match.kickoff_time.asc())
            .all()
        )

        finished_recent = []
        pred_map = {}
        comment_map = {}
        if pred_days:
            finished_recent = (
                Match.query.join(Tour)
                .filter(
                    Tour.league == league,
                    Match.status == "finished",
                    Match.featured == True,
                    func.date(Match.kickoff_time).in_(pred_days),
                )
                .order_by(Match.kickoff_time.desc())
                .limit(30)
                .all()
            )
            match_ids = [m.id for m in finished_recent]
            if match_ids:
                for p in Prediction.query.filter(Prediction.match_id.in_(match_ids)).all():
                    pred_map[(p.match_id, p.user_id)] = p
                for c in MatchComment.query.filter(MatchComment.match_id.in_(match_ids)).all():
                    comment_map.setdefault(c.match_id, {})[c.user_id] = c

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

        return {
            "leaderboard": leaderboard,
            "pred_days": pred_days,
            "scheduled_matches": scheduled,
            "finished_recent": finished_recent,
            "pred_map": pred_map,
            "comment_map": comment_map,
            "live_matches": live_matches,
            "live_pred_map": live_pred_map,
            "scheduled_total": scheduled_total,
            "unfilled_count": unfilled_count,
            "user_fill_status": user_fill_status,
        }

    league_order, league_enabled = _get_league_config()

    ucl_data = _build("UCL")
    pl_data = _build("PL")
    wc_data = _build("WC")

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
                           comment_max_length=comment_max_length)


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
    return render_template("admin.html", ucl_scheduled=ucl_scheduled, pl_scheduled=pl_scheduled,
                           wc_scheduled=wc_scheduled, league_order=league_order,
                           league_enabled=league_enabled)


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
                           team_form_count=team_form_count,
                           comment_max_length=sa_comment_max_length)


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
    league_filter = request.args.get("league")  # None | "UCL" | "PL" | "WC"

    users = User.query.filter_by(is_bot=False).order_by(User.id).all()

    # ── Общая статистика ──────────────────────────────────────────
    base_q = (
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
        .filter(Match.status == "finished")
    )
    if league_filter:
        base_q = base_q.filter(Tour.league == league_filter)
    rows = base_q.group_by(Prediction.user_id).all()

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
        overall_stats.append({
            "user": u, "points": pts, "exact": exact,
            "winner": winner, "none": none_c,
            "total": total, "accuracy": accuracy,
        })
    overall_stats.sort(key=lambda x: x["points"], reverse=True)

    # ── График: туры в хронологическом порядке ───────────────────
    tour_order_q = (
        db.session.query(
            Tour.id,
            Tour.name,
            func.min(Match.kickoff_time).label("first_kickoff"),
        )
        .join(Match, Tour.id == Match.tour_id)
        .filter(Match.status == "finished", Match.featured == True)
    )
    if league_filter:
        tour_order_q = tour_order_q.filter(Tour.league == league_filter)
    tour_order_rows = (
        tour_order_q.group_by(Tour.id, Tour.name)
        .order_by(func.min(Match.kickoff_time))
        .all()
    )

    chart_pts_q = (
        db.session.query(
            Tour.id,
            Prediction.user_id,
            func.sum(PredictionPoints.points).label("pts"),
        )
        .join(Match, Tour.id == Match.tour_id)
        .join(Prediction, Match.id == Prediction.match_id)
        .join(PredictionPoints, Prediction.id == PredictionPoints.prediction_id)
        .filter(Match.status == "finished", Match.featured == True)
    )
    if league_filter:
        chart_pts_q = chart_pts_q.filter(Tour.league == league_filter)
    chart_pts_rows = chart_pts_q.group_by(Tour.id, Prediction.user_id).all()

    tour_pts_map = {}
    for r in chart_pts_rows:
        tour_pts_map.setdefault(r.id, {})[r.user_id] = int(r.pts)

    chart_labels = [r.name for r in tour_order_rows]
    chart_datasets = []
    for u in users:
        cumulative = 0
        data = []
        for t in tour_order_rows:
            cumulative += tour_pts_map.get(t.id, {}).get(u.id, 0)
            data.append(cumulative)
        chart_datasets.append({
            "label": u.display_name,
            "color": u.avatar_color or "#7c7caa",
            "data": data,
        })

    # Вкладки лиг
    _le = {
        "UCL": Setting.query.get("league_enabled_UCL"),
        "PL":  Setting.query.get("league_enabled_PL"),
        "WC":  Setting.query.get("league_enabled_WC"),
    }
    enabled_leagues = [lg for lg in ["WC", "UCL", "PL"]
                       if not _le[lg] or _le[lg].value != "0"]

    return render_template("stats.html",
                           users=users,
                           overall_stats=overall_stats,
                           chart_labels=chart_labels,
                           chart_datasets=chart_datasets,
                           active_league=league_filter,
                           enabled_leagues=enabled_leagues)
