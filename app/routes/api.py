from flask import Blueprint, jsonify, request
from ..models import db, Prediction, Match, Score, Tour, Commentary, User, Setting
from ..services.football_api import fetch_and_save_cl_matches
from ..services.points import update_points_for_match
from ..auth import get_current_user, login_required, admin_required

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


@api_bp.route("/featured-matches", methods=["POST"])
@admin_required
def set_featured_matches():
    data = request.get_json()
    featured_ids = set(data.get("match_ids", []))
    matches = (
        Match.query.join(Tour)
        .filter(Tour.league == "UCL", Match.status == "scheduled")
        .all()
    )
    for m in matches:
        m.featured = m.id in featured_ids
    db.session.commit()

    featured_matches = [m for m in matches if m.featured]

    from ..services.groq_api import generate_bender_pick
    from ..seed import BENDER_USERNAME

    bender = User.query.filter_by(username=BENDER_USERNAME).first()
    Commentary.query.delete()
    db.session.commit()

    for m in featured_matches:
        home = m.home_team.display_name
        away = m.away_team.display_name
        label = f"{home} vs {away}"
        try:
            result = generate_bender_pick(home, away)
            if result:
                hs, as_, text = result
                # Save Бендер's prediction
                if bender:
                    pred = Prediction.query.filter_by(user_id=bender.id, match_id=m.id).first()
                    if pred:
                        pred.home_score = hs
                        pred.away_score = as_
                    else:
                        db.session.add(Prediction(
                            user_id=bender.id, match_id=m.id,
                            home_score=hs, away_score=as_,
                        ))
                db.session.add(Commentary(match_label=label, text=f"{text} Ставлю {hs}:{as_}."))
        except Exception as e:
            print(f"[groq] bender skipped for {label}: {e}")

    db.session.commit()
    return jsonify({"ok": True, "featured": len(featured_ids)})


@api_bp.route("/simulate-results", methods=["POST"])
@admin_required
def simulate_results():
    data = request.get_json(silent=True) or {}
    match_ids = data.get("match_ids")

    if match_ids:
        scheduled = Match.query.filter(
            Match.id.in_(match_ids), Match.status == "scheduled"
        ).all()
    else:
        scheduled = Match.query.filter_by(status="scheduled").all()

    if not scheduled:
        return jsonify({"updated": 0})

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

    # Generate Бендер standings comment
    try:
        from ..services.points import get_leaderboard
        from ..services.groq_api import generate_bender_standings, STANDINGS_LABEL
        from datetime import date as date_type
        from sqlalchemy import func as sqlfunc

        lb = get_leaderboard()
        standings_lines = ["Турнирная таблица:"]
        for i, row in enumerate(lb, 1):
            standings_lines.append(f"  {i}. {row['user'].display_name} — {row['total']} очков")

        # Last game day
        day_row = (
            db.session.query(sqlfunc.date(Match.kickoff_time))
            .filter(Match.status == "finished")
            .group_by(sqlfunc.date(Match.kickoff_time))
            .order_by(sqlfunc.date(Match.kickoff_time).desc())
            .first()
        )
        if day_row:
            last_day = day_row[0]
            lb_day = get_leaderboard(last_days=[
                date_type.fromisoformat(last_day) if isinstance(last_day, str) else last_day
            ])
            standings_lines.append(f"\nПоследний игровой день ({last_day}):")
            for row in lb_day:
                d = row["days"][0] if row["days"] else {"pts": 0, "has_pred": False}
                if d["has_pred"]:
                    standings_lines.append(f"  {row['user'].display_name}: +{d['pts']}")
                else:
                    standings_lines.append(f"  {row['user'].display_name}: не ставил")

        standings_text = "\n".join(standings_lines)
        text = generate_bender_standings(standings_text)
        if text:
            Commentary.query.filter_by(match_label=STANDINGS_LABEL).delete()
            db.session.add(Commentary(match_label=STANDINGS_LABEL, text=text))
            db.session.commit()
    except Exception as e:
        print(f"[groq] standings comment skipped: {e}")

    return jsonify({"updated": len(scheduled)})


