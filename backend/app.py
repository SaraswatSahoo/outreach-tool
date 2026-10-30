"""
Flask application entry point for the outreach tool.

Routes live in the `routes` package (one blueprint per feature area).
Run with `python app.py` from the backend directory.
"""

import os

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from common import BadRequest
from config import load_config
from constants import MAX_RESUME_BYTES, UPLOAD_FOLDER
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

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
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

    return app


app = create_app()


if __name__ == "__main__":
    # backend/.env is already loaded by constants.py; stop Flask suggesting python-dotenv
    os.environ.setdefault("FLASK_SKIP_DOTENV", "1")
    load_config()  # Ensure config.json exists
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    # With the debug reloader the module runs in two processes; only start
    # the scheduler in the child that actually serves requests.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()
    app.run(debug=debug, host="127.0.0.1", port=int(os.environ.get("PORT", 5000)))
