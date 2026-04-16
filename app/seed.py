from .models import db, User


def run():
    if User.query.count() == 0:
        db.session.add(User(username="test"))
        db.session.commit()
