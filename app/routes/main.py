from collections import defaultdict
from datetime import date as date_type
from flask import render_template
from sqlalchemy import func
from ..models import db, Match, Tour, Prediction, User
from ..services.points import get_leaderboard
from ..auth import get_current_user, login_required, admin_required

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
    # Last 4 game days with finished UCL matches
    pred_days = _parse_days(
        db.session.query(func.date(Match.kickoff_time))
        .join(Tour)
        .filter(Tour.league == "UCL", Match.status == "finished")
        .group_by(func.date(Match.kickoff_time))
        .order_by(func.date(Match.kickoff_time).desc())
        .limit(4)
        .all()
    )

    # Leaderboard: 4 columns (one per game day)
    leaderboard = get_leaderboard(last_days=pred_days)

    # Matches list: all scheduled + finished from last 4 game days (max 10 finished)
    scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league == "UCL", Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    finished_for_list = []
    if pred_days:
        finished_for_list = (
            Match.query
            .join(Tour)
            .filter(
                Tour.league == "UCL",
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
    unfilled_count = sum(1 for m in scheduled_matches if m.id not in predictions)

    # Per-day status for next 4 upcoming game days
    sched_by_date = defaultdict(list)
    for m in scheduled:
        if m.kickoff_time:
            sched_by_date[m.kickoff_time.date()].append(m)
    status_days = []
    for d in sorted(sched_by_date.keys())[:4]:
        day_matches = sched_by_date[d]
        filled = sum(1 for m in day_matches if m.id in predictions)
        status_days.append({"date": d, "total": len(day_matches), "filled": filled})

    # Fill status matrix: all users × upcoming game days
    all_sched_ids = [m.id for m in scheduled]
    all_preds_sched = set()
    if all_sched_ids:
        for p in Prediction.query.filter(Prediction.match_id.in_(all_sched_ids)).all():
            all_preds_sched.add((p.match_id, p.user_id))

    user_fill_status = []
    for lb_row in leaderboard:
        u = lb_row["user"]
        days = []
        for sd in status_days:
            day_matches = sched_by_date[sd["date"]]
            filled = sum(1 for m in day_matches if (m.id, u.id) in all_preds_sched)
            days.append({"filled": filled, "total": sd["total"]})
        user_fill_status.append({"user": u, "days": days})

    # Predictions table: finished matches from last 4 game days, max 10
    all_users = User.query.order_by(User.username).all()
    finished_recent = []
    pred_map = {}
    if pred_days:
        finished_recent = (
            Match.query
            .join(Tour)
            .filter(
                Tour.league == "UCL",
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

    return render_template("index.html", matches=matches, predictions=predictions,
                           leaderboard=leaderboard, last_days=pred_days,
                           status_days=status_days,
                           user_fill_status=user_fill_status,
                           finished_recent=finished_recent,
                           all_users=all_users,
                           pred_map=pred_map)


@main_bp.route("/admin")
@admin_required
def admin():
    return render_template("admin.html")
