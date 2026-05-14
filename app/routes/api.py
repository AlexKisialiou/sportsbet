from flask import Blueprint, jsonify, request
from ..models import db, Prediction, Match, Score, Tour, Commentary, User, Setting
from ..services.football_api import fetch_and_save_cl_matches, fetch_and_save_pl_matches
from ..services.points import update_points_for_match
from ..auth import get_current_user, login_required, admin_required, superuser_required
from ..services.activity import log_action
from ..limiter import limiter

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/cl-matches", methods=["POST"])
@limiter.limit("5 per minute")
def cl_matches():
    try:
        added, updated = fetch_and_save_cl_matches()
        return jsonify({"added": added, "updated": updated})
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/pl-matches", methods=["POST"])
@admin_required
@limiter.limit("5 per minute")
def pl_matches():
    data = request.get_json(silent=True) or {}
    clear_first = data.get("clear", False)
    try:
        if clear_first:
            from ..models import PredictionPoints, Score as ScoreModel
            pl_tour_ids = [t.id for t in Tour.query.filter_by(league="PL").all()]
            if pl_tour_ids:
                pl_match_ids = [m.id for m in Match.query.filter(Match.tour_id.in_(pl_tour_ids)).all()]
                if pl_match_ids:
                    pred_ids = [p.id for p in Prediction.query.filter(
                        Prediction.match_id.in_(pl_match_ids)).all()]
                    if pred_ids:
                        PredictionPoints.query.filter(
                            PredictionPoints.prediction_id.in_(pred_ids)).delete(synchronize_session=False)
                    Prediction.query.filter(Prediction.match_id.in_(pl_match_ids)).delete(synchronize_session=False)
                    ScoreModel.query.filter(ScoreModel.match_id.in_(pl_match_ids)).delete(synchronize_session=False)
                    Match.query.filter(Match.tour_id.in_(pl_tour_ids)).delete(synchronize_session=False)
                Tour.query.filter_by(league="PL").delete()
                db.session.commit()
        added, updated = fetch_and_save_pl_matches()
        actor = get_current_user()
        log_action(actor.id if actor else None, "pl_matches_loaded",
                   f"АПЛ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated})
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/prediction", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
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
    # Log prediction
    match = Match.query.get(match_id)
    if match:
        label = f"{match.home_team.display_name} — {match.away_team.display_name}: {home_score}:{away_score}"
    else:
        label = f"матч #{match_id}: {home_score}:{away_score}"
    log_action(user.id, "prediction_set", f"Ставка: {label}")
    return jsonify({"ok": True})


@api_bp.route("/featured-matches", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def set_featured_matches():
    data = request.get_json()
    featured_ids = set(data.get("match_ids", []))
    league = data.get("league", "UCL")
    matches = (
        Match.query.join(Tour)
        .filter(Tour.league == league, Match.status == "scheduled")
        .all()
    )
    for m in matches:
        m.featured = m.id in featured_ids
    db.session.commit()

    featured_matches = [m for m in matches if m.featured]

    from ..services.groq_api import generate_bender_pick
    from ..seed import BENDER_USERNAME

    bender = User.query.filter_by(username=BENDER_USERNAME).first()
    Commentary.query.filter(Commentary.match_label.like(f"{league}:%")).delete(synchronize_session=False)
    db.session.commit()

    for m in featured_matches:
        home = m.home_team.display_name
        away = m.away_team.display_name
        label = f"{m.tour.league}:{home} vs {away}"
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
    admin = get_current_user()
    log_action(admin.id if admin else None, "featured_set", f"Матчи для ставок: {len(featured_ids)} шт.")
    return jsonify({"ok": True, "featured": len(featured_ids)})


@api_bp.route("/simulate-results", methods=["POST"])
@superuser_required
@limiter.limit("10 per minute")
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

    # Generate Бендер standings comments per league
    try:
        from ..services.points import get_leaderboard
        from ..services.groq_api import generate_bender_standings, STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL
        from datetime import date as date_type
        from sqlalchemy import func as sqlfunc

        for league, label_key, league_name in [
            ("UCL", STANDINGS_LABEL_UCL, "ЛЧ"),
            ("PL", STANDINGS_LABEL_PL, "АПЛ"),
        ]:
            lb = get_leaderboard(league=league)
            standings_lines = [f"Турнирная таблица ({league_name}):"]
            for i, row in enumerate(lb, 1):
                standings_lines.append(f"  {i}. {row['user'].display_name} — {row['total']} очков")

            day_row = (
                db.session.query(sqlfunc.date(Match.kickoff_time))
                .join(Tour, Match.tour_id == Tour.id)
                .filter(Match.status == "finished", Tour.league == league)
                .group_by(sqlfunc.date(Match.kickoff_time))
                .order_by(sqlfunc.date(Match.kickoff_time).desc())
                .first()
            )
            if day_row:
                last_day = day_row[0]
                lb_day = get_leaderboard(last_days=[
                    date_type.fromisoformat(last_day) if isinstance(last_day, str) else last_day
                ], league=league)
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
                Commentary.query.filter_by(match_label=label_key).delete()
                db.session.add(Commentary(match_label=label_key, text=text))
                db.session.commit()
    except Exception as e:
        print(f"[groq] standings comment skipped: {e}")

    actor = get_current_user()
    log_action(actor.id if actor else None, "results_simulated", f"Симулировал {len(scheduled)} матчей")
    return jsonify({"updated": len(scheduled)})


@api_bp.route("/settings/theme", methods=["POST"])
@superuser_required
def set_theme():
    data = request.get_json(silent=True) or {}
    theme = data.get("theme", "navy")
    if theme not in ("navy", "forest", "purple", "crimson"):
        return jsonify({"error": "unknown theme"}), 400
    s = Setting.query.get("theme") or Setting(key="theme")
    s.value = theme
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "theme_changed", f"Тема: {theme}")
    return jsonify({"ok": True, "theme": theme})


@api_bp.route("/reset-scores", methods=["POST"])
@superuser_required
@limiter.limit("5 per minute")
def reset_scores():
    from ..models import PredictionPoints
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "RESET":
        return jsonify({"error": "confirm required"}), 400
    PredictionPoints.query.delete()
    Prediction.query.delete()
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "scores_reset", "Сброс всех ставок и очков")
    return jsonify({"ok": True})


