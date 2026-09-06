import os
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request, current_app
from ..models import db, Prediction, PredictionPoints, Match, Score, Tour, Commentary, User, Setting, ReleaseNote, Team, MatchComment, CommentRead, HofEntry
from ..services.football_api import fetch_and_save_cl_matches, fetch_and_save_pl_matches, fetch_and_save_wc_matches, fetch_and_save_ucl2627_matches
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
        added, updated, existing = fetch_and_save_cl_matches()
        maybe_generate_standings("UCL", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "cl_matches_loaded",
                   f"ЛЧ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated, "existing": existing})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/ucl2627-matches", methods=["POST"])
@admin_required
@limiter.limit("5 per minute")
def ucl2627_matches():
    try:
        added, updated, existing = fetch_and_save_ucl2627_matches()
        maybe_generate_standings("UCL2627", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "ucl2627_matches_loaded",
                   f"ЛЧ 26/27 матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated, "existing": existing})
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
        added, updated, existing = fetch_and_save_pl_matches()
        maybe_generate_standings("PL", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "pl_matches_loaded",
                   f"АПЛ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated, "existing": existing})
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
        added, updated, existing = fetch_and_save_wc_matches()
        maybe_generate_standings("WC", current_app._get_current_object())
        actor = get_current_user()
        log_action(actor.id if actor else None, "wc_matches_loaded",
                   f"ЧМ матчи загружены: +{added} новых, {updated} обновлено")
        return jsonify({"added": added, "updated": updated, "existing": existing})
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
    data = request.get_json()
    featured_ids = set(data.get("match_ids", []))
    league = data.get("league", "UCL")
    round_map = {int(k): int(v) for k, v in data.get("round_map", {}).items() if str(v).isdigit()}
    matches = (
        Match.query.join(Tour)
        .filter(Tour.league == league, Match.status == "scheduled")
        .all()
    )
    for m in matches:
        m.featured = m.id in featured_ids
        if m.id in featured_ids:
            m.featured_round = round_map.get(m.id) or None
        else:
            m.featured_round = None
    db.session.commit()

    # Manual save — lock auto-featured so scheduler won't override until matches finish
    lock_row = Setting.query.get(f"featured_manual_lock_{league}") or Setting(key=f"featured_manual_lock_{league}", value="0")
    lock_row.value = "1"
    db.session.merge(lock_row)
    # Обновляем fingerprint под новый набор, чтобы скедулер не дублировал запуск
    import hashlib
    ids_str = ",".join(str(i) for i in sorted(featured_ids))
    fp_row = Setting.query.get(f"bender_fp_{league}") or Setting(key=f"bender_fp_{league}")
    fp_row.value = hashlib.md5(ids_str.encode()).hexdigest()
    db.session.merge(fp_row)
    db.session.commit()

    # Запускаем Бендера сразу только для активного дня — остальные подтянет скедулер
    if featured_ids:
        from ..services.auto_featured import run_bender_for_league
        run_bender_for_league(current_app._get_current_object(), league, active_only=True)

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
    standings_s = Setting.query.get("bender_standings_enabled")
    if standings_s is None or standings_s.value != "1":
        actor = get_current_user()
        log_action(actor.id if actor else None, "results_simulated", f"Симулировал {len(scheduled)} матчей")
        return jsonify({"updated": len(scheduled)})

    try:
        from ..services.points import get_leaderboard
        from ..services.groq_api import (generate_bender_standings,
                                        STANDINGS_LABEL_UCL, STANDINGS_LABEL_UCL2627,
                                        STANDINGS_LABEL_PL, STANDINGS_LABEL_WC)

        from ..seed import LEAGUE_TO_TOURNAMENT
        for league, label_key, league_name in [
            ("UCL", STANDINGS_LABEL_UCL, "ЛЧ"),
            ("UCL2627", STANDINGS_LABEL_UCL2627, "ЛЧ 26/27"),
            ("PL", STANDINGS_LABEL_PL, "АПЛ"),
            ("WC", STANDINGS_LABEL_WC, "ЧМ"),
        ]:
            lb = get_leaderboard(league=league)
            standings_lines = [f"Турнирная таблица ({league_name}):"]
            for i, row in enumerate(lb, 1):
                standings_lines.append(f"  {i}. {row['user'].display_name} — {row['total']} очков")

            round_row = (
                db.session.query(Match.featured_round)
                .join(Tour, Match.tour_id == Tour.id)
                .filter(Match.status == "finished", Tour.league == league,
                        Match.featured_round.isnot(None))
                .order_by(Match.featured_round.desc())
                .first()
            )
            if round_row:
                last_round = round_row[0]
                lb_round = get_leaderboard(last_rounds=[last_round], league=league)
                standings_lines.append(f"\nИгровой день {last_round}:")
                for row in lb_round:
                    d = row["days"][0] if row["days"] else {"pts": 0, "has_pred": False}
                    if d["has_pred"]:
                        standings_lines.append(f"  {row['user'].display_name}: +{d['pts']}")
                    else:
                        standings_lines.append(f"  {row['user'].display_name}: не ставил")

            standings_text = "\n".join(standings_lines)
            text = generate_bender_standings(standings_text, tournament=LEAGUE_TO_TOURNAMENT.get(league, league))
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


@api_bp.route("/settings/reveal-live-predictions", methods=["POST"])
@admin_required
def set_reveal_live_predictions():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"error": "enabled required"}), 400
    s = Setting.query.get("reveal_live_predictions") or Setting(key="reveal_live_predictions")
    s.value = "1" if enabled else "0"
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "reveal_live_predictions",
               "Ставки во время матча: показываются" if enabled else "Ставки во время матча: скрыты")
    return jsonify({"ok": True, "enabled": enabled})


