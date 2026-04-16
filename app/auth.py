from functools import wraps
from flask import session, redirect, url_for
from .models import User


def get_current_user():
    uid = session.get("user_id")
    if uid:
        return User.query.get(uid)
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user.is_admin:
            return redirect(url_for("main.index"))
        return f(*args, **kwargs)
    return decorated
