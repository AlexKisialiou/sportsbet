from datetime import date as date_type, datetime, timedelta
from flask import render_template, request
from sqlalchemy import func
from ..models import db, Match, Tour, Prediction, User, Commentary, ActivityLog, Setting
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
                           ucl_first_match_iso=ucl_first_match_iso,
                           pl_first_match_iso=pl_first_match_iso,
                           wc_first_match_iso=wc_first_match_iso,
                           league_order=league_order,
                           league_enabled=league_enabled)


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

    return render_template("superadmin.html", current_theme=current_theme, users=users,
                           current_user=current, all_scheduled=all_scheduled,
                           betting_locked=betting_locked,
                           league_order=league_order, league_enabled=league_enabled,
                           pred_days_limits=pred_days_limits,
                           edit_matches_by_league=edit_matches_by_league)


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
