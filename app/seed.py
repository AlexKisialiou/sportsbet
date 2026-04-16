from werkzeug.security import generate_password_hash
from .models import db, User

USERS = [
    {"username": "admin",  "password": "admin",  "is_admin": True},
    {"username": "user1",  "password": "user1",  "is_admin": False},
    {"username": "user2",  "password": "user2",  "is_admin": False},
    {"username": "user3",  "password": "user3",  "is_admin": False},
]


def run():
    if User.query.count() == 0:
        for u in USERS:
            db.session.add(User(
                username=u["username"],
                password_hash=generate_password_hash(u["password"]),
                is_admin=u["is_admin"],
            ))
        db.session.commit()
