"""PyInstaller entry point, using the same CLI as the installed console script."""

from aaf_qc_worker.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
