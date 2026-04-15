import os
from flask import Flask, render_template
from models import db, Tour, Match, Score
from datetime import datetime, date

app = Flask(__name__)

# DB config: use DATABASE_URL from env (Render Postgres) or local SQLite
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///sportsbet.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


def seed_data():
    """Fill DB with sample data if empty."""
    if Tour.query.count() > 0:
        return

    # Tour 11 - finished
    t11 = Tour(
        name="Тур 11", season="2025/2026", round_number=11,
        start_date=date(2026, 4, 1), end_date=date(2026, 4, 3),
        status="finished"
    )
    db.session.add(t11)
    db.session.flush()

    matches_t11 = [
        ("Динамо", "Шахтёр", "finished", 1, 2),
        ("Металлист", "Ворскла", "finished", 0, 0),
        ("Десна", "Колос", "finished", 3, 1),
    ]
    for home, away, status, hs, as_ in matches_t11:
        m = Match(tour_id=t11.id, home_team=home, away_team=away,
                  kickoff_time=datetime(2026, 4, 1, 18, 0), status=status)
        db.session.add(m)
        db.session.flush()
        db.session.add(Score(match_id=m.id, home_score=hs, away_score=as_))

    # Tour 12 - active
    t12 = Tour(
        name="Тур 12", season="2025/2026", round_number=12,
        start_date=date(2026, 4, 15), end_date=date(2026, 4, 17),
        status="active"
    )
    db.session.add(t12)
    db.session.flush()

    matches_t12 = [
        ("Шахтёр", "Десна", "finished", 2, 0),
        ("Ворскла", "Динамо", "live", None, None),
        ("Колос", "Металлист", "scheduled", None, None),
    ]
    for home, away, status, hs, as_ in matches_t12:
        m = Match(tour_id=t12.id, home_team=home, away_team=away,
                  kickoff_time=datetime(2026, 4, 15, 19, 0), status=status)
        db.session.add(m)
        db.session.flush()
        if hs is not None:
            db.session.add(Score(match_id=m.id, home_score=hs, away_score=as_))

    db.session.commit()


@app.route("/")
def index():
    tours = (
        Tour.query
        .order_by(Tour.round_number.desc())
        .limit(2)
        .all()
    )
    # Show in ascending order on the page (older first)
    tours = list(reversed(tours))
    return render_template("index.html", tours=tours)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        seed_data()
    app.run(debug=True)
