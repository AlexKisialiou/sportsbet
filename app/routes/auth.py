import os
from flask import Blueprint, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash, generate_password_hash
from ..models import db, User
from ..auth import login_required
from ..services.activity import log_action
from ..limiter import limiter

auth_bp = Blueprint("auth", __name__)


def _check_password(user, password):
    if user.is_superuser:
        return password == os.environ.get("ADMIN_PASSWORD", "")
    return check_password_hash(user.password_hash, password)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute; 60 per hour", methods=["POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and not user.is_bot and _check_password(user, password):
            session["user_id"] = user.id
            log_action(user.id, "login", "Вошёл в систему")
            return redirect(url_for("main.index"))
        error = "Неверный логин или пароль"
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    from ..auth import get_current_user
    user = get_current_user()
    if user:
        log_action(user.id, "logout", "Вышел из системы")
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from ..auth import get_current_user
    user = get_current_user()
    saved = False
    pwd_error = None
    pwd_saved = False

    if request.method == "POST":
        action = request.form.get("action", "profile")
        if action == "password" and not user.is_superuser:
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "").strip()
            confirm = request.form.get("confirm_password", "").strip()
            if not check_password_hash(user.password_hash, current):
                pwd_error = "Неверный текущий пароль"
            elif len(new) < 3:
                pwd_error = "Минимум 3 символа"
            elif new != confirm:
                pwd_error = "Пароли не совпадают"
            else:
                User.query.filter_by(id=user.id).update(
                    {'password_hash': generate_password_hash(new)}
                )
                db.session.commit()
                pwd_saved = True
                log_action(user.id, "password_changed", "Изменил пароль")
        else:
            nickname = request.form.get("nickname", "").strip()[:11]
            user.nickname = nickname if nickname else None
            emoji = request.form.get("avatar_emoji", "").strip()
            color = request.form.get("avatar_color", "").strip()
            user.avatar_emoji = emoji if emoji else None
            user.avatar_color = color if color else None
            db.session.commit()
            saved = True
            log_action(user.id, "profile_updated", "Обновил профиль")

    from ..config import AVATAR_EMOJIS, AVATAR_COLORS
    return render_template("profile.html", user=user, saved=saved,
                           pwd_error=pwd_error, pwd_saved=pwd_saved,
                           avatar_emojis=AVATAR_EMOJIS, avatar_colors=AVATAR_COLORS)
