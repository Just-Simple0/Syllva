# PyInstaller spec for the Core sidecar (onedir; implementation spec §13.6, R2-5 spike).
# Build: pyinstaller --noconfirm --clean packaging/syllva-core.spec  ->  dist/syllva-core/
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("uvicorn")          # loops/protocols are imported by name at run time
    + collect_submodules("keyring.backends")
    + collect_submodules("syllva.core")
    + collect_submodules("syllva.ui")
    + collect_submodules("syllva.state.v2")
    + collect_submodules("syllva.intake.v2")
)
datas = collect_data_files("syllva", includes=["**/*.sql", "ui/static/*"])

a = Analysis(
    ["core_entry.py"],
    pathex=[],
    hiddenimports=hidden,
    datas=datas,
    excludes=["tkinter", "gi", "PyQt5", "PySide6", "matplotlib", "numpy", "PIL", "mcp", "syllva.mcp", "syllva.adapters", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="syllva-core", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="syllva-core", upx=False)
