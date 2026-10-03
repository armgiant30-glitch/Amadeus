# DSH review: Yachiyo Companion art pack

Status: preflight review passed; no blocking defect found.

## Contract

- Pack path: assets/companion/yachiyo
- Emotions: 11
- Mouth profiles: 15
- idleStatic: present for all 15
- crying references: none
- absolute paths in manifest: none

## Emotion bridge

- New aliases and fallbacks cover surprised, shy, excited, confused, sleepy, smug and worried.
- Generic surprise keeps the legacy sided_surprised text result.
- Yachiyo maps sided_surprised to the new front-facing surprised.
- Kurisu remains compatible because its pack retains sided_surprised.
- Unknown emotions fall back to normal.

## Mouth and transition

- Every emotion exposes mouth-closed.webp and an ROI.
- Speech-loop emotion changes are deferred until the loop boundary.
- Speech end applies a pending switch immediately.
- Card interpreter smoke: normal speaking -> pending worried -> switched at loop boundary -> worried/mouth-closed.webp selected.

## Verification

- Related pytest suite: 62 passed, 3 warnings.
- Card Python interpreter has tkinter and passed the direct AtlasPlayer smoke.
- Full pytest UI run in the card interpreter remains optional because that interpreter has no pytest installed.

## Residual risk

- This version uses deterministic cross-state switching, not new in/out transition video frames.
- True Kurisu-style transition takes would require additional transition assets.
