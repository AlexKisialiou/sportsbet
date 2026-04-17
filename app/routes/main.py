from datetime import date as date_type
from flask import render_template
from sqlalchemy import func
from ..models import db, Match, Tour, Prediction, User, Commentary
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

    # Matches list: featured scheduled + finished from last 4 game days (max 10 finished)
    scheduled = (
        Match.query
        .join(Tour)
        .filter(Tour.league == "UCL", Match.status == "scheduled", Match.featured == True)
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

    from ..services.groq_api import STANDINGS_LABEL
    commentaries = Commentary.query.filter(Commentary.match_label != STANDINGS_LABEL).order_by(Commentary.created_at.asc()).all()
    standings_commentary = Commentary.query.filter_by(match_label=STANDINGS_LABEL).first()

    return render_template("index.html", matches=matches, predictions=predictions,
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
        .filter(Tour.league == "UCL", Match.status == "scheduled")
        .order_by(Match.kickoff_time.asc())
        .all()
    )
    return render_template("admin.html", all_scheduled=all_scheduled)
