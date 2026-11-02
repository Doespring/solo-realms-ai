# Prototype host and integration — 2.2.0-prototype.1

Source launch: Desktop.cmd (ordinary cloud configuration) or Prototype-Offline.cmd
(explicit mock). Same local backend/frontend, one window. Explicit CLI:
desktop_main.py --experimental --offline-mock --data-dir <new empty QA folder>.
The profile resumes only with its marker; project ROOT/data and legacy import are refused.
Default profiles: %LOCALAPPDATA%/SoloRealmsAI-Prototype-1 and the separate -Offline variant.
No keys are copied; ordinary cloud startup does not perform inference.

create_app(db,llm=None,*,local_port=8000,experimental=False,offline_mock=False) keeps
existing routes. The experimental factory registers actual prototype_ui and prototype_gm
services. /api/prototype/status reports both registered. Ordinary module import has no
app/database side effects without explicit SOLO_DATA_DIR; SOLO_TEST=1 disables the default.
Source browser alternative: scripts/start_cloud.py --data-dir <new folder> --offline-mock.

Build entry: scripts/build_prototype.ps1. One new dist/SoloRealmsAI-Prototype directory,
one versioned portable ZIP/checksum; existing dist/SoloRealmsAI and Preview10 stay intact.
The spec includes the pinned engine, full canonical corpus, explicit prototype manifest,
old minimal replay release, licenses, local assets, prompts and web interface. No installer.
EXE ordinary mode starts unconfigured; Offline-Mock.cmd chooses separate mock profile.

Current acceptance evidence is in reports/prototype-5/STATUS.md and release report.
Common full was run once: 1140 passed / 15 failed. Targeted corrections are recorded
separately, never described as a clean rerun of the entire suite. Native packaging and
window validation are separate gates. Source UI/HTTP checks alone do not prove the EXE.

Preservation incident stays open; no source data/keys are opened for QA. Protected 48
file/missing states are checked by hashes. QA profiles are isolated in reports/prototype-5.
Prior policy-blocked TEMP deletion is not retried/bypassed. Current temporary profiles
are retained until their release gate completes; final evidence and incident remain.
