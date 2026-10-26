import os

from flask import Flask, abort, jsonify, send_from_directory
from flask_cors import CORS
from pathlib import Path
from dotenv import load_dotenv

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

def create_app():
    load_dotenv()  # reads a local .env file if present (Render uses its own env vars)
    app = Flask(__name__, static_folder=None)  # the frontend is served by static_files() below
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    from .routes.predict import predict_bp
    from .routes.analytics import analytics_bp
    from .routes.concierge import concierge_bp

    app.register_blueprint(predict_bp, url_prefix="/api")
    app.register_blueprint(analytics_bp, url_prefix="/api")
    app.register_blueprint(concierge_bp, url_prefix="/api")

    # Load the delay model in the background so the first prediction is fast
    from .services import delay_model
    if delay_model.is_available():
        import threading
        threading.Thread(target=delay_model._load, daemon=True).start()

    @app.route("/")
    def index():
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.route("/api/site-config")
    def site_config():
        # Contact details are set in the host's environment (CONTACT_EMAIL, CONTACT_WHATSAPP), never in the code
        whatsapp = "".join(c for c in os.environ.get("CONTACT_WHATSAPP", "") if c.isdigit() or c == "+")
        return jsonify({"contact_email": os.environ.get("CONTACT_EMAIL", "").strip() or None,
                        "contact_whatsapp": whatsapp if len(whatsapp.lstrip("+")) >= 8 else None})

    @app.route("/<path:path>")
    def static_files(path):
        # Clean page URLs: /faq serves faq.html
        if "." not in path.rsplit("/", 1)[-1] and (FRONTEND_DIR / f"{path}.html").is_file():
            path = f"{path}.html"
        if not (FRONTEND_DIR / path).is_file():
            abort(404)
        return send_from_directory(str(FRONTEND_DIR), path)

    return app
