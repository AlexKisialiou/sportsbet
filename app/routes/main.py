from flask import Blueprint, render_template
from ..models import Match, Tour, User, Prediction

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

    return render_template("index.html", matches=matches, predictions=predictions)


@main_bp.route("/admin")
def admin():
    return render_template("admin.html")
