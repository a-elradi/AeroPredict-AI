from flask import Flask, send_from_directory
from flask_cors import CORS
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

def create_app():
    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    from .routes.predict import predict_bp
    from .routes.analytics import analytics_bp

    app.register_blueprint(predict_bp, url_prefix="/api")
    app.register_blueprint(analytics_bp, url_prefix="/api")

    @app.route("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/<path:path>")
    def static_files(path):
        return send_from_directory(str(FRONTEND_DIR), path)

    return app
