import os
from flask import Flask
from dotenv import load_dotenv
from .models import db

load_dotenv()


def create_app():
    app = Flask(__name__, template_folder="templates")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///sportsbet.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    db.init_app(app)

    with app.app_context():
        if os.environ.get("RESET_DB"):
            db.drop_all()
        db.create_all()

        # Migration: add featured column if missing
        from sqlalchemy import text, inspect as sa_inspect
        try:
            cols = [c["name"] for c in sa_inspect(db.engine).get_columns("matches")]
            if "featured" not in cols:
                db.session.execute(text(
                    "ALTER TABLE matches ADD COLUMN featured BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                db.session.commit()
                print("[migration] added matches.featured column")

            user_cols = [c["name"] for c in sa_inspect(db.engine).get_columns("users")]
            if "nickname" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN nickname VARCHAR(100)"))
                db.session.commit()
                print("[migration] added users.nickname column")
            if "is_bot" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN is_bot BOOLEAN NOT NULL DEFAULT FALSE"))
                db.session.commit()
                print("[migration] added users.is_bot column")
            if "avatar_emoji" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN avatar_emoji VARCHAR(10)"))
                db.session.commit()
                print("[migration] added users.avatar_emoji column")
            if "avatar_color" not in user_cols:
                db.session.execute(text("ALTER TABLE users ADD COLUMN avatar_color VARCHAR(10)"))
                db.session.commit()
                print("[migration] added users.avatar_color column")
        except Exception as e:
            db.session.rollback()
            print(f"[migration] skipped: {e}")

        from .seed import run as seed
        seed()
        from .services.football_api import fetch_and_save_cl_matches
        try:
            added, updated = fetch_and_save_cl_matches()
            print(f"[startup] CL matches: +{added} added, {updated} updated")
        except Exception as e:
            print(f"[startup] CL fetch skipped: {e}")

    from .routes.main import main_bp
    from .routes.api import api_bp
    from .routes.auth import auth_bp
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)

    from .auth import get_current_user
    from . import config as app_config

    @app.context_processor
    def inject_globals():
        return dict(
            current_user=get_current_user(),
            APP_NAME=app_config.APP_NAME,
            APP_VERSION=app_config.APP_VERSION,
        )

    return app
