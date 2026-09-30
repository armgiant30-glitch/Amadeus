# Companion-only startup — 0.15.x

Companion-only is a startup mode, not a wallpaper modifier. One launch owns
exactly one visible surface: the repository-owned Tk Companion card in
`render/vn_overlay_window.py`, launched through
`tools/vn_portrait_overlay_lite.py`. It reuses the shared session, context,
Qwen3-ASR, GPT-SoVITS, subtitles and interruption behavior; it never starts the
wallpaper host, the Electron Slice, the Canvas surface, or a visible main UI.

## Entry points

| Surface | Command |
|---|---|
| Desktop launcher | `run_amadeus_companion.bat` |
| Electron dev | `electron . --companion` (or `AMADEUS_COMPANION=1`) |
| Backend only | `python -m server.app --port 17777 --companion` |
| Environment | `AMADEUS_COMPANION=1` |

`--companion` / `AMADEUS_COMPANION=1` resolve to `StartupMode = 'companion'` in
`electron/src/main/startupMode.ts`. Companion mode is checked before the Windows
wallpaper preference, so an enabled wallpaper setting can never pull the
wallpaper host into a companion launch. `--wallpaper` and
`AMADEUS_WINDOWS_STARTUP_MODE` keep their existing meaning for every other mode.

## What companion mode skips

- `windowsWallpaper.start()` — the wallpaper host is never created
  (`STARTUP_MODE === 'wallpaper'` gates the session and the tray).
- `createElectronSliceWindow()` and `createElectronCanvasWindow()` — both return
  early, and the `electron-slice.open` IPC refuses before it can reach them.
- The visible main window — it is still created as a hidden bridge client so the
  Companion bridge descriptor and back-end session stay intact, but it is never
  shown by startup.
- Work, tools, Provider, permission and AUIP context — the backend runs with
  `--companion`, which turns off the cooperative/Work-planner lane and AUIP
  narration. Ordinary chat therefore creates no WorkItem. The session store,
  conversation history and context assembly are untouched.

## Card ownership and lifecycle

The backend owns the card process (`server/companion_runtime.py`):

- `CompanionCardHost.ensure_running()` probes port 8788 first. A card that is
  already serving is adopted as the presentation surface, so a repeated start
  never creates a second card.
- Otherwise the host spawns `tools/vn_portrait_overlay_lite.py` with
  `--on-close card-close`, waits for `/health`, and publishes its `/reaction`
  endpoint as the speech surface.
- `set_visible(false/true)` drives the card's own `/visibility` endpoint and
  `focus()` drives `/focus`; the tray in `electron/src/main/companionTray.ts` is
  the only visible control for a hidden main window.
- The card's audio boundary events still arrive through the shared TTS bridge:
  `server/vn_tts_bridge.py` falls back to the published card endpoint when a
  speech payload names no session overlay. An explicit `overlay_url` from a VN
  or reading session always wins.

Closing the card (right-click, or the Tk window close) posts
`/companion/card-close`. The owning backend stops the card and exits, which
stops Electron and releases the TTS/ASR resources it owns. If the backend was
already running before this Electron launch, Electron adopts it and exits
without stopping it, so a DSH/desktop backend is never killed by a companion
card close.

## Single instance

`app.requestSingleInstanceLock()` still owns process identity. A second
companion launch does not create a second card: the first instance receives
`second-instance` and calls `/companion/card/focus`, which shows and raises the
card it already owns.

## Verification commands

```powershell
# Electron launch-mode and startup-surface contracts
cd electron
node --experimental-strip-types --test tests/startupMode.test.mjs tests/startupWindowAccess.test.mjs

# Full desktop suite
node --experimental-strip-types --test tests/*.test.mjs

# Type check
node .\node_modules\typescript\bin\tsc --noEmit

# Companion routing, credential hand-off and close ownership
python -m pytest tests/test_companion_only_runtime.py tests/test_companion_card_close.py -q
```

Manual acceptance:

1. Start `run_amadeus_companion.bat`. Task Manager shows no `wallpaper64.exe`
   and no `webwallpaper64.exe`; no Slice, Canvas or main window appears.
2. The Tk card is visible, topmost, draggable, and its caption follows speech.
3. Speak into the microphone: ASR input, the LLM reply, TTS playback, subtitles
   and barge-in interruption behave as in the desktop app.
4. Ordinary chat creates no WorkItem in `runtime/work_ledger.sqlite3`.
5. Close the card. No orphan `python.exe` or `Electron` process remains.
6. Launch a second instance while the first runs. Only one card exists; the
   second launch surfaces the existing one.