@api_bp.route("/settings/league-enabled", methods=["POST"])
@superuser_required
def set_league_enabled():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    enabled = data.get("enabled")
    if league not in ("UCL", "UCL2627", "PL", "WC"):
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
    if league not in ("UCL", "UCL2627", "PL", "WC"):
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
    valid = [lg for lg in order if lg in ("UCL", "UCL2627", "PL", "WC")]
    if len(valid) < 1:
        return jsonify({"error": "order must contain valid league codes"}), 400
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


@api_bp.route("/admin/fix-round0", methods=["POST"])
@superuser_required
def fix_round0():
    data = request.get_json(silent=True) or {}
    league = data.get("league", "WC")
    if league not in ("UCL", "UCL2627", "PL", "WC"):
        return jsonify({"error": "invalid league"}), 400
    matches = (
        Match.query.join(Tour)
        .filter(
            Tour.league == league,
            Match.status == "finished",
            Match.featured_round.is_(None),
        )
        .all()
    )
    count = 0
    for m in matches:
        m.featured = True
        m.featured_round = 0
        count += 1
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "admin_fix",
               f"Перенос в день 0 [{league}]: {count} матчей")
    return jsonify({"ok": True, "updated": count})


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


@api_bp.route("/admin/release-notes", methods=["GET"])
@superuser_required
def list_release_notes():
    notes = ReleaseNote.query.order_by(ReleaseNote.deployed_at.desc()).all()
    return jsonify([{
        "id": n.id, "version": n.version, "content": n.content,
        "deployed_at": n.deployed_at.strftime("%Y-%m-%dT%H:%M"),
        "active": n.active,
    } for n in notes])


@api_bp.route("/admin/release-notes", methods=["POST"])
@superuser_required
def create_release_note():
    data = request.get_json(silent=True) or {}
    version = (data.get("version") or "").strip()
    content = (data.get("content") or "").strip()
    deployed_at_str = (data.get("deployed_at") or "").strip()
    if not version or not content:
        return jsonify({"error": "version и content обязательны"}), 400
    try:
        deployed_at = datetime.strptime(deployed_at_str, "%Y-%m-%dT%H:%M") if deployed_at_str else datetime.utcnow()
    except ValueError:
        return jsonify({"error": "Неверный формат даты (ожидается YYYY-MM-DDTHH:MM)"}), 400
    note = ReleaseNote(version=version, content=content, deployed_at=deployed_at, active=True)
    db.session.add(note)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "release_note_created", f"Создана заметка релиза: {version}")
    return jsonify({"ok": True, "id": note.id})


@api_bp.route("/admin/release-notes/<int:note_id>", methods=["POST"])
@superuser_required
def update_release_note(note_id):
    note = ReleaseNote.query.get(note_id)
    if not note:
        return jsonify({"error": "Не найдено"}), 404
    data = request.get_json(silent=True) or {}
    if "version" in data:
        note.version = (data["version"] or "").strip()
    if "content" in data:
        note.content = (data["content"] or "").strip()
    if "active" in data:
        note.active = bool(data["active"])
    if "deployed_at" in data:
        try:
            note.deployed_at = datetime.strptime(data["deployed_at"], "%Y-%m-%dT%H:%M")
        except ValueError:
            return jsonify({"error": "Неверный формат даты"}), 400
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "release_note_updated", f"Обновлена заметка релиза: {note.version}")
    return jsonify({"ok": True})


@api_bp.route("/admin/release-notes/<int:note_id>/delete", methods=["POST"])
@superuser_required
def delete_release_note(note_id):
    note = ReleaseNote.query.get(note_id)
    if not note:
        return jsonify({"error": "Не найдено"}), 404
    version = note.version
    db.session.delete(note)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "release_note_deleted", f"Удалена заметка релиза: {version}")
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


@api_bp.route("/admin/match/<int:match_id>/set-live", methods=["POST"])
@superuser_required
@limiter.limit("60 per minute")
def set_match_live(match_id):
    data = request.get_json(silent=True) or {}
    live = data.get("live", True)
    match = Match.query.get(match_id)
    if not match:
        return jsonify({"error": "Матч не найден"}), 404
    if live:
        match.status = "live"
        db.session.commit()
        actor = get_current_user()
        log_action(actor.id if actor else None, "match_live_set",
                   f"{match.home_team.display_name} vs {match.away_team.display_name}")
    else:
        match.status = "scheduled"
        db.session.commit()
        actor = get_current_user()
        log_action(actor.id if actor else None, "match_live_cleared",
                   f"{match.home_team.display_name} vs {match.away_team.display_name}")
    return jsonify({"ok": True, "status": match.status})


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


