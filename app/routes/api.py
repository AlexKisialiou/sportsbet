from datetime import datetime
from flask import Blueprint, jsonify, request, current_app
from ..models import db, Prediction, PredictionPoints, Match, Score, Tour, Commentary, User, Setting
from ..services.football_api import fetch_and_save_cl_matches, fetch_and_save_pl_matches, fetch_and_save_wc_matches
from ..services.points import update_points_for_match, calc_points
from ..auth import get_current_user, login_required, admin_required, superuser_required
from ..services.activity import log_action
from ..services.standings import maybe_generate_standings
from ..limiter import limiter

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/cl-matches", methods=["POST"])
@admin_required
@limiter.limit("5 per minute")
def cl_matches():
    try:
        added, updated = fetch_and_save_cl_matches()
        maybe_generate_standings("UCL", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "cl_matches_loaded",
                   f"ЛЧ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated})
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
        maybe_generate_standings("PL", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "pl_matches_loaded",
                   f"АПЛ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/wc-matches", methods=["POST"])
@admin_required
@limiter.limit("5 per minute")
def wc_matches():
    data = request.get_json(silent=True) or {}
    clear_first = data.get("clear", False)
    try:
        if clear_first:
            from ..models import PredictionPoints, Score as ScoreModel
            wc_tour_ids = [t.id for t in Tour.query.filter_by(league="WC").all()]
            if wc_tour_ids:
                wc_match_ids = [m.id for m in Match.query.filter(Match.tour_id.in_(wc_tour_ids)).all()]
                if wc_match_ids:
                    pred_ids = [p.id for p in Prediction.query.filter(
                        Prediction.match_id.in_(wc_match_ids)).all()]
                    if pred_ids:
                        PredictionPoints.query.filter(
                            PredictionPoints.prediction_id.in_(pred_ids)).delete(synchronize_session=False)
                    Prediction.query.filter(Prediction.match_id.in_(wc_match_ids)).delete(synchronize_session=False)
                    ScoreModel.query.filter(ScoreModel.match_id.in_(wc_match_ids)).delete(synchronize_session=False)
                    Match.query.filter(Match.tour_id.in_(wc_tour_ids)).delete(synchronize_session=False)
                Tour.query.filter_by(league="WC").delete()
                db.session.commit()
        added, updated = fetch_and_save_wc_matches()
        maybe_generate_standings("WC", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "wc_matches_loaded",
                   f"ЧМ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/prediction", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def save_prediction():
    from datetime import datetime as _dt
    data = request.get_json()
    match_id = data.get("match_id")
    home_score = data.get("home_score")
    away_score = data.get("away_score")

    if match_id is None or home_score is None or away_score is None:
        return jsonify({"error": "match_id, home_score, away_score required"}), 400

    if not isinstance(home_score, int) or not isinstance(away_score, int):
        return jsonify({"error": "scores must be integers"}), 400
    if not (0 <= home_score <= 99 and 0 <= away_score <= 99):
        return jsonify({"error": "scores must be between 0 and 99"}), 400

    lock_s = Setting.query.get("betting_locked")
    if lock_s and lock_s.value == "1":
        return jsonify({"error": "Ставки заблокированы"}), 423

    match_obj = Match.query.get(match_id)
    if not match_obj:
        return jsonify({"error": "match not found"}), 404
    if match_obj.kickoff_time and match_obj.kickoff_time <= _dt.utcnow():
        return jsonify({"error": "Матч уже начался"}), 423

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
    match = match_obj
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
    import threading
    from concurrent.futures import ThreadPoolExecutor

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

    # Extract data before thread — ORM objects must not cross session boundaries
    match_data = [
        (m.id, m.home_team.display_name, m.away_team.display_name,
         f"{league}:{m.home_team.display_name} vs {m.away_team.display_name}")
        for m in featured_matches
    ]

    admin = get_current_user()
    log_action(admin.id if admin else None, "featured_set", f"Матчи для ставок: {len(featured_ids)} шт.")

    if match_data:
        app = current_app._get_current_object()

        def generate_bender_async():
            with app.app_context():
                from ..services.groq_api import generate_bender_pick
                from ..seed import BENDER_USERNAME

                Commentary.query.filter(
                    Commentary.match_label.like(f"{league}:%")
                ).delete(synchronize_session=False)
                db.session.commit()

                bender = User.query.filter_by(username=BENDER_USERNAME).first()
                bender_id = bender.id if bender else None

                competition_names = {"UCL": "Лига Чемпионов УЕФА", "PL": "Английская Премьер-лига", "WC": "Чемпионат Мира по футболу"}
                competition = competition_names.get(league, league)

                def call_groq(item):
                    match_id, home, away, label = item
                    try:
                        result = generate_bender_pick(home, away, competition)
                        return (match_id, label, result)
                    except Exception as e:
                        print(f"[groq] bender skipped for {label}: {e}")
                        return (match_id, label, None)

                with ThreadPoolExecutor(max_workers=min(len(match_data), 5)) as executor:
                    results = list(executor.map(call_groq, match_data))

                for match_id, label, result in results:
                    if not result:
                        continue
                    hs, as_, text = result
                    if bender_id:
                        pred = Prediction.query.filter_by(
                            user_id=bender_id, match_id=match_id
                        ).first()
                        if pred:
                            pred.home_score = hs
                            pred.away_score = as_
                        else:
                            db.session.add(Prediction(
                                user_id=bender_id, match_id=match_id,
                                home_score=hs, away_score=as_,
                            ))
                    db.session.add(Commentary(match_label=label, text=f"{text} Ставлю {hs}:{as_}."))

                db.session.commit()

        threading.Thread(target=generate_bender_async, daemon=True).start()

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
        from ..services.groq_api import generate_bender_standings, STANDINGS_LABEL_UCL, STANDINGS_LABEL_PL, STANDINGS_LABEL_WC
        from datetime import date as date_type
        from sqlalchemy import func as sqlfunc

        for league, label_key, league_name in [
            ("UCL", STANDINGS_LABEL_UCL, "ЛЧ"),
            ("PL", STANDINGS_LABEL_PL, "АПЛ"),
            ("WC", STANDINGS_LABEL_WC, "ЧМ"),
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
    theme = data.get("theme", "purple")
    if theme not in ("purple", "ucl"):
        return jsonify({"error": "unknown theme"}), 400
    s = Setting.query.get("theme") or Setting(key="theme")
    s.value = theme
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "theme_changed", f"Тема: {theme}")
    return jsonify({"ok": True, "theme": theme})


@api_bp.route("/settings/betting-lock", methods=["POST"])
@superuser_required
def set_betting_lock():
    data = request.get_json(silent=True) or {}
    locked = data.get("locked")
    if locked is None:
        return jsonify({"error": "locked required"}), 400
    s = Setting.query.get("betting_locked") or Setting(key="betting_locked")
    s.value = "1" if locked else "0"
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "betting_lock",
               "Ставки заблокированы" if locked else "Ставки разблокированы")
    return jsonify({"ok": True, "locked": locked})


@api_bp.route("/settings/league-enabled", methods=["POST"])
@superuser_required
def set_league_enabled():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    enabled = data.get("enabled")
    if league not in ("UCL", "PL", "WC"):
        return jsonify({"error": "unknown league"}), 400
    if enabled is None:
        return jsonify({"error": "enabled required"}), 400
    s = Setting.query.get(f"league_enabled_{league}") or Setting(key=f"league_enabled_{league}")
    s.value = "1" if enabled else "0"
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "league_toggled",
               f"Лига {league}: {'включена' if enabled else 'выключена'}")
    return jsonify({"ok": True, "league": league, "enabled": enabled})


