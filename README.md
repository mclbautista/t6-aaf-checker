# AAF Gate worker

The native SwiftUI shell is in `app/`. Build the signed Apple Silicon app with
`./scripts/build_app.sh`; see [build and verification instructions](docs/build.md)
for Command Line Tools requirements, Swift tests and the synthetic drop checklist.

Python 3.12 worker for AAF Gate. Install and run from the repository root:

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/aaf-qc-worker check /path/to/synthetic.aaf
.venv/bin/aaf-qc-worker compare --flagged /path/to/flagged.aaf --corrected /path/to/corrected.aaf
.venv/bin/python -m pytest -q
```

The worker writes flushed NDJSON to stdout. Every event has `schema_version: 1`
and a `type`: `run_started`, `progress`, `finding`, `diff_item`, or `terminal`. Paths identify
the input AAF. The terminal verdict is `PASS`, `FAIL`, or `ERROR`; an error
includes a `reason`. Exit codes are 0, 1, and 2 respectively.

Both modes run the same read-only container, timeline, and audio checks. Media
is streamed in fixed-size chunks. The bundled `src/aaf_qc_worker/rules.json`
defines the rule version, thresholds, expected format, metadata field map,
severities, and fix text.

Compare's `run_started` carries `mode: "compare"`, `flagged_path`, and
`corrected_path`. Progress and finding events retain `aaf_path` and add
`aaf_role: "flagged"` or `"corrected"`. Both inputs get a full check, even if
one cannot be read. The terminal event carries `flagged_verdict` and
`corrected_verdict` as well as the overall verdict. Completed comparisons also
carry `paired_clips` and `blocking_regressions`.

Clips pair by media mob ID, then any locator basename with agreeing clip name,
then clip name and source length. All keys include exact delivered-media
in-point and track type. Duplicate keys pair by track slot and structural order,
independently of record position. Different edit rates or fewer than half the
clips paired (on either side) produce ERROR.

Each `diff_item` has `kind` and `blocking`. Finding kinds are `resolved`,
`persisting`, and `new`; these carry `rule_id`, `flagged_finding`, and
`corrected_finding` (null for an absent side). `added` and `dropped` items carry
`aaf_role`, `aaf_path`, the clip location fields, and nested `findings`. Dropped
clip findings are not classified as resolved. File findings come first, then
items in corrected position order, then dropped clips in flagged position order.

Compare passes only when the corrected check passes and no clips were added or
dropped. Position, source-slip, length, track, handle, and content change
detection belong to tickets #16/#17. Report generation also belongs to a later
ticket; `report_paths` is currently an empty list.

A `check` that ends FAIL, with at least one FAIL finding on a clip, also writes
marker files beside the reports and lists them in the terminal event's
`marker_paths` (the field is absent otherwise; `report_paths` is unchanged):
`<name>_AAFGate_FAIL_<stamp>_PremiereXML.xml` (FCP 7 XML sequence with markers),
`..._PremiereCSV.csv` (Premiere Markers panel columns, tab-delimited) and
`..._Avid.txt` (tab-delimited marker list: user, timecode, track `TC1`, colour,
comment, duration). Channels of one clip share one marker. File-level findings
have no timecode and make no marker.

Tests invoke only the installed command as a subprocess. All AAFs are built
synthetically in pytest temporary directories; size boundaries use sparse
padding. Compare tests use small timelines, regenerated mob IDs, embedded and
linked exports, duplicate uses, and defective variants, asserting only the CLI
events.