@api_bp.route("/admin/prompt-hints", methods=["GET"])
@superuser_required
def list_prompt_hints():
    from ..models import PromptHint
    tournament = request.args.get("tournament", "WC2026")
    hints = PromptHint.query.filter_by(tournament=tournament).order_by(PromptHint.sort_order).all()
    return jsonify([{
        "id": h.id, "tournament": h.tournament, "hint_type": h.hint_type,
        "content": h.content, "active": h.active, "sort_order": h.sort_order,
    } for h in hints])


@api_bp.route("/admin/prompt-hints/<int:hint_id>", methods=["POST"])
@superuser_required
@limiter.limit("30 per minute")
def update_prompt_hint(hint_id):
    from ..models import PromptHint
    hint = PromptHint.query.get(hint_id)
    if not hint:
        return jsonify({"error": "Хинт не найден"}), 404
    data = request.get_json(silent=True) or {}
    if "content" in data:
        hint.content = (data["content"] or "").strip()
    if "active" in data:
        hint.active = bool(data["active"])
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "prompt_hint_updated",
               f"{hint.tournament}/{hint.hint_type}: active={hint.active}")
    return jsonify({"ok": True, "id": hint.id, "active": hint.active})


@api_bp.route("/admin/bender-picks", methods=["POST"])
@superuser_required
@limiter.limit("10 per minute")
def trigger_bender_picks():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    leagues = [league] if league in ("UCL", "UCL2627", "PL", "WC") else ["UCL", "UCL2627", "PL", "WC"]

    app = current_app._get_current_object()
    from ..services.auto_featured import run_bender_for_league

    total = 0
    for lg in leagues:
        n = run_bender_for_league(app, lg)
        if n:
            featured = Match.query.join(Tour).filter(
                Tour.league == lg, Match.featured == True, Match.status == "scheduled"
            ).all()
            ids_str = ",".join(str(m.id) for m in sorted(featured, key=lambda x: x.id))
            fp_row = Setting.query.get(f"bender_fp_{lg}") or Setting(key=f"bender_fp_{lg}")
            fp_row.value = ids_str
            db.session.merge(fp_row)
            total += n
    db.session.commit()

    actor = get_current_user()
    log_action(actor.id if actor else None, "bender_manual",
               f"Ручной запрос прогнозов: {', '.join(leagues)}, {total} матчей")
    return jsonify({"ok": True, "started": total})


@api_bp.route("/settings/auto-fetch", methods=["POST"])
@superuser_required
def set_auto_fetch():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    interval = data.get("interval")

    if enabled is None and interval is None:
        return jsonify({"error": "enabled or interval required"}), 400

    enabled_s = Setting.query.get("auto_fetch_enabled") or Setting(key="auto_fetch_enabled", value="0")
    interval_s = Setting.query.get("auto_fetch_interval_min") or Setting(key="auto_fetch_interval_min", value="15")

    if enabled is not None:
        enabled_s.value = "1" if enabled else "0"
        db.session.add(enabled_s)

    if interval is not None:
        try:
            interval = max(5, min(int(interval), 120))
        except (ValueError, TypeError):
            return jsonify({"error": "interval must be 5–120"}), 400
        interval_s.value = str(interval)
        db.session.add(interval_s)

    db.session.commit()

    from ..scheduler import update_auto_fetch
    final_enabled = enabled_s.value == "1"
    try:
        final_interval = max(5, min(int(interval_s.value), 120))
    except (ValueError, TypeError):
        final_interval = 15
    update_auto_fetch(final_enabled, final_interval)

    actor = get_current_user()
    log_action(actor.id if actor else None, "auto_fetch_changed",
               f"Автообновление: {'вкл' if final_enabled else 'выкл'}, интервал {final_interval} мин")
    return jsonify({"ok": True, "enabled": final_enabled, "interval": final_interval})


@api_bp.route("/settings/odds-fetch", methods=["POST"])
@superuser_required
def set_odds_fetch():
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"error": "enabled required"}), 400

    s = Setting.query.get("odds_fetch_enabled") or Setting(key="odds_fetch_enabled", value="1")
    s.value = "1" if enabled else "0"
    db.session.add(s)
    db.session.commit()

    actor = get_current_user()
    log_action(actor.id if actor else None, "odds_fetch_changed",
               f"Запросы ставок: {'вкл' if enabled else 'выкл'}")
    return jsonify({"ok": True, "enabled": enabled})


@api_bp.route("/settings/featured-auto", methods=["POST"])
@admin_required
def set_featured_auto_settings():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    if league not in ("UCL", "UCL2627", "PL", "WC"):
        return jsonify({"error": "invalid league"}), 400
    from ..services.auto_featured import save_auto_settings
    save_auto_settings(
        league,
        enabled=bool(data.get("enabled", False)),
        from_time=data.get("from_time", "10:00"),
        to_time=data.get("to_time", "03:00"),
        days=int(data.get("days") or 2),
    )
    return jsonify({"ok": True})


@api_bp.route("/admin/featured-auto/apply", methods=["POST"])
@admin_required
def apply_featured_auto_now():
    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    if league not in ("UCL", "UCL2627", "PL", "WC"):
        return jsonify({"error": "invalid league"}), 400
    from ..services.auto_featured import _do_apply
    # Admin explicitly requests auto-apply — clear manual lock first
    lock_row = Setting.query.get(f"featured_manual_lock_{league}") or Setting(key=f"featured_manual_lock_{league}", value="0")
    lock_row.value = "0"
    db.session.merge(lock_row)
    db.session.commit()
    app = current_app._get_current_object()
    n = _do_apply(league, app, force=True)
    return jsonify({"ok": True, "featured": n})