@api_bp.route("/settings/pred-days", methods=["POST"])
@superuser_required
def set_pred_days():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    days = data.get("days")
    if league not in ("UCL", "PL", "WC"):
        return jsonify({"error": "unknown league"}), 400
    if not isinstance(days, int) or not (1 <= days <= 20):
        return jsonify({"error": "days must be integer 1–20"}), 400
    s = Setting.query.get(f"pred_days_{league}") or Setting(key=f"pred_days_{league}")
    s.value = str(days)
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "pred_days_changed",
               f"Кол-во дней {league}: {days}")
    return jsonify({"ok": True, "league": league, "days": days})


@api_bp.route("/settings/league-order", methods=["POST"])
@superuser_required
def set_league_order():
    data = request.get_json(silent=True) or {}
    order = data.get("order", [])
    valid = [lg for lg in order if lg in ("UCL", "PL", "WC")]
    if len(valid) != 3:
        return jsonify({"error": "order must contain UCL, PL, WC"}), 400
    s = Setting.query.get("league_order") or Setting(key="league_order")
    s.value = ",".join(valid)
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "league_order_changed", f"Порядок лиг: {s.value}")
    return jsonify({"ok": True, "order": valid})


@api_bp.route("/admin/translate-teams-ru", methods=["POST"])
@superuser_required
def translate_teams_ru():
    from ..models import Team as TeamModel
    from ..services.groq_api import translate_team_names
    teams = TeamModel.query.filter(
        (TeamModel.name_ru == None) | (TeamModel.name_ru == "")
    ).all()
    if not teams:
        return jsonify({"ok": True, "updated": 0, "failed": []})

    BATCH = 30
    all_translations = {}
    names = [t.name for t in teams]
    for i in range(0, len(names), BATCH):
        try:
            all_translations.update(translate_team_names(names[i:i + BATCH]))
        except Exception as e:
            print(f"[groq] translate batch error: {e}")

    updated, failed = 0, []
    for team in teams:
        ru = all_translations.get(team.name)
        if ru:
            team.name_ru = ru
            updated += 1
        else:
            failed.append(team.name)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "teams_ru_translated",
               f"Groq перевёл: {updated}, не удалось: {len(failed)}")
    return jsonify({"ok": True, "updated": updated, "failed": sorted(failed)})