@api_bp.route("/user/<int:user_id>/reset-password", methods=["POST"])
@superuser_required
def reset_user_password(user_id):
    from werkzeug.security import generate_password_hash
    user = User.query.get(user_id)
    if not user or user.is_bot:
        return jsonify({"error": "user not found"}), 404
    if user.is_superuser:
        return jsonify({"error": "cannot reset superuser password"}), 403
    user.password_hash = generate_password_hash(user.username)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "admin_password_reset", f"Сброс пароля: {user.display_name}")
    return jsonify({"ok": True})


@api_bp.route("/user/<int:user_id>/set-admin", methods=["POST"])
@superuser_required
def set_user_admin(user_id):
    data = request.get_json(silent=True) or {}
    is_admin = data.get("is_admin")
    if is_admin is None:
        return jsonify({"error": "is_admin required"}), 400
    user = User.query.get(user_id)
    if not user or user.is_bot:
        return jsonify({"error": "user not found"}), 404
    current = get_current_user()
    if user.is_superuser and not is_admin:
        return jsonify({"error": "cannot remove admin from superuser"}), 403
    if current and current.id == user.id and not is_admin:
        return jsonify({"error": "cannot remove own admin"}), 403
    user.is_admin = bool(is_admin)
    db.session.commit()
    actor = get_current_user()
    verb = "Назначил админом" if is_admin else "Снял права админа"
    log_action(actor.id if actor else None, "admin_toggle", f"{verb}: {user.display_name}")
    return jsonify({"ok": True, "user_id": user.id, "is_admin": user.is_admin})


@api_bp.route("/user/<int:user_id>/set-note", methods=["POST"])
@superuser_required
def set_user_note(user_id):
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip()[:100]
    user = User.query.get(user_id)
    if not user or user.is_bot:
        return jsonify({"error": "user not found"}), 404
    user.superadmin_note = note if note else None
    db.session.commit()
    return jsonify({"ok": True, "note": user.superadmin_note})


@api_bp.route("/user/create", methods=["POST"])
@superuser_required
def create_user():
    from werkzeug.security import generate_password_hash
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    nickname = (data.get("nickname") or "").strip() or None

    if not username:
        return jsonify({"error": "Логин обязателен"}), 400
    if len(username) < 2:
        return jsonify({"error": "Логин минимум 2 символа"}), 400
    if len(username) > 11:
        return jsonify({"error": "Логин максимум 11 символов"}), 400
    if not password or len(password) < 3:
        return jsonify({"error": "Пароль минимум 3 символа"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": f"Логин «{username}» уже занят"}), 400

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        nickname=nickname,
        is_admin=False,
        is_superuser=False,
        is_bot=False,
    )
    db.session.add(user)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "user_created", f"Создан пользователь: {user.display_name}")
    return jsonify({"ok": True, "user_id": user.id, "display_name": user.display_name,
                    "username": user.username})


@api_bp.route("/user/<int:user_id>/delete", methods=["POST"])
@superuser_required
def delete_user(user_id):
    user = User.query.get(user_id)
    if not user or user.is_bot:
        return jsonify({"error": "Пользователь не найден"}), 404
    current = get_current_user()
    if user.is_superuser:
        return jsonify({"error": "Нельзя удалить суперадмина"}), 403
    if current and current.id == user_id:
        return jsonify({"error": "Нельзя удалить себя"}), 403

    name = user.display_name
    from ..models import PredictionPoints, Prediction as Pred
    pred_ids = [p.id for p in Pred.query.filter_by(user_id=user_id).all()]
    if pred_ids:
        PredictionPoints.query.filter(PredictionPoints.prediction_id.in_(pred_ids)).delete(synchronize_session=False)
    Pred.query.filter_by(user_id=user_id).delete()
    from ..models import ActivityLog as AL
    AL.query.filter_by(user_id=user_id).update({"user_id": None})
    db.session.delete(user)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "user_deleted", f"Удалён пользователь: {name}")
    return jsonify({"ok": True})


@api_bp.route("/reset-db", methods=["POST"])
@superuser_required
@limiter.limit("3 per hour")
def reset_db():
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "RESET":
        return jsonify({"error": "confirm required"}), 400
    actor = get_current_user()
    actor_id = actor.id if actor else None
    db.drop_all()
    db.create_all()
    from ..seed import run as seed
    seed()
    log_action(actor_id, "db_reset", "Полный сброс базы данных")
    return jsonify({"ok": True})