@api_bp.route("/team/<int:team_id>/recent-matches", methods=["GET"])
@login_required
def team_recent_matches(team_id):
    league = (request.args.get("league") or "UCL").upper()
    if league not in ("UCL", "UCL2627", "PL", "WC"):
        return jsonify({"error": "invalid league"}), 400

    team = Team.query.get(team_id)
    if not team:
        return jsonify({"error": "not found"}), 404

    count_s = Setting.query.get("team_form_matches_count")
    try:
        limit = int(count_s.value) if count_s else 0
    except (ValueError, TypeError):
        limit = 0

    q = (
        Match.query
        .join(Tour)
        .filter(
            Tour.league == league,
            Match.status == "finished",
            db.or_(Match.home_team_id == team_id, Match.away_team_id == team_id)
        )
        .order_by(Match.kickoff_time.desc())
    )
    if limit > 0:
        q = q.limit(limit)
    matches = q.all()

    result = []
    for m in matches:
        score = Score.query.filter_by(match_id=m.id).first()
        if not score:
            continue
        is_home = m.home_team_id == team_id
        opponent = m.away_team if is_home else m.home_team
        team_goals = score.home_score if is_home else score.away_score
        opp_goals = score.away_score if is_home else score.home_score
        if team_goals > opp_goals:
            res = "W"
        elif team_goals == opp_goals:
            res = "D"
        else:
            res = "L"
        dt = (m.kickoff_time + timedelta(hours=3)).strftime("%d.%m") if m.kickoff_time else "—"
        result.append({
            "date": dt,
            "opponent": opponent.display_name,
            "opponent_crest": opponent.crest or "",
            "score": f"{team_goals}:{opp_goals}",
            "is_home": is_home,
            "result": res,
        })

    return jsonify({
        "ok": True,
        "team_name": team.display_name,
        "team_crest": team.crest or "",
        "matches": result,
    })


@api_bp.route("/settings/team-form-count", methods=["POST"])
@superuser_required
def set_team_form_count():
    data = request.get_json(silent=True) or {}
    count = data.get("count")
    if count is None:
        return jsonify({"error": "count required"}), 400
    try:
        count = int(count)
        if count < 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({"error": "count must be >= 0"}), 400
    s = Setting.query.get("team_form_matches_count") or Setting(key="team_form_matches_count")
    s.value = str(count)
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "team_form_count_changed",
               f"Подсказка форма команды: {count if count > 0 else 'все'} матчей")
    return jsonify({"ok": True, "count": count})


