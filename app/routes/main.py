from flask import Blueprint, render_template
from ..models import Match, Tour, User, Prediction
from ..services.points import get_leaderboard

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    matches = (
        Match.query
        .join(Tour)
        .filter(Tour.league == "UCL")
        .order_by(Match.kickoff_time.desc())
        .limit(30)
        .all()
    )

    user = User.query.filter_by(username="test").first()
    predictions = {}
    if user:
        for p in Prediction.query.filter_by(user_id=user.id).all():
            predictions[p.match_id] = p

    last_tour = (
        Tour.query.filter_by(league="UCL")
        .order_by(Tour.round_number.desc())
        .first()
    )
    leaderboard = get_leaderboard(last_tour_id=last_tour.id if last_tour else None)

    return render_template("index.html", matches=matches, predictions=predictions,
                           leaderboard=leaderboard, last_tour=last_tour)


@main_bp.route("/admin")
def admin():
    return render_template("admin.html")
