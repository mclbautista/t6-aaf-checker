# AAF Gate worker

Python 3.12 worker for AAF Gate. Install and run from the repository root:

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/aaf-qc-worker check /path/to/synthetic.aaf
.venv/bin/python -m pytest -q
```

The worker writes flushed NDJSON to stdout. Every event has `schema_version: 1`
and a `type`: `run_started`, `progress`, `finding`, or `terminal`. Paths identify
the input AAF. The terminal verdict is `PASS`, `FAIL`, or `ERROR`; an error
includes a `reason`. Exit codes are 0, 1, and 2 respectively.

This first slice checks Container corrupt using the raw CFB header and file
size. Valid headers are also opened read-only with pyaaf2 to catch unreadable
AAF structures. A corrupt header produces one file-level finding, with null
track, timecode, clip name, and source file fields. It does not attempt timeline
inspection after a corrupt header. The bundled `src/aaf_qc_worker/rules.json`
defines the rule version, sector sizes, size limit, severity, and fix text.

Progress currently counts the header bytes read, with zero clips inspected.
Timeline and audio checks, comparison, and report generation belong to later
tickets; `report_paths` is currently an empty list.

Tests invoke only the installed command as a subprocess. All AAFs are built
synthetically in pytest temporary directories; size boundaries use sparse
padding. Tests cover all three corruption conditions, valid v4 files, unreadable
inputs, unchanged SHA-256 hashes, and deterministic events.