@api_bp.route("/match/<int:match_id>/comment", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def save_match_comment(match_id):
    user = get_current_user()
    match = Match.query.get_or_404(match_id)
    if match.status not in ("finished", "live"):
        return jsonify({"error": "Матч ещё не завершён"}), 400

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Пустой комментарий"}), 400

    max_len_s = Setting.query.get("comment_max_length")
    try:
        max_len = max(1, int(max_len_s.value)) if max_len_s else 280
    except (ValueError, TypeError):
        max_len = 280

    if len(text) > max_len:
        return jsonify({"error": f"Слишком длинный (макс. {max_len})"}), 400

    comment = MatchComment(match_id=match_id, user_id=user.id, text=text)
    db.session.add(comment)
    db.session.flush()
    # mark as read up to this comment for the author
    read = CommentRead.query.filter_by(user_id=user.id, match_id=match_id).first()
    if read:
        read.last_read_comment_id = comment.id
    else:
        db.session.add(CommentRead(user_id=user.id, match_id=match_id, last_read_comment_id=comment.id))
    db.session.commit()
    log_action(user.id, "comment_set", f"Матч #{match_id}: {text[:50]}")
    return jsonify({
        "ok": True,
        "id": comment.id,
        "created_at": comment.created_at.isoformat(),
        "author": user.display_name,
        "avatar_emoji": user.avatar_emoji or "",
        "avatar_color": user.avatar_color or "",
        "is_bot": user.is_bot,
    })


@api_bp.route("/match/<int:match_id>/comment/<int:comment_id>", methods=["DELETE"])
@login_required
@limiter.limit("30 per minute")
def delete_match_comment(match_id, comment_id):
    user = get_current_user()
    comment = MatchComment.query.filter_by(id=comment_id, match_id=match_id, user_id=user.id).first()
    if not comment:
        return jsonify({"error": "Комментарий не найден"}), 404
    db.session.delete(comment)
    db.session.commit()
    log_action(user.id, "comment_deleted", f"Матч #{match_id}")
    return jsonify({"ok": True})


@api_bp.route("/match/<int:match_id>/comments/read", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def mark_comments_read(match_id):
    user = get_current_user()
    latest = MatchComment.query.filter_by(match_id=match_id).order_by(MatchComment.id.desc()).first()
    if not latest:
        return jsonify({"ok": True})
    read = CommentRead.query.filter_by(user_id=user.id, match_id=match_id).first()
    if read:
        read.last_read_comment_id = latest.id
    else:
        db.session.add(CommentRead(user_id=user.id, match_id=match_id, last_read_comment_id=latest.id))
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/settings/comment-max-length", methods=["POST"])
@superuser_required
@limiter.limit("10 per minute")
def set_comment_max_length():
    data = request.get_json(silent=True) or {}
    try:
        val = max(1, min(int(data.get("length", 10)), 500))
    except (ValueError, TypeError):
        return jsonify({"error": "Неверное значение"}), 400
    s = Setting.query.get("comment_max_length") or Setting(key="comment_max_length")
    s.value = str(val)
    db.session.add(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "comment_max_length_changed",
               f"Макс. длина комментария: {val}")
    return jsonify({"ok": True, "length": val})


@api_bp.route("/admin/generate-random-predictions", methods=["POST"])
@superuser_required
@limiter.limit("5 per minute")
def generate_random_predictions():
    import os, random

    if os.environ.get("APP_ENV") != "sandbox":
        return jsonify({"error": "Только в sandbox режиме"}), 400

    data = request.get_json(silent=True) or {}
    league = (data.get("league") or "").upper()
    if league not in ("UCL", "UCL2627", "PL", "WC"):
        return jsonify({"error": "Неизвестная лига"}), 400

    users = User.query.filter_by(is_bot=False, is_superuser=False).all()
    if not users:
        return jsonify({"ok": True, "predictions": 0})

    matches = (
        Match.query.join(Tour)
        .filter(Tour.league == league, Match.status == "finished", Match.featured == True)
        .all()
    )
    if not matches:
        return jsonify({"ok": True, "predictions": 0})

    preds_created = 0
    for match in matches:
        for user in users:
            hs = random.randint(0, 3)
            aws = random.randint(0, 3)
            pred = Prediction.query.filter_by(user_id=user.id, match_id=match.id).first()
            if pred:
                pred.home_score = hs
                pred.away_score = aws
            else:
                pred = Prediction(user_id=user.id, match_id=match.id, home_score=hs, away_score=aws)
                db.session.add(pred)
            preds_created += 1
        db.session.flush()
        update_points_for_match(match, commit=False)

    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "random_preds_generated",
               f"Рандомные ставки {league}: {preds_created} шт.")
    return jsonify({"ok": True, "predictions": preds_created})


@api_bp.route("/admin/copy-prod-to-sandbox", methods=["POST"])
@superuser_required
@limiter.limit("3 per minute")
def copy_prod_to_sandbox():
    import os
    from sqlalchemy import text as satext

    if os.environ.get("APP_ENV") != "sandbox":
        return jsonify({"error": "Только в sandbox режиме"}), 400

    try:
        # 1. Fetch all users from prod schema (including admins, superusers, bots)
        prod_users = db.session.execute(satext(
            "SELECT id, username, password_hash, is_admin, is_superuser, is_bot, "
            "nickname, avatar_emoji, avatar_color FROM bet.users"
        )).fetchall()

        if not prod_users:
            return jsonify({"ok": True, "users": 0, "matches": 0, "predictions": 0})

        # 2. Upsert users into sandbox (current schema) — all flags preserved from prod
        users_copied = 0
        for u in prod_users:
            db.session.execute(satext("""
                INSERT INTO users (username, password_hash, is_admin, is_superuser, is_bot, nickname, avatar_emoji, avatar_color)
                VALUES (:username, :ph, :is_admin, :is_superuser, :is_bot, :nickname, :emoji, :color)
                ON CONFLICT (username) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    is_admin = EXCLUDED.is_admin,
                    is_superuser = EXCLUDED.is_superuser,
                    is_bot = EXCLUDED.is_bot,
                    nickname = EXCLUDED.nickname,
                    avatar_emoji = EXCLUDED.avatar_emoji,
                    avatar_color = EXCLUDED.avatar_color
            """), dict(username=u.username, ph=u.password_hash, is_admin=u.is_admin,
                       is_superuser=u.is_superuser, is_bot=u.is_bot,
                       nickname=u.nickname, emoji=u.avatar_emoji, color=u.avatar_color))
            users_copied += 1

        # 3. Build prod_id → sandbox_id mapping via username
        sb_users = db.session.execute(satext(
            "SELECT id, username FROM users WHERE username = ANY(:unames)"
        ), {"unames": [u.username for u in prod_users]}).fetchall()
        username_to_sb_id = {u.username: u.id for u in sb_users}
        prod_to_sb_user = {pu.id: username_to_sb_id[pu.username]
                           for pu in prod_users if pu.username in username_to_sb_id}
        prod_user_ids = list(prod_to_sb_user.keys())

        if not prod_user_ids:
            db.session.commit()
            return jsonify({"ok": True, "users": users_copied, "matches": 0, "predictions": 0})

        # 4. Determine which leagues are enabled in prod
        enabled_rows = db.session.execute(satext(
            "SELECT key, value FROM bet.settings WHERE key IN "
            "('league_enabled_UCL', 'league_enabled_UCL2627', 'league_enabled_PL', 'league_enabled_WC')"
        )).fetchall()
        setting_map = {row.key: row.value for row in enabled_rows}
        enabled_leagues = [
            league for league in ("UCL", "UCL2627", "PL", "WC")
            if setting_map.get(f"league_enabled_{league}", "1") == "1"
        ]
        if not enabled_leagues:
            db.session.commit()
            return jsonify({"ok": True, "users": users_copied, "matches": 0, "predictions": 0})

        # 4a. Fetch finished matches from prod (enabled leagues only) with team/tour/score data
        prod_finished = db.session.execute(satext("""
            SELECT
                m.external_id, m.kickoff_time, m.status, m.featured,
                ht.external_id AS home_ext, ht.name AS home_name, ht.name_ru AS home_name_ru,
                    ht.short_name AS home_short, ht.crest AS home_crest,
                at.external_id AS away_ext, at.name AS away_name, at.name_ru AS away_name_ru,
                    at.short_name AS away_short, at.crest AS away_crest,
                tr.name AS tour_name, tr.season, tr.round_number, tr.league,
                    tr.start_date, tr.end_date, tr.status AS tour_status,
                s.home_score, s.away_score
            FROM bet.matches m
            JOIN bet.teams ht ON ht.id = m.home_team_id
            JOIN bet.teams at ON at.id = m.away_team_id
            JOIN bet.tours tr ON tr.id = m.tour_id
            LEFT JOIN bet.scores s ON s.match_id = m.id
            WHERE m.status = 'finished' AND m.external_id IS NOT NULL
              AND tr.league = ANY(:leagues)
        """), {"leagues": enabled_leagues}).fetchall()

        # 4a. Upsert teams (by external_id)
        seen_team_exts = set()
        for r in prod_finished:
            for ext, name, name_ru, short, crest in (
                (r.home_ext, r.home_name, r.home_name_ru, r.home_short, r.home_crest),
                (r.away_ext, r.away_name, r.away_name_ru, r.away_short, r.away_crest),
            ):
                if ext in seen_team_exts:
                    continue
                seen_team_exts.add(ext)
                db.session.execute(satext("""
                    INSERT INTO teams (external_id, name, name_ru, short_name, crest)
                    VALUES (:ext, :name, :name_ru, :short, :crest)
                    ON CONFLICT (external_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        name_ru = COALESCE(EXCLUDED.name_ru, teams.name_ru),
                        short_name = EXCLUDED.short_name,
                        crest = EXCLUDED.crest
                """), dict(ext=ext, name=name, name_ru=name_ru, short=short, crest=crest))

        sb_team_rows = db.session.execute(satext(
            "SELECT id, external_id FROM teams WHERE external_id = ANY(:eids)"
        ), {"eids": list(seen_team_exts)}).fetchall()
        ext_to_sb_team = {t.external_id: t.id for t in sb_team_rows}

        # 4b. Upsert tours (no unique constraint — match by name+season+league)
        tour_key_to_sb_id = {}
        for r in prod_finished:
            key = (r.tour_name, r.season, r.league)
            if key in tour_key_to_sb_id:
                continue
            existing = db.session.execute(satext(
                "SELECT id FROM tours WHERE name = :name AND season = :season AND league = :league"
            ), dict(name=r.tour_name, season=r.season, league=r.league)).fetchone()
            if existing:
                tour_key_to_sb_id[key] = existing.id
            else:
                new_tour = db.session.execute(satext("""
                    INSERT INTO tours (name, season, round_number, league, start_date, end_date, status)
                    VALUES (:name, :season, :rn, :league, :sd, :ed, :status)
                    RETURNING id
                """), dict(name=r.tour_name, season=r.season, rn=r.round_number,
                           league=r.league, sd=r.start_date, ed=r.end_date,
                           status=r.tour_status)).fetchone()
                tour_key_to_sb_id[key] = new_tour.id

        # 4c. Upsert finished matches and their scores
        matches_copied = 0
        ext_to_sb_match = {}
        for r in prod_finished:
            key = (r.tour_name, r.season, r.league)
            sb_tour_id = tour_key_to_sb_id.get(key)
            sb_home_id = ext_to_sb_team.get(r.home_ext)
            sb_away_id = ext_to_sb_team.get(r.away_ext)
            if not (sb_tour_id and sb_home_id and sb_away_id):
                continue
            match_row = db.session.execute(satext("""
                INSERT INTO matches (external_id, tour_id, home_team_id, away_team_id,
                                     kickoff_time, status, featured)
                VALUES (:ext, :tid, :hid, :aid, :kt, :status, :featured)
                ON CONFLICT (external_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    kickoff_time = EXCLUDED.kickoff_time,
                    featured = EXCLUDED.featured,
                    tour_id = EXCLUDED.tour_id,
                    home_team_id = EXCLUDED.home_team_id,
                    away_team_id = EXCLUDED.away_team_id
                RETURNING id
            """), dict(ext=r.external_id, tid=sb_tour_id, hid=sb_home_id, aid=sb_away_id,
                       kt=r.kickoff_time, status=r.status, featured=r.featured)).fetchone()
            if match_row:
                matches_copied += 1
                ext_to_sb_match[r.external_id] = match_row.id
                if r.home_score is not None:
                    db.session.execute(satext("""
                        INSERT INTO scores (match_id, home_score, away_score, manual_lock)
                        VALUES (:mid, :hs, :aws, false)
                        ON CONFLICT (match_id) DO UPDATE SET
                            home_score = EXCLUDED.home_score,
                            away_score = EXCLUDED.away_score
                    """), dict(mid=match_row.id, hs=r.home_score, aws=r.away_score))

        # 5. Fetch prod predictions for those matches
        prod_match_ext_ids = list(ext_to_sb_match.keys())
        if not prod_match_ext_ids:
            db.session.commit()
            return jsonify({"ok": True, "users": users_copied, "matches": matches_copied, "predictions": 0})

        prod_preds = db.session.execute(satext("""
            SELECT p.user_id, p.home_score, p.away_score, m.external_id AS match_ext_id,
                   pp.points, pp.reason, pp.manual_lock
            FROM bet.predictions p
            JOIN bet.matches m ON p.match_id = m.id
            LEFT JOIN bet.prediction_points pp ON pp.prediction_id = p.id
            WHERE p.user_id = ANY(:uid)
        """), {"uid": prod_user_ids}).fetchall()

        # 6. Upsert predictions and their points
        preds_copied = 0
        for p in prod_preds:
            sb_uid = prod_to_sb_user.get(p.user_id)
            sb_mid = ext_to_sb_match.get(p.match_ext_id)
            if not (sb_uid and sb_mid):
                continue

            row = db.session.execute(satext("""
                INSERT INTO predictions (user_id, match_id, home_score, away_score)
                VALUES (:uid, :mid, :hs, :aws)
                ON CONFLICT (user_id, match_id) DO UPDATE SET
                    home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score
                RETURNING id
            """), dict(uid=sb_uid, mid=sb_mid, hs=p.home_score, aws=p.away_score)).fetchone()

            if row:
                preds_copied += 1
                if p.points is not None:
                    db.session.execute(satext("""
                        INSERT INTO prediction_points (prediction_id, points, reason, manual_lock)
                        VALUES (:pid, :pts, :reason, :ml)
                        ON CONFLICT (prediction_id) DO UPDATE SET
                            points = EXCLUDED.points, reason = EXCLUDED.reason,
                            manual_lock = EXCLUDED.manual_lock
                    """), dict(pid=row.id, pts=p.points, reason=p.reason, ml=p.manual_lock or False))

        db.session.commit()
        actor = get_current_user()
        log_action(actor.id if actor else None, "copy_prod_to_sandbox",
                   f"Скопировано из прод: {users_copied} юзеров, {matches_copied} матчей, {preds_copied} ставок")
        return jsonify({"ok": True, "users": users_copied, "matches": matches_copied, "predictions": preds_copied})

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@api_bp.route("/settings/tg-remind", methods=["POST"])
@superuser_required
def set_tg_remind_settings():
    from ..scheduler import update_tg_remind
    data = request.get_json(force=True, silent=True) or {}

    keys_updated = []

    if "enabled" in data:
        val = "1" if data["enabled"] else "0"
        row = Setting.query.get("tg_remind_enabled") or Setting(key="tg_remind_enabled")
        row.value = val
        db.session.add(row)
        keys_updated.append("enabled")

    if "before_min" in data:
        try:
            v = max(1, min(int(data["before_min"]), 1440))
        except (ValueError, TypeError):
            return jsonify({"error": "before_min должен быть числом 1–1440"}), 400
        row = Setting.query.get("tg_remind_before_min") or Setting(key="tg_remind_before_min")
        row.value = str(v)
        db.session.add(row)
        keys_updated.append("before_min")

    if "quiet_from" in data:
        try:
            v = max(0, min(int(data["quiet_from"]), 23))
        except (ValueError, TypeError):
            return jsonify({"error": "quiet_from должен быть 0–23"}), 400
        row = Setting.query.get("tg_remind_quiet_from") or Setting(key="tg_remind_quiet_from")
        row.value = str(v)
        db.session.add(row)
        keys_updated.append("quiet_from")

    if "quiet_to" in data:
        try:
            v = max(0, min(int(data["quiet_to"]), 23))
        except (ValueError, TypeError):
            return jsonify({"error": "quiet_to должен быть 0–23"}), 400
        row = Setting.query.get("tg_remind_quiet_to") or Setting(key="tg_remind_quiet_to")
        row.value = str(v)
        db.session.add(row)
        keys_updated.append("quiet_to")

    if not keys_updated:
        return jsonify({"error": "Нечего сохранять"}), 400

    db.session.commit()

    enabled_s = Setting.query.get("tg_remind_enabled")
    update_tg_remind(enabled_s is not None and enabled_s.value == "1")

    actor = get_current_user()
    log_action(actor.id if actor else None, "tg_remind_settings",
               f"Настройки TG напоминания: {', '.join(keys_updated)}")
    return jsonify({"ok": True})


@api_bp.route("/admin/tg-remind", methods=["POST"])
@superuser_required
def tg_remind():
    import requests as req_lib
    from ..models import Match, Tour, Team, User, Prediction

    MINSK = timezone(timedelta(hours=3))
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы"}), 500

    all_featured = (
        Match.query
        .filter(Match.featured == True, Match.status == "scheduled")
        .order_by(Match.kickoff_time)
        .all()
    )
    if not all_featured:
        return jsonify({"error": "Нет активных матчей для ставок"}), 400

    rounds_numbered = [m.featured_round for m in all_featured if m.featured_round is not None]
    active_round = min(rounds_numbered) if rounds_numbered else None
    if active_round is not None:
        featured = [m for m in all_featured if m.featured_round == active_round]
    else:
        featured = [m for m in all_featured if m.featured_round is None]

    all_users = User.query.filter(User.is_bot == False).all()
    placed_ids_by_match = {}
    for m in featured:
        preds = Prediction.query.filter_by(match_id=m.id).all()
        placed_ids_by_match[m.id] = {p.user_id for p in preds}

    lines = ["🔔 Напоминание о ставках!\n"]
    any_missing = False
    for m in featured:
        home = m.home_team.display_name if m.home_team else "?"
        away = m.away_team.display_name if m.away_team else "?"
        kt = m.kickoff_time.replace(tzinfo=timezone.utc).astimezone(MINSK).strftime("%H:%M") if m.kickoff_time else "?"
        missing = [u.display_name for u in all_users if u.id not in placed_ids_by_match[m.id]]
        lines.append(f"⚽ {kt} — {home} vs {away}")
        if missing:
            any_missing = True
            lines.append(f"❌ Не поставили: {', '.join(missing)}")
        else:
            lines.append("✅ Все поставили")

    if not any_missing:
        return jsonify({"ok": True, "sent": False, "message": "Все уже поставили — сообщение не отправлено"}), 200

    app_url = os.environ.get("APP_URL", "")
    if app_url:
        lines.append(f"\n👉 {app_url}")

    text = "\n".join(lines)
    resp = req_lib.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    resp.raise_for_status()
    actor = get_current_user()
    log_action(actor.id if actor else None, "tg_remind", f"Отправлено TG напоминание: {len(featured)} матч(ей)")
    return jsonify({"ok": True, "sent": True, "message": "Сообщение отправлено в Telegram"})


@api_bp.route("/settings/bender-commentary", methods=["POST"])
@superuser_required
def set_bender_commentary():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    s = Setting.query.get("bender_commentary_enabled") or Setting(key="bender_commentary_enabled")
    s.value = "1" if enabled else "0"
    db.session.merge(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "bender_commentary_toggle",
               f"Комментарии Бендера: {'вкл' if enabled else 'выкл'}")
    return jsonify({"ok": True, "enabled": enabled})


@api_bp.route("/settings/bender-standings", methods=["POST"])
@superuser_required
def set_bender_standings():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    s = Setting.query.get("bender_standings_enabled") or Setting(key="bender_standings_enabled")
    s.value = "1" if enabled else "0"
    db.session.merge(s)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "bender_standings_toggle",
               f"Итоги Бендера: {'вкл' if enabled else 'выкл'}")
    return jsonify({"ok": True, "enabled": enabled})


