# PyInstaller spec for the Windows build. Run from the repo root:
#   pyinstaller packaging/vlclone.spec --noconfirm
# Expects build/vlc (packaging/fetch_vlc.py) and build/vlclone.ico.
from pathlib import Path

ROOT = Path(SPECPATH).parent
VLC_DIR = ROOT / "build" / "vlc"

# Ship libVLC + plugins as plain files under _internal/vlc; they are loaded at
# runtime by python-vlc, so PyInstaller must not try to analyse them.
datas = [
    (str(p), str(Path("vlc") / p.parent.relative_to(VLC_DIR)))
    for p in VLC_DIR.rglob("*")
    if p.is_file() and p.name != "vlc-cache-gen.exe"
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    datas=datas,
    excludes=["tkinter", "unittest", "pytest", "PySide6.QtNetwork", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.Qt3DCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VLClone",
    icon=str(ROOT / "build" / "vlclone.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "build" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="VLClone", upx=False)
