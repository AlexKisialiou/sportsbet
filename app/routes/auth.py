from flask import Blueprint, request, session, redirect, url_for, render_template
from werkzeug.security import check_password_hash
from ..models import db, User
from ..auth import login_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and not user.is_bot and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            return redirect(url_for("main.index"))
        error = "Неверный логин или пароль"
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    from ..auth import get_current_user
    user = get_current_user()
    saved = False

    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        user.nickname = nickname if nickname else None
        emoji = request.form.get("avatar_emoji", "").strip()
        color = request.form.get("avatar_color", "").strip()
        user.avatar_emoji = emoji if emoji else None
        user.avatar_color = color if color else None
        db.session.commit()
        saved = True

    from ..config import AVATAR_EMOJIS, AVATAR_COLORS
    return render_template("profile.html", user=user, saved=saved,
                           avatar_emojis=AVATAR_EMOJIS, avatar_colors=AVATAR_COLORS)
