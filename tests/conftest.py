import os
import pytest
from unittest.mock import patch
from datetime import datetime, date
from werkzeug.security import generate_password_hash

# Must be set before importing app (load_dotenv respects pre-set vars)
_TEST_DB = os.path.join(os.path.dirname(__file__), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["SECRET_KEY"] = "test-secret"
# Override user seed vars so seed() creates only the Bender bot in tests
for _v in ("ADMIN_USERNAME", "USER1_USERNAME", "USER2_USERNAME", "USER3_USERNAME"):
    os.environ[_v] = ""


@pytest.fixture(scope="session")
def app():
    with patch("app.services.football_api.fetch_and_save_cl_matches", return_value=(0, 0)), \
         patch("app.services.football_api.fetch_and_save_pl_matches", return_value=(0, 0)), \
         patch("app.services.standings.maybe_generate_standings"):
        from app import create_app
        application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture(autouse=True)
def clean_db(app):
    from app.models import (db, User, Tour, Match, Team, Score, Prediction,
                             PredictionPoints, Commentary, Setting, ActivityLog)
    with app.app_context():
        PredictionPoints.query.delete()
        Prediction.query.delete()
        Score.query.delete()
        Match.query.delete()
        Tour.query.delete()
        Team.query.delete()
        Commentary.query.delete()
        Setting.query.delete()
        ActivityLog.query.delete()
        User.query.delete()
        db.session.commit()
        from app.seed import run as seed
        seed()
    yield


@pytest.fixture
def client(app):
    return app.test_client()


# ── helpers ──────────────────────────────────────────────────────────────────

def make_user(app, username, password="pass123", is_admin=False, is_superuser=False):
    from app.models import db, User
    with app.app_context():
        u = User(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=is_admin,
            is_superuser=is_superuser,
        )
        db.session.add(u)
        db.session.commit()
        return u.id


def make_match(app, league="UCL", kickoff=None, status="scheduled", featured=True):
    from app.models import db, Team, Tour, Match
    with app.app_context():
        t1 = Team(name="Home FC", external_id=abs(hash(f"{league}home{kickoff}")) % 100000)
        t2 = Team(name="Away FC", external_id=abs(hash(f"{league}away{kickoff}")) % 100000)
        db.session.add_all([t1, t2])
        tour = Tour(name="Test Tour", season="2024", round_number=1, league=league)
        db.session.add(tour)
        db.session.flush()
        m = Match(
            tour_id=tour.id,
            home_team_id=t1.id,
            away_team_id=t2.id,
            kickoff_time=kickoff or datetime(2030, 1, 1, 15, 0),
            status=status,
            featured=featured,
        )
        db.session.add(m)
        db.session.commit()
        return m.id


def login(client, username, password="pass123"):
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=False)
