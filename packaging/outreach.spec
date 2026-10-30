# PyInstaller build for the standalone Outreach Tool executable (one file, console window).
#
#   cd frontend && npm ci && npm run build      # the website must be built first
#   pyinstaller packaging/outreach.spec --distpath dist --workpath build
#
# The console window doubles as the "app is running" indicator: it shows the
# address and logs, and closing it stops the tool.
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is provided by PyInstaller)
BACKEND = os.path.join(ROOT, "backend")
FRONTEND_DIST = os.path.join(ROOT, "frontend", "dist")

if not os.path.isfile(os.path.join(FRONTEND_DIST, "index.html")):
    raise SystemExit("frontend/dist is missing — run `npm ci && npm run build` in frontend/ first.")

datas = [(FRONTEND_DIST, "frontend_dist")]
binaries = []
hiddenimports = []

# APScheduler discovers triggers/executors via package entry points (needs its metadata);
# pdfplumber's pdfminer ships CMap data files.
for package in ("apscheduler",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden
datas += collect_data_files("pdfminer")
datas += copy_metadata("apscheduler")

# Backend modules are imported by name from the backend directory
hiddenimports += [os.path.splitext(f)[0] for f in os.listdir(BACKEND) if f.endswith(".py") and f != "serve.py"] + [
    "routes." + os.path.splitext(f)[0] for f in os.listdir(os.path.join(BACKEND, "routes")) if f.endswith(".py")
]

a = Analysis(  # noqa: F821
    [os.path.join(BACKEND, "serve.py")],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "ruff"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OutreachTool",
    console=True,
    upx=False,
)
