from flask import Blueprint, jsonify
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
