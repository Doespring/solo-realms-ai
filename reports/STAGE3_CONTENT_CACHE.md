# Stage 3 — content verification cache, source freeze

Owner: Memory, 2026-10-18. **READY for the single failed PDF gate repeat**:
Windows DirEntry/fstat mismatch fixed and covered by a focused valid/tamper test.
Exact frozen hashes: `reports/stage3-content-cache/freeze.json`.

Changes only: `solo/content_verification_cache.py` (new), verification/hot JSON
reads in `solo/nat20_content.py` and `solo/nat20_prototype.py`, own tests/profiler.
No runtime/native semantics, DTO, UI/API, PDF hook, EXE/ZIP or live AI changes.

## Measured cause and outcome

Source `solo.prototype_ui.creation_options()` called `catalog`/`release` 11 times.
Original cProfile measurement: **8616.40 ms** total, **7996 ms** in release,
**7277 ms** in bundled_release; 19,184 repeated file byte reads, large pathlib
relative-path work. The reported historical ~11.9 s was not reused as a new
measurement: this machine's directly observed baseline is 8.62 s.
Single all-category catalogue before: **1298.59 ms profiled**, **843.87 ms** repeated.

Matched options measurements with UI result LRU cleared:

| Condition | First call, cProfile | Next call, no cProfile | SHA files first/next |
|---|---:|---:|---:|
| Original source | 8616.40 ms | not recorded | repeated full reads |
| New empty profile/cache | 1823.55 ms | 766.46 ms | 1731 / 0 |
| New Python process, populated SQLite | 1449.52 ms | 717.36 ms | 0 / 0 |

Separate source verification measurement with populated SQLite:
`release(RELEASE)` **71.80 ms**, `catalog('classes')` **47.13 ms**, zero file SHA.
Full options after content warming **944.16 ms**. Variation is recorded, not hidden.
Imports alone measured **~984–1033 ms**, separate from the options function.
Thus **<500 ms full cold screen is not achieved**. Content verification is well
below that target; do not claim a sub-500 ms overall app startup from these numbers.

Remaining boundary for root **before any edits outside ownership**:
`solo/prototype_ui.py:creation_options` / `catalogue_rows` still invoke 11 catalogue
pages; constructing/parsing ~3084 JSON reads and full option/choice projections
remains visible in the profile (~642 ms profiled in read_json). That function's
existing result LRU returns near-zero on repeat but is not a new cold measurement.
Batching/paginating UI/options or changing its invalidation belongs to its owner.
No such API/UI edit was made here. PDF hashing callers outside the owned Nat20
content seam were not silently rewritten; the reusable verifier accepts any suffix,
but this change wires only pinned `.py`/`.json` trees.

Reproduce from project root, explicit fresh test profile:

```powershell
.venv/Scripts/python.exe -X utf8 -B tests/profile_content_cache.py --profile '<absolute new synthetic profile>' --output reports/stage3-content-cache/measurement.json
```

Run twice with the same profile for empty/persisted cases. This script starts no
app/server and makes no provider calls. `.prof` files and compact timing JSON are
inside `reports/stage3-content-cache`; profile data are disposable cache only.

## Verification boundary

SQLite path: **explicit** `SOLO_DATA_DIR/cache/content-verification-v1.sqlite3`.
No explicit absolute profile (or protected old project data path) means no persistent
cache. Broken/unwritable SQLite falls back to SHA verification, never cached trust.

Every call enumerates the full requested tree with scandir. The exact set is checked
against the existing caller pin: new/deleted/renamed paths, traversal, symlinks and
Windows reparse entries fail. Pin/release/edition/provenance validations remain in
the existing caller. Old fighter fingerprint is unchanged:
`374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75`.

Previously verified same root/path/expected SHA and same size/mtime_ns/ctime_ns/file
metadata can reuse SHA results. A changed file gets full SHA before success.
Only successful full-tree checks populate SQLite/in-process receipts. Invalid cache
timestamps/row types, negative/future filesystem timestamps, corrupt DB and changed
roots/pins cannot make an unverified file trusted. Parsed JSON values are detached
for each consumer so caller mutation cannot poison later reads.

Windows fix: DirEntry commonly has inode/device zero and ctime creation semantics,
whereas fstat supplies nonzero IDs and different ctime semantics on Python 3.12.
Directory-to-opened-file comparison therefore uses **size + mtime_ns** only.
Within one open handle, full fstat size/mtime/ctime/inode before/after must match.
Tests explicitly simulate these differing representations: valid file PASS,
subsequent tamper FAIL; read-time change also FAIL.

This is **metadata-based reuse of a prior cryptographic check**, not cryptographic
verification of every read. Deliberate same-size writes with restored timestamps,
hostile cache-database forgery, concurrent replacement after verification and a
compromised pin remain outside this performance-cache threat model. Windows
directory IDs may be zero; do not claim inode-based security there. A cold missing
cache always hashes bytes first; no guessed/missing manifest acceptance.

## Tests / handoff

`22 passed, 1.53 s`, only two existing Lark warnings:

```powershell
$env:SOLO_TEST='1'
$env:SOLO_DATA_DIR='<absolute new synthetic profile>'
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -X utf8 -B -m pytest tests/test_content_verification_cache.py -q -p no:cacheprovider --basetemp '<new task temp directory>'
```

JUnit: `reports/stage3-content-cache/tests.xml`. `git diff --check` for owned
tracked files passed. No full regression/build. Root may now ask AI to repeat only
its previously failed positive PDF gate against these exact frozen sources; this
report itself does not claim the PDF gate passed.

The code is frozen for root's `Solarimus AI` source copy. This owner did not overwrite
the shared manual copy/SOURCE_MANIFEST while root owns that integration operation.
No personal campaign SQLite/keys were opened. Cache SQLite lives only in new
synthetic profiles. Cleanup status and any policy block are in the final handoff.

Cleanup attempted once with resolved direct-child checks under this task stand;
automatic policy rejected recursive removal (`blocked by policy`). No workaround.
Remaining own synthetic directories: `profile`, `final-profile`, `tests-01`,
`tests-final`, directly under `reports/stage3-content-cache`. All workers/benchmarks
completed; no own running server/process. Keep timing JSON, JUnit and freeze evidence;
the four cache/test directories can be removed after acceptance when authorized by
the environment. No other task cleanup attempted.
