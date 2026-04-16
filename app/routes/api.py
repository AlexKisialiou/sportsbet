from flask import Blueprint, jsonify, request
from ..models import db, Prediction, Match, Score
from ..services.football_api import fetch_and_save_cl_matches
from ..services.points import update_points_for_match
from ..auth import get_current_user, login_required

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/cl-matches", methods=["POST"])
def cl_matches():
    try:
        added, updated = fetch_and_save_cl_matches()
        return jsonify({"added": added, "updated": updated})
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/prediction", methods=["POST"])
@login_required
def save_prediction():
    data = request.get_json()
    match_id = data.get("match_id")
    home_score = data.get("home_score")
    away_score = data.get("away_score")

    if match_id is None or home_score is None or away_score is None:
        return jsonify({"error": "match_id, home_score, away_score required"}), 400

    user = get_current_user()
    if not user:
        return jsonify({"error": "not authenticated"}), 401

    prediction = Prediction.query.filter_by(user_id=user.id, match_id=match_id).first()
    if prediction:
        prediction.home_score = home_score
        prediction.away_score = away_score
    else:
        prediction = Prediction(user_id=user.id, match_id=match_id,
                                home_score=home_score, away_score=away_score)
        db.session.add(prediction)

    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/simulate-results", methods=["POST"])
def simulate_results():
    """DEV ONLY: generate random scores for all scheduled matches and mark as finished."""
    scheduled = Match.query.filter_by(status="scheduled").all()
    if not scheduled:
        return jsonify({"updated": 0, "message": "Нет матчей со статусом scheduled"})

    for match in scheduled:
        hs = 1
        as_ = 0
        match.status = "finished"
        if match.score:
            match.score.home_score = hs
            match.score.away_score = as_
        else:
            db.session.add(Score(match_id=match.id, home_score=hs, away_score=as_))
        db.session.flush()
        db.session.refresh(match)  # reload score relationship after flush
        update_points_for_match(match, commit=False)

    db.session.commit()
    return jsonify({"updated": len(scheduled)})
