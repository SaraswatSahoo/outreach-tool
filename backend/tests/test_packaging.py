import importlib.util
import os
import zipfile

import pytest

import constants

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_launcher():
    spec = importlib.util.spec_from_file_location("launcher", os.path.join(REPO, "run.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def frontend_app(tmp_path, monkeypatch, client):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><div id="root"></div></html>')
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("OUTREACH_FRONTEND_DIST", str(dist))
    from app import create_app

    return create_app().test_client()


def test_spa_serves_index_assets_and_keeps_api_404(frontend_app):
    assert 'id="root"' in frontend_app.get("/").get_data(as_text=True)
    assert 'id="root"' in frontend_app.get("/campaign/3/preview").get_data(as_text=True)  # client-side route
    assert frontend_app.get("/assets/app.js").get_data(as_text=True) == "console.log(1)"
    missing = frontend_app.get("/api/does-not-exist")
    assert missing.status_code == 404 and missing.get_json() == {"error": "Not found"}
    assert frontend_app.get("/api/health").get_json()["app"] == "outreach-tool"


def test_health(client):
    from version import VERSION

    assert client.get("/api/health").get_json() == {"app": "outreach-tool", "version": VERSION}


@pytest.mark.parametrize(
    "platform,environ,expected_tail",
    [
        (
            "win32",
            {"APPDATA": "C:/Users/a/AppData/Roaming"},
            os.path.join("C:/Users/a/AppData/Roaming", "OutreachTool"),
        ),
        ("darwin", {}, os.path.join("Library", "Application Support", "OutreachTool")),
        ("linux", {"XDG_DATA_HOME": "/x/data"}, os.path.join("/x/data", "outreach-tool")),
        ("linux", {}, os.path.join(".local", "share", "outreach-tool")),
    ],
)
def test_user_data_dir_per_os(platform, environ, expected_tail):
    assert constants.user_data_dir(platform, environ).endswith(expected_tail)


def test_source_checkout_keeps_data_in_backend():
    assert constants.default_data_dir(frozen=False) == constants.BASE_DIR
    assert constants.default_data_dir(frozen=True) == constants.user_data_dir()


def test_launcher_hash_changes_with_content(tmp_path):
    launcher = _load_launcher()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("1")
    first = launcher.hash_paths(str(tmp_path), ["src"])
    assert launcher.hash_paths(str(tmp_path), ["src"]) == first
    (tmp_path / "src" / "a.js").write_text("2")
    assert launcher.hash_paths(str(tmp_path), ["src"]) != first


def test_launcher_safe_extract_rejects_path_traversal(tmp_path):
    launcher = _load_launcher()
    good, evil = tmp_path / "good.zip", tmp_path / "evil.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("index.html", "<html>")
        zf.writestr("assets/app.js", "x")
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "boom")
    launcher.safe_extract(str(good), str(tmp_path / "out"))
    assert (tmp_path / "out" / "assets" / "app.js").read_text() == "x"
    with pytest.raises(ValueError):
        launcher.safe_extract(str(evil), str(tmp_path / "out2"))
    assert not (tmp_path / "escape.txt").exists()


def test_launcher_detects_repo_from_remote(monkeypatch):
    launcher = _load_launcher()

    class Result:
        def __init__(self, out):
            self.stdout = out

    for remote in (
        "git@github.com:someone/fork.git",
        "https://github.com/someone/fork",
        "https://github.com/someone/fork.git",
    ):
        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, _r=remote, **k: Result(_r))
        assert launcher.github_repo() == "someone/fork"
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Result(""))
    assert launcher.github_repo() == launcher.DEFAULT_REPO


def test_safe_console_survives_legacy_windows_code_page(monkeypatch):
    import io

    from serve import safe_console

    for name in ("stdout", "stderr"):
        monkeypatch.setattr(f"sys.{name}", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    safe_console()
    print("Groq error on model x — retrying ─── …1234")  # would raise UnicodeEncodeError without it


def test_server_starts_under_cp1252_console(tmp_path):
    """Regression: the banner crashed the server on Windows (cp1252 console) before it could serve."""
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(
        os.environ,
        PYTHONIOENCODING="cp1252",
        OUTREACH_DB_PATH=str(tmp_path / "x.db"),
        OUTREACH_CONFIG_PATH=str(tmp_path / "c.json"),
        OUTREACH_UPLOAD_DIR=str(tmp_path / "up"),
    )
    serve_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "serve.py")
    proc = subprocess.Popen(
        [sys.executable, serve_py, "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        body = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                    body = resp.read().decode()
                    break
            except OSError:
                if proc.poll() is not None:
                    break
                time.sleep(0.25)
        assert body and "outreach-tool" in body, proc.stdout.read().decode(errors="replace") if proc.poll() else ""
    finally:
        proc.terminate()
        proc.wait(timeout=10)
