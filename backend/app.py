"""
Flask application entry point for the outreach tool.

Routes live in the `routes` package (one blueprint per feature area).
If the React app has been built (frontend/dist), it's served from the same
server, so the whole tool runs on one port — see serve.py / run.py.
Run `python app.py` from the backend directory for the Flask dev server.
"""

import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from common import BadRequest
from config import load_config
from constants import CONFIG_PATH, DB_PATH, MAX_RESUME_BYTES, UPLOAD_FOLDER, frontend_dist_dir
from db import init_db
from jobs import mark_interrupted_jobs
from routes import ALL_BLUEPRINTS
from routes.inbox import start_scheduler


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_RESUME_BYTES + 1024 * 1024

    # Only the local frontend may call the API (the Vite dev proxy doesn't need CORS at all).
    # An open CORS policy would let any website you visit send email from your Gmail.
    CORS(app, origins=["http://localhost:5173", "http://127.0.0.1:5173"])

    for folder in (UPLOAD_FOLDER, os.path.dirname(DB_PATH), os.path.dirname(CONFIG_PATH)):
        os.makedirs(folder, exist_ok=True)
    init_db()
    mark_interrupted_jobs()

    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)

    @app.errorhandler(BadRequest)
    def handle_bad_request(e):
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_too_large(e):
        return jsonify({"error": "File too large. Maximum size is 5MB."}), 413

    @app.errorhandler(HTTPException)
    def handle_http_error(e):
        return jsonify({"error": e.description}), e.code

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        app.logger.exception("Unhandled error")
        return jsonify({"error": f"Internal error: {e}"}), 500

    _serve_frontend(app, frontend_dist_dir())
    return app


def _serve_frontend(app, dist):
    """Serve the built React app; unknown non-API paths get index.html (client-side routing)."""
    if not os.path.isfile(os.path.join(dist, "index.html")):
        return

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def frontend(path):
        if path == "api" or path.startswith("api/"):
            return jsonify({"error": "Not found"}), 404
        if path and os.path.isfile(os.path.join(dist, path)):
            return send_from_directory(dist, path)
        response = send_from_directory(dist, "index.html")
        response.headers["Cache-Control"] = "no-cache"  # always pick up a new build
        return response


app = create_app()


if __name__ == "__main__":
    from serve import safe_console

    safe_console()
    # backend/.env is already loaded by constants.py; stop Flask suggesting python-dotenv
    os.environ.setdefault("FLASK_SKIP_DOTENV", "1")
    load_config()  # Ensure config.json exists
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    # With the debug reloader the module runs in two processes; only start
    # the scheduler in the child that actually serves requests.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()
    app.run(debug=debug, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
