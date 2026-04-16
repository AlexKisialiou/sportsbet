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

    @app.context_processor
    def inject_user():
        return dict(current_user=get_current_user())

    return app
