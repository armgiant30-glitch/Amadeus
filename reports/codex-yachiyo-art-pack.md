# Codex report: Yachiyo Companion art pack

Status: core implementation complete; DSH independent review pending.

## Deliverable

- Installed pack: assets/companion/yachiyo
- 11 emotions, no crying.
- Character profile art_dir now points to assets/companion/yachiyo.
- Added normal-style idleStatic for every emotion.
- Added manifest aliases/fallbacks for Kurisu compatibility.

## Emotion bridge

- New text hints: excited, shy, confused, sleepy, smug, worried, surprised.
- Legacy emotion keys remain compatible.
- Kurisu generic surprise remains sided_surprised.
- Yachiyo maps sided_surprised to the new front-facing surprised.
- Unknown emotions fall back through pack-specific candidates and finally normal.

## Mouth and speaking transition

- Every emotion has mouth-closed.webp, ROI and closedFrame.
- AtlasPlayer now defers emotion changes during an active speaking loop until the loop boundary.
- Speech end applies the pending transition immediately.
- Lite overlay uses resolve_emotion and request_select.

## Verification

- 51 tests passed, 3 warnings.
- Card interpreter smoke passed: Yachiyo pack loaded, 11 emotions, deferred switch normal -> worried, mouth closed asset selected.
- Pack loader, rollback, deferred speaking switch, mouth overlay selection and memory/reading regressions covered.
- DSH preflight completed on the desktop Python interpreter; full pytest UI run remains optional because that interpreter has no pytest.

## DSH review focus

- 15 emotion manifest consistency.
- No crying references and no absolute paths.
- ROI/closedFrame behavior at mouth=0, 1, 0.08.
- Continuous emotion switching and speaking boundary behavior.
- Real card switch between Kurisu and Yachiyo.
