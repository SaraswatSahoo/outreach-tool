"""Profile and resume routes."""

import os

from flask import Blueprint, jsonify, request

from common import json_error
from config import is_profile_complete, load_config, save_config
from constants import MAX_RESUME_BYTES, RESUME_PATH
from llm import resume_models
from resume_parser import parse_resume

bp = Blueprint("profile", __name__, url_prefix="/api")

PROFILE_FIELDS = ["name", "college", "branch", "year", "cgpa", "skills", "github", "linkedin", "bio"]


@bp.route("/profile", methods=["GET"])
def get_profile():
    config = load_config()
    return jsonify(
        {
            "profile": config.get("profile", {}),
            "is_complete": is_profile_complete(config),
            "resume_parsed": config.get("resume_parsed", {}),
            "has_resume": os.path.exists(RESUME_PATH),
        }
    )


@bp.route("/profile", methods=["POST"])
def save_profile():
    data = request.json
    if not data:
        return json_error("No data provided")

    config = load_config()
    for field in PROFILE_FIELDS:
        if field in data:
            config["profile"][field] = (data[field] or "").strip()

    save_config(config)
    return jsonify({"message": "Profile saved successfully", "is_complete": is_profile_complete(config)})


@bp.route("/upload-resume", methods=["POST"])
def upload_resume():
    if "resume" not in request.files:
        return json_error("No file provided")

    file = request.files["resume"]
    if file.filename == "":
        return json_error("No file selected")

    if not file.filename.lower().endswith(".pdf"):
        return json_error("Only PDF files are accepted")

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_RESUME_BYTES:
        return json_error("File too large. Maximum size is 5MB.")

    if file.read(5) != b"%PDF-":
        return json_error("File does not look like a valid PDF")
    file.seek(0)

    os.makedirs(os.path.dirname(RESUME_PATH), exist_ok=True)
    file.save(RESUME_PATH)

    # Trigger parsing automatically if a Groq key exists
    groq_api_key = load_config().get("groq_api_key", "")
    parsed, parse_error = {}, None
    if groq_api_key:
        try:
            parsed = parse_resume(RESUME_PATH, groq_api_key, resume_models(load_config()))
        except Exception as e:
            print(f"Resume parsing failed: {e}")
            parse_error = str(e)

    # Reload after the (slow) parse so settings saved meanwhile aren't overwritten
    config = load_config()
    config["resume_parsed"] = parsed
    save_config(config)

    return jsonify(
        {
            "message": "Resume uploaded successfully",
            "parsed": parsed,
            "parse_error": parse_error,
        }
    )


@bp.route("/resume/reparse", methods=["POST"])
def reparse_resume():
    groq_api_key = load_config().get("groq_api_key", "")
    if not groq_api_key:
        return json_error("Groq API key not configured. Go to Settings.")
    if not os.path.exists(RESUME_PATH):
        return json_error("No resume uploaded yet")

    try:
        parsed = parse_resume(RESUME_PATH, groq_api_key, resume_models(load_config()))
    except Exception as e:
        return json_error(str(e), 500)

    config = load_config()
    config["resume_parsed"] = parsed
    save_config(config)
    return jsonify({"success": True, "parsed": parsed})
