from flask import Blueprint, render_template
from ..models import Match, Tour

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
    return render_template("index.html", matches=matches)


@main_bp.route("/champions-league")
def champions_league():
    return render_template("cl.html")
