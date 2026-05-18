import json
import pytest
from datetime import datetime, timedelta
from .conftest import make_user, make_match, login


def test_prediction_requires_login(app, client):
    match_id = make_match(app)
    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": 1, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 302
    assert "/login" in resp.location


def test_prediction_save(app, client):
    uid = make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": 2, "away_score": 1}),
                       content_type="application/json")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    from app.models import Prediction
    with app.app_context():
        pred = Prediction.query.filter_by(user_id=uid, match_id=match_id).first()
        assert pred is not None
        assert pred.home_score == 2
        assert pred.away_score == 1


def test_prediction_update(app, client):
    uid = make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    login(client, "alice")

    client.post("/api/prediction",
                data=json.dumps({"match_id": match_id, "home_score": 1, "away_score": 0}),
                content_type="application/json")
    client.post("/api/prediction",
                data=json.dumps({"match_id": match_id, "home_score": 3, "away_score": 2}),
                content_type="application/json")

    from app.models import Prediction
    with app.app_context():
        preds = Prediction.query.filter_by(user_id=uid, match_id=match_id).all()
        assert len(preds) == 1
        assert preds[0].home_score == 3
        assert preds[0].away_score == 2


def test_prediction_betting_locked(app, client):
    make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))

    from app.models import db, Setting
    with app.app_context():
        db.session.add(Setting(key="betting_locked", value="1"))
        db.session.commit()

    login(client, "alice")
    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": 1, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 423


def test_prediction_match_already_started(app, client):
    make_user(app, "alice")
    past_kickoff = datetime.utcnow() - timedelta(minutes=5)
    match_id = make_match(app, kickoff=past_kickoff, status="scheduled")
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": 1, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 423


def test_prediction_invalid_score_negative(app, client):
    make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": -1, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_prediction_invalid_score_too_large(app, client):
    make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": 100, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_prediction_invalid_score_non_int(app, client):
    make_user(app, "alice")
    match_id = make_match(app, kickoff=datetime(2030, 1, 1, 15, 0))
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": match_id, "home_score": "two", "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_prediction_match_not_found(app, client):
    make_user(app, "alice")
    login(client, "alice")

    resp = client.post("/api/prediction",
                       data=json.dumps({"match_id": 99999, "home_score": 1, "away_score": 0}),
                       content_type="application/json")
    assert resp.status_code == 404
