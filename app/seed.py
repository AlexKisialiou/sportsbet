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

    admin_username = os.environ.get("ADMIN_USERNAME", "")
    if admin_username and not User.query.filter_by(username=admin_username).first():
        db.session.add(User(
            username=admin_username,
            password_hash=generate_password_hash(os.environ.get("ADMIN_PASSWORD", "")),
            nickname=os.environ.get("ADMIN_NICKNAME"),
            is_admin=True,
            is_superuser=True,
        ))
        db.session.commit()
