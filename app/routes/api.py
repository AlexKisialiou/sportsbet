from flask import Blueprint, jsonify, request
from ..models import db, Prediction, User
from ..services.football_api import fetch_and_save_cl_matches

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
def save_prediction():
    data = request.get_json()
    match_id = data.get("match_id")
    home_score = data.get("home_score")
    away_score = data.get("away_score")

    if match_id is None or home_score is None or away_score is None:
        return jsonify({"error": "match_id, home_score, away_score required"}), 400

    user = User.query.filter_by(username="test").first()
    if not user:
        return jsonify({"error": "user not found"}), 500

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
