import os
from werkzeug.security import generate_password_hash
from .models import db, User


BENDER_USERNAME = "bender"


def run():
    # Ensure Бендер bot user exists
    if not User.query.filter_by(username=BENDER_USERNAME).first():
        db.session.add(User(
            username=BENDER_USERNAME,
            password_hash="",
            nickname="Бендер",
            is_bot=True,
        ))
        db.session.commit()

    if User.query.filter_by(is_bot=False).count() == 0:
        users = [
            {"username": os.environ["ADMIN_USERNAME"],
             "password": os.environ["ADMIN_PASSWORD"],
             "nickname": os.environ.get("ADMIN_NICKNAME"),
             "is_admin": True},
            {"username": os.environ["USER1_USERNAME"],
             "password": os.environ["USER1_PASSWORD"],
             "nickname": os.environ.get("USER1_NICKNAME"),
             "is_admin": False},
            {"username": os.environ["USER2_USERNAME"],
             "password": os.environ["USER2_PASSWORD"],
             "nickname": os.environ.get("USER2_NICKNAME"),
             "is_admin": False},
            {"username": os.environ["USER3_USERNAME"],
             "password": os.environ["USER3_PASSWORD"],
             "nickname": os.environ.get("USER3_NICKNAME"),
             "is_admin": False},
        ]
        for u in users:
            db.session.add(User(
                username=u["username"],
                password_hash=generate_password_hash(u["password"]),
                nickname=u["nickname"],
                is_admin=u["is_admin"],
            ))
        db.session.commit()
