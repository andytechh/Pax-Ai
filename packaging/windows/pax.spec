# PyInstaller spec for PAX. Build on Windows from the repository root.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata
from PyInstaller.building.datastruct import Tree

ROOT = Path(SPECPATH).resolve().parents[1]
hidden, datas, binaries, trees = set(), [], [], []

packages = (
    "PyQt6", "google.genai", "google.auth", "googleapiclient", "sounddevice",
    "numpy", "cv2", "PIL", "mss", "pyautogui", "pyperclip", "psutil",
    "playwright", "fastapi", "uvicorn", "cryptography", "win32com", "win32crypt",
    "comtypes", "pywinauto", "pycaw", "wmi", "qrcode", "google_auth_oauthlib",
    "certifi", "httpx", "requests", "yt_dlp", "pdfplumber", "pptx", "docx",
    "openpyxl", "pynvml", "ddgs", "send2trash", "pythoncom", "win32api", "openwakeword",
)
for package in packages:
    try:
        hidden.update(collect_submodules(package))
    except Exception:
        pass

# These are discovered at runtime by scanning the filesystem, so preserve them
# as real files in the frozen application's _MEIPASS directory as well as
# including their importable modules in the PYZ archive.
for package_dir in ("actions", "plugins", "core", "dashboard"):
    path = ROOT / package_dir
    if path.exists():
        hidden.update(collect_submodules(package_dir))
        trees.append(Tree(str(path), prefix=package_dir))

for rel in ("config/jarvis.ico", "dashboard/static"):
    path = ROOT / rel
    if path.is_file():
        datas.append((str(path), str(Path(rel).parent)))
    elif path.is_dir():
        trees.append(Tree(str(path), prefix=rel))

for package in ("google-genai", "PyQt6", "certifi", "playwright", "yt-dlp"):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

for package in ("PyQt6", "sounddevice", "cv2", "numpy", "cryptography", "pywin32", "openwakeword"):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hidden.update(h)
    except Exception:
        pass

hidden.update({"main", "pax_entry", "pax_wake_listener", "plugins._gmail_core", "core.desktop.controller", "win32crypt", "win32api", "pythoncom", "pywintypes"})
# Keep main.py as a resource for the existing in-app restart action.
datas.append((str(ROOT / "main.py"), "."))
MODE = __import__("os").environ.get("PAX_BUILD_MODE", "onedir").lower()
if MODE not in ("onedir", "onefile"):
    raise ValueError("PAX_BUILD_MODE must be onedir or onefile")
a = Analysis(
    [str(ROOT / "pax_entry.py")], pathex=[str(ROOT)], binaries=binaries,
    datas=datas, hiddenimports=sorted(hidden), hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["tkinter.test", "pytest", "IPython", "notebook"],
    noarchive=False,
)
for tree in trees:
    a.datas += tree

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [] if MODE == "onedir" else a.binaries,
    [] if MODE == "onedir" else a.datas, [], name="PAX", debug=False,
    exclude_binaries=(MODE == "onedir"),
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "config" / "jarvis.ico") if (ROOT / "config" / "jarvis.ico").exists() else None,
)
if MODE == "onedir":
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="PAX")
