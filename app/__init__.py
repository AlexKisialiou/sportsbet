import os
from datetime import timedelta
from flask import Flask, jsonify, request as flask_request, render_template
from dotenv import load_dotenv
from .models import db

load_dotenv()

IS_PRODUCTION = bool(os.environ.get("RENDER"))
DB_SCHEMA = "bet"


def create_app():
    app = Flask(__name__, template_folder="templates")

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///sportsbet.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    # Request size limit (1 MB)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

    # Session security
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if IS_PRODUCTION:
        app.config["SESSION_COOKIE_SECURE"] = True

    is_postgres = "postgresql" in app.config["SQLALCHEMY_DATABASE_URI"]

    # Pin all connections to the sportsbet schema (PostgreSQL only)
    if is_postgres:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"options": f"-csearch_path={DB_SCHEMA}"}
        }

    # Trust the Render proxy for real client IPs
    if IS_PRODUCTION:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)

    from .limiter import limiter
    limiter.init_app(app)

    with app.app_context():
        from sqlalchemy import text, inspect as sa_inspect

        if is_postgres:
            _init_schema(db, DB_SCHEMA)

        db.create_all()

        # Column migrations — search_path already points to DB_SCHEMA so
        # unqualified table names resolve there automatically.
        inspect_schema = DB_SCHEMA if is_postgres else None
        try:
            insp = sa_inspect(db.engine)
            cols = [c["name"] for c in insp.get_columns("matches", schema=inspect_schema)]
            if "featured" not in cols:
                db.session.execute(text(
                    "ALTER TABLE matches ADD COLUMN featured BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                db.session.commit()
                print("[migration] added matches.featured column")

            user_cols = [c["name"] for c in insp.get_columns("users", schema=inspect_schema)]
            for col, ddl in [
                ("nickname",        "ALTER TABLE users ADD COLUMN nickname VARCHAR(100)"),
                ("is_bot",          "ALTER TABLE users ADD COLUMN is_bot BOOLEAN NOT NULL DEFAULT FALSE"),
                ("avatar_emoji",    "ALTER TABLE users ADD COLUMN avatar_emoji VARCHAR(10)"),
                ("avatar_color",    "ALTER TABLE users ADD COLUMN avatar_color VARCHAR(10)"),
                ("superadmin_note", "ALTER TABLE users ADD COLUMN superadmin_note VARCHAR(100)"),
            ]:
                if col not in user_cols:
                    db.session.execute(text(ddl))
                    db.session.commit()
                    print(f"[migration] added users.{col} column")

            if "is_superuser" not in user_cols:
                db.session.execute(text(
                    "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                db.session.execute(text(
                    "UPDATE users SET is_superuser=TRUE WHERE is_admin=TRUE"
                ))
                db.session.commit()
                print("[migration] added users.is_superuser column")
        except Exception as e:
            db.session.rollback()
            print(f"[migration] skipped: {e}")

        from .seed import run as seed
        seed()
        from .services.football_api import fetch_and_save_cl_matches, fetch_and_save_pl_matches
        try:
            added, updated = fetch_and_save_cl_matches()
            print(f"[startup] CL matches: +{added} added, {updated} updated")
        except Exception as e:
            print(f"[startup] CL fetch skipped: {e}")
        try:
            added, updated = fetch_and_save_pl_matches()
            print(f"[startup] PL matches: +{added} added, {updated} updated")
        except Exception as e:
            print(f"[startup] PL fetch skipped: {e}")

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
        theme = "navy"
        try:
            from .models import Setting
            s = Setting.query.get("theme")
            if s:
                theme = s.value
        except Exception:
            pass
        return dict(
            current_user=get_current_user(),
            APP_NAME=app_config.APP_NAME,
            APP_VERSION=app_config.APP_VERSION,
            current_theme=theme,
        )

    # Security headers on every response
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if IS_PRODUCTION:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    # Rate-limit exceeded handler
    @app.errorhandler(429)
    def ratelimit_handler(e):
        if flask_request.path.startswith("/api/"):
            return jsonify({"error": "Слишком много запросов. Подожди немного."}), 429
        return render_template("429.html"), 429

    return app


def _init_schema(db, schema: str):
    """Create the schema if missing, then migrate any tables still in 'public'."""
    from sqlalchemy import text

    with db.engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()

        # Find tables that are still sitting in public (one-time migration)
        result = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        public_tables = [row[0] for row in result]

        if not public_tables:
            return

        moved = []
        for tbl in public_tables:
            # Use a savepoint so a single failure doesn't abort the whole batch
            conn.execute(text("SAVEPOINT mv"))
            try:
                conn.execute(text(f'ALTER TABLE public."{tbl}" SET SCHEMA {schema}'))
                conn.execute(text("RELEASE SAVEPOINT mv"))
                moved.append(tbl)
            except Exception as e:
                conn.execute(text("ROLLBACK TO SAVEPOINT mv"))
                print(f"[schema] could not move '{tbl}': {e}")

        if moved:
            conn.commit()
            print(f"[schema] moved to '{schema}': {', '.join(moved)}")