@api_bp.route("/admin/apply-teams-ru", methods=["POST"])
@superuser_required
def apply_teams_ru():
    from ..models import Team as TeamModel
    from ..data.teams_ru import TEAMS_RU
    teams = TeamModel.query.all()
    updated = 0
    missing = []
    for team in teams:
        ru = TEAMS_RU.get(team.name)
        if ru:
            if team.name_ru != ru:
                team.name_ru = ru
                updated += 1
        else:
            if not team.name_ru:
                missing.append(team.name)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "teams_ru_applied",
               f"Русские названия: обновлено {updated}, без перевода {len(missing)}")
    return jsonify({"ok": True, "updated": updated, "missing": sorted(missing)})


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
    actor = get_current_user()
    log_action(actor.id if actor else None, "note_set",
               f"Заметка для {user.display_name}: «{note}»" if note else f"Заметка удалена у {user.display_name}")
    return jsonify({"ok": True, "note": user.superadmin_note})


@api_bp.route("/user/<int:user_id>/revoke-superuser", methods=["POST"])
@superuser_required
def revoke_superuser(user_id):
    current = get_current_user()
    if current and current.id == user_id:
        return jsonify({"error": "Нельзя снять права у себя"}), 403
    user = User.query.get(user_id)
    if not user or user.is_bot:
        return jsonify({"error": "Пользователь не найден"}), 404
    if not user.is_superuser:
        return jsonify({"error": "Пользователь не является суперадмином"}), 400
    user.is_superuser = False
    db.session.commit()
    log_action(current.id if current else None, "superuser_revoked",
               f"Снял права суперадмина: {user.display_name}")
    return jsonify({"ok": True})


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
        return jsonify({"error": "Сначала снимите права суперадмина"}), 403
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


@api_bp.route("/admin/match/<int:match_id>/score", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def set_match_score(match_id):
    data = request.get_json(silent=True) or {}
    home_score = data.get("home_score")
    away_score = data.get("away_score")
    if not isinstance(home_score, int) or not isinstance(away_score, int):
        return jsonify({"error": "home_score и away_score обязательны (целые числа)"}), 400
    if not (0 <= home_score <= 99 and 0 <= away_score <= 99):
        return jsonify({"error": "Счёт должен быть от 0 до 99"}), 400
    match = Match.query.get(match_id)
    if not match:
        return jsonify({"error": "Матч не найден"}), 404
    match.status = "finished"
    if match.score:
        match.score.home_score = home_score
        match.score.away_score = away_score
        match.score.manual_lock = True
        match.score.updated_at = datetime.utcnow()
    else:
        db.session.add(Score(match_id=match_id, home_score=home_score, away_score=away_score,
                             manual_lock=True))
    db.session.flush()
    db.session.refresh(match)
    update_points_for_match(match)
    # Auto-lock all calculated prediction points for this match
    for pred in Prediction.query.filter_by(match_id=match_id).all():
        if pred.result and not pred.result.manual_lock:
            pred.result.manual_lock = True
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "score_manual_set",
               f"{match.home_team.display_name} {home_score}:{away_score} {match.away_team.display_name}")
    return jsonify({"ok": True})


