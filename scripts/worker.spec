"""arm64 onedir worker with its runtime libraries and package resources."""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


root = Path(SPECPATH).parent
datas = (
    collect_data_files("aaf_qc_worker", includes=["rules.json"])
    + copy_metadata("aaf-gate-worker")
    + collect_data_files("reportlab", includes=["fonts/Vera*.ttf", "fonts/bitstream-vera-license.txt"])
    + collect_data_files("_soundfile_data", includes=["COPYING"])
)

analysis = Analysis(
    [str(root / "scripts" / "worker_entry.py")],
    pathex=[str(root / "src")],
    # Keep soundfile's wheel-provided libsndfile beside _soundfile_data.
    binaries=collect_dynamic_libs("_soundfile_data"),
    datas=datas,
    # pyaaf2's model definitions are Python modules, including its extensions.
    hiddenimports=collect_submodules("aaf2.model"),
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="aaf-qc-worker",
    console=True,
    target_arch="arm64",
    codesign_identity=None,
    upx=False,
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    name="aaf-qc-worker",
    upx=False,
)