@api_bp.route("/admin/hof", methods=["POST"])
@superuser_required
def hof_create():
    data = request.get_json() or {}
    tournament = (data.get("tournament") or "").strip()
    champion = (data.get("champion") or "").strip()
    if not tournament or not champion:
        return jsonify({"error": "Обязательные поля: tournament, champion"}), 400
    from sqlalchemy import func as _func
    max_order = db.session.query(_func.max(HofEntry.sort_order)).scalar()
    entry = HofEntry(tournament=tournament, champion=champion, sort_order=(max_order or 0) + 1)
    db.session.add(entry)
    db.session.commit()
    actor = get_current_user()
    log_action(actor.id if actor else None, "hof_create", f"Зал Славы: добавлен {champion} ({tournament})")
    return jsonify({"ok": True, "id": entry.id})


@api_bp.route("/admin/hof/<int:entry_id>", methods=["POST"])
@superuser_required
def hof_update(entry_id):
    entry = db.session.get(HofEntry, entry_id)
    if not entry:
        return jsonify({"error": "Не найдено"}), 404
    data = request.get_json() or {}
    if "tournament" in data:
        entry.tournament = (data["tournament"] or "").strip()
    if "champion" in data:
        entry.champion = (data["champion"] or "").strip()
    if not entry.tournament or not entry.champion:
        db.session.rollback()
        return jsonify({"error": "Поля не могут быть пустыми"}), 400
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/admin/hof/<int:entry_id>/delete", methods=["POST"])
@superuser_required
def hof_delete(entry_id):
    entry = db.session.get(HofEntry, entry_id)
    if not entry:
        return jsonify({"error": "Не найдено"}), 404
    actor = get_current_user()
    log_action(actor.id if actor else None, "hof_delete",
               f"Зал Славы: удалён {entry.champion} ({entry.tournament})")
    db.session.delete(entry)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/scheduler/status", methods=["GET"])
@login_required
def scheduler_status():
    from ..scheduler import _scheduler
    enabled_s = Setting.query.get("auto_fetch_enabled")
    enabled = enabled_s is not None and enabled_s.value == "1"
    interval_s = Setting.query.get("auto_fetch_interval_min")
    try:
        interval = max(5, min(int(interval_s.value), 120)) if interval_s else 15
    except (ValueError, TypeError):
        interval = 15
    next_run_ts = None
    if enabled:
        job = _scheduler.get_job("auto_fetch")
        if job and job.next_run_time:
            next_run_ts = job.next_run_time.timestamp()
    return jsonify({"enabled": enabled, "interval_min": interval, "next_run_ts": next_run_ts})