@api_bp.route("/admin/match/<int:match_id>/score/clear", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def clear_match_score(match_id):
    match = Match.query.get(match_id)
    if not match:
        return jsonify({"error": "Матч не найден"}), 404
    if match.score:
        db.session.delete(match.score)
    match.status = "scheduled"
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "score_manual_cleared",
               f"{match.home_team.display_name} vs {match.away_team.display_name}")
    return jsonify({"ok": True})


@api_bp.route("/admin/match/<int:match_id>/predictions", methods=["GET"])
@superuser_required
def get_match_predictions(match_id):
    match = Match.query.get(match_id)
    if not match:
        return jsonify({"error": "Матч не найден"}), 404
    preds = Prediction.query.filter_by(match_id=match_id).all()
    result = []
    for pred in preds:
        result.append({
            "id": pred.id,
            "user_id": pred.user_id,
            "user_name": pred.user.display_name if pred.user else "?",
            "home_score": pred.home_score,
            "away_score": pred.away_score,
            "points": pred.result.points if pred.result else None,
            "reason": pred.result.reason if pred.result else None,
            "manual_lock": pred.result.manual_lock if pred.result else False,
        })
    return jsonify({
        "match_id": match_id,
        "score_locked": match.score.manual_lock if match.score else False,
        "predictions": result,
    })


@api_bp.route("/admin/prediction/<int:prediction_id>/points", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def set_prediction_points(prediction_id):
    data = request.get_json(silent=True) or {}
    points = data.get("points")
    if points not in (0, 1, 3):
        return jsonify({"error": "points должен быть 0, 1 или 3"}), 400
    pred = Prediction.query.get(prediction_id)
    if not pred:
        return jsonify({"error": "Прогноз не найден"}), 404
    reason_map = {0: "none", 1: "winner", 3: "exact"}
    reason = reason_map[points]
    if pred.result:
        pred.result.points = points
        pred.result.reason = reason
        pred.result.manual_lock = True
        pred.result.calculated_at = datetime.utcnow()
    else:
        db.session.add(PredictionPoints(
            prediction_id=pred.id,
            points=points,
            reason=reason,
            manual_lock=True,
        ))
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "points_manual_set",
               f"Прогноз #{pred.id}: {points} очков")
    return jsonify({"ok": True})


@api_bp.route("/admin/prediction/<int:prediction_id>/points/lock", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def lock_prediction_points(prediction_id):
    pred = Prediction.query.get(prediction_id)
    if not pred:
        return jsonify({"error": "Прогноз не найден"}), 404
    if not pred.result:
        return jsonify({"error": "Очки ещё не рассчитаны"}), 400
    pred.result.manual_lock = True
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "points_manual_set",
               f"Прогноз #{pred.id}: заблокировано ({pred.result.points} очков)")
    return jsonify({"ok": True})


@api_bp.route("/admin/prediction/<int:prediction_id>/points/unlock", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def unlock_prediction_points(prediction_id):
    pred = Prediction.query.get(prediction_id)
    if not pred:
        return jsonify({"error": "Прогноз не найден"}), 404
    if pred.result:
        pred.result.manual_lock = False
        match = Match.query.get(pred.match_id)
        if match and match.score and match.status == "finished":
            pts, rsn = calc_points(pred.home_score, pred.away_score,
                                   match.score.home_score, match.score.away_score)
            pred.result.points = pts
            pred.result.reason = rsn
            pred.result.calculated_at = datetime.utcnow()
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "points_manual_unlocked",
               f"Прогноз #{pred.id}: разблокировано")
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


