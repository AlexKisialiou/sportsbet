from datetime import date as date_type, datetime, timedelta
from flask import render_template, request
from sqlalchemy import func
from ..models import db, Match, Tour, Prediction, User, Commentary, ActivityLog
from ..services.points import get_leaderboard
from ..services.activity import ACTION_LABELS
from ..auth import get_current_user, login_required, admin_required, superuser_required

from flask import Blueprint
main_bp = Blueprint("main", __name__)


def _parse_days(rows):
    result = []
    for r in rows:
        raw = r[0]
        result.append(date_type.fromisoformat(raw) if isinstance(raw, str) else raw)
    return result


@main_bp.route("/")
@login_required
def index():
    _leagues = ["UCL", "PL"]

    # Last 4 game days with finished matches
    pred_days = _parse_days(
        db.session.query(func.date(Match.kickoff_time))
        .join(Tour)
        .filter(Tour.league.in_(_leagues), Match.status == "finished")
        .group_by(func.date(Match.kickoff_time))
        .order_by(func.date(Match.kickoff_time).desc())
        .limit(4)
        .all()
    )

    # Leaderboard: 4 columns (one per game day)
    leaderboard = get_leaderboard(last_days=pred_days)

    # Matches list: featured scheduled + finished from last 4 game days (max 10 finished)
    scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league.in_(_leagues), Match.status == "scheduled", Match.featured == True)
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    finished_for_list = []
    if pred_days:
        finished_for_list = (
            Match.query
            .join(Tour)
            .filter(
                Tour.league.in_(_leagues),
                Match.status != "scheduled",
                func.date(Match.kickoff_time).in_(pred_days),
            )
            .order_by(Match.kickoff_time.desc())
            .limit(10)
            .all()
        )
    matches = sorted(
        scheduled + finished_for_list,
        key=lambda m: m.kickoff_time,
        reverse=True,
    )

    user = get_current_user()
    predictions = {}
    if user:
        for p in Prediction.query.filter_by(user_id=user.id).all():
            predictions[p.match_id] = p

    scheduled_matches = [m for m in matches if m.status == "scheduled"]
    scheduled_total = len(scheduled_matches)
    unfilled_count = sum(1 for m in scheduled_matches if m.id not in predictions)

    # Fill status per user across all featured scheduled matches
    all_sched_ids = [m.id for m in scheduled_matches]
    all_preds_sched = set()
    if all_sched_ids:
        for p in Prediction.query.filter(Prediction.match_id.in_(all_sched_ids)).all():
            all_preds_sched.add((p.match_id, p.user_id))

    user_fill_status = []
    for lb_row in leaderboard:
        u = lb_row["user"]
        filled = sum(1 for m in scheduled_matches if (m.id, u.id) in all_preds_sched)
        user_fill_status.append({"user": u, "filled": filled, "total": scheduled_total})

    # Predictions table: finished matches from last 4 game days, max 10
    all_users = User.query.order_by(User.username).all()
    finished_recent = []
    pred_map = {}
    if pred_days:
        finished_recent = (
            Match.query
            .join(Tour)
            .filter(
                Tour.league.in_(_leagues),
                Match.status == "finished",
                func.date(Match.kickoff_time).in_(pred_days),
            )
            .order_by(Match.kickoff_time.desc())
            .limit(10)
            .all()
        )
        match_ids = [m.id for m in finished_recent]
        if match_ids:
            for p in Prediction.query.filter(Prediction.match_id.in_(match_ids)).all():
                pred_map[(p.match_id, p.user_id)] = p

    from ..services.groq_api import STANDINGS_LABEL
    commentaries = Commentary.query.filter(Commentary.match_label != STANDINGS_LABEL).order_by(Commentary.created_at.asc()).all()
    standings_commentary = Commentary.query.filter_by(match_label=STANDINGS_LABEL).first()

    return render_template("index.html",
                           scheduled_matches=scheduled_matches,
                           predictions=predictions,
                           commentaries=commentaries,
                           standings_commentary=standings_commentary,
                           leaderboard=leaderboard, last_days=pred_days,
                           scheduled_total=scheduled_total,
                           unfilled_count=unfilled_count,
                           user_fill_status=user_fill_status,
                           finished_recent=finished_recent,
                           all_users=all_users,
                           pred_map=pred_map)


@main_bp.route("/admin")
@admin_required
def admin():
    all_scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league.in_(["UCL", "PL"]), Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    return render_template("admin.html", all_scheduled=all_scheduled)


@main_bp.route("/superadmin")
@superuser_required
def superadmin():
    from ..models import Setting
    theme_s = Setting.query.get("theme")
    current_theme = theme_s.value if theme_s else "navy"
    users = User.query.filter_by(is_bot=False).order_by(User.username).all()
    current = get_current_user()
    all_scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league.in_(["UCL", "PL"]), Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    return render_template("superadmin.html", current_theme=current_theme, users=users,
                           current_user=current, all_scheduled=all_scheduled)


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
