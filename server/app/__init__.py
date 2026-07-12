from flask import Flask

from .db import init_db, SessionLocal


def create_app():
    app = Flask(__name__)

    init_db()

    from .auth import get_or_create_secret_key

    app.secret_key = get_or_create_secret_key(SessionLocal())

    from .routes_api import api_bp
    from .routes_dashboard import dashboard_bp

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(dashboard_bp)

    @app.teardown_appcontext
    def remove_session(exception=None):
        SessionLocal.remove()

    return app
