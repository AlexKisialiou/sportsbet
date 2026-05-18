import pytest
from datetime import datetime, date
from werkzeug.security import generate_password_hash
from .conftest import make_user, make_match


def test_calc_points_exact():
    from app.services.points import calc_points
    pts, reason = calc_points(2, 1, 2, 1)
    assert pts == 3
    assert reason == "exact"


def test_calc_points_correct_winner():
    from app.services.points import calc_points
    pts, reason = calc_points(3, 1, 2, 0)
    assert pts == 1
    assert reason == "winner"


def test_calc_points_correct_draw():
    from app.services.points import calc_points
    pts, reason = calc_points(1, 1, 0, 0)
    assert pts == 1
    assert reason == "winner"


def test_calc_points_exact_draw():
    from app.services.points import calc_points
    pts, reason = calc_points(0, 0, 0, 0)
    assert pts == 3
    assert reason == "exact"


def test_calc_points_wrong():
    from app.services.points import calc_points
    pts, reason = calc_points(1, 0, 0, 2)
    assert pts == 0
    assert reason == "none"


def test_get_leaderboard_totals(app):
    from app.models import db, User, Team, Tour, Match, Prediction, PredictionPoints
    from app.services.points import get_leaderboard

    with app.app_context():
        u1 = User(username="alice", password_hash=generate_password_hash("p"))
        u2 = User(username="bob", password_hash=generate_password_hash("p"))
        db.session.add_all([u1, u2])

        t1 = Team(name="Arsenal", external_id=9001)
        t2 = Team(name="Chelsea", external_id=9002)
        db.session.add_all([t1, t2])

        tour = Tour(name="T", season="2024", round_number=1, league="UCL")
        db.session.add(tour)
        db.session.flush()

        m = Match(tour_id=tour.id, home_team_id=t1.id, away_team_id=t2.id,
                  kickoff_time=datetime(2025, 5, 10, 15, 0), status="finished", featured=True)
        db.session.add(m)
        db.session.flush()

        p1 = Prediction(user_id=u1.id, match_id=m.id, home_score=2, away_score=1)
        p2 = Prediction(user_id=u2.id, match_id=m.id, home_score=0, away_score=0)
        db.session.add_all([p1, p2])
        db.session.flush()

        db.session.add(PredictionPoints(prediction_id=p1.id, points=3, reason="exact"))
        db.session.add(PredictionPoints(prediction_id=p2.id, points=0, reason="none"))
        db.session.commit()

        lb = get_leaderboard(league="UCL")
        totals = {row["user"].username: row["total"] for row in lb}

        assert totals["alice"] == 3
        assert totals["bob"] == 0
        assert lb[0]["user"].username == "alice"


def test_get_leaderboard_per_day(app):
    from app.models import db, User, Team, Tour, Match, Prediction, PredictionPoints
    from app.services.points import get_leaderboard

    with app.app_context():
        u1 = User(username="alice", password_hash=generate_password_hash("p"))
        u2 = User(username="bob", password_hash=generate_password_hash("p"))
        db.session.add_all([u1, u2])

        t1 = Team(name="AC Milan", external_id=9003)
        t2 = Team(name="Inter", external_id=9004)
        db.session.add_all([t1, t2])

        tour = Tour(name="T", season="2024", round_number=1, league="UCL")
        db.session.add(tour)
        db.session.flush()

        day1 = date(2025, 5, 10)
        day2 = date(2025, 5, 17)

        m1 = Match(tour_id=tour.id, home_team_id=t1.id, away_team_id=t2.id,
                   kickoff_time=datetime(2025, 5, 10, 15, 0), status="finished", featured=True)
        m2 = Match(tour_id=tour.id, home_team_id=t2.id, away_team_id=t1.id,
                   kickoff_time=datetime(2025, 5, 17, 20, 0), status="finished", featured=True)
        db.session.add_all([m1, m2])
        db.session.flush()

        # alice: 3pts day1, 1pt day2
        p1 = Prediction(user_id=u1.id, match_id=m1.id, home_score=1, away_score=0)
        p2 = Prediction(user_id=u1.id, match_id=m2.id, home_score=2, away_score=0)
        # bob: 0pts day1, 3pts day2
        p3 = Prediction(user_id=u2.id, match_id=m1.id, home_score=0, away_score=0)
        p4 = Prediction(user_id=u2.id, match_id=m2.id, home_score=0, away_score=1)
        db.session.add_all([p1, p2, p3, p4])
        db.session.flush()

        db.session.add(PredictionPoints(prediction_id=p1.id, points=3, reason="exact"))
        db.session.add(PredictionPoints(prediction_id=p2.id, points=1, reason="winner"))
        db.session.add(PredictionPoints(prediction_id=p3.id, points=0, reason="none"))
        db.session.add(PredictionPoints(prediction_id=p4.id, points=3, reason="exact"))
        db.session.commit()

        lb = get_leaderboard(last_days=[day1, day2], league="UCL")
        rows = {row["user"].username: row for row in lb}

        assert rows["alice"]["total"] == 4
        assert rows["bob"]["total"] == 3
        assert rows["alice"]["days"][0] == {"pts": 3, "has_pred": True}
        assert rows["alice"]["days"][1] == {"pts": 1, "has_pred": True}
        assert rows["bob"]["days"][0] == {"pts": 0, "has_pred": True}
        assert rows["bob"]["days"][1] == {"pts": 3, "has_pred": True}


def test_update_points_for_match(app):
    from app.models import db, User, Team, Tour, Match, Score, Prediction, PredictionPoints
    from app.services.points import update_points_for_match

    with app.app_context():
        u = User(username="charlie", password_hash=generate_password_hash("p"))
        db.session.add(u)
        t1 = Team(name="Bayern", external_id=9005)
        t2 = Team(name="Dortmund", external_id=9006)
        db.session.add_all([t1, t2])
        tour = Tour(name="T", season="2024", round_number=1, league="UCL")
        db.session.add(tour)
        db.session.flush()

        m = Match(tour_id=tour.id, home_team_id=t1.id, away_team_id=t2.id,
                  kickoff_time=datetime(2025, 5, 10, 15, 0), status="finished", featured=True)
        db.session.add(m)
        db.session.flush()

        score = Score(match_id=m.id, home_score=2, away_score=1)
        db.session.add(score)

        pred = Prediction(user_id=u.id, match_id=m.id, home_score=2, away_score=1)
        db.session.add(pred)
        db.session.commit()

        count = update_points_for_match(m)
        assert count == 1

        pp = PredictionPoints.query.filter_by(prediction_id=pred.id).first()
        assert pp is not None
        assert pp.points == 3
        assert pp.reason == "exact"
