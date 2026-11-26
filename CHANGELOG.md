# Changelog

## 3.2.0 — 2026-10-16

- Compact view with the latest answer, a small composer, separate window geometry, a title-bar toggle and Ctrl+Alt+C. Full conversation and drafts survive toggling.
- Remembered screen region: Ctrl+Alt+R reuses it, Ctrl+Alt+Shift+R selects a new one; the Area menu can replace or clear it. Invalid regions never fall back to a full-screen capture.
- Push-to-talk via the Voice button or Ctrl+Alt+V. Release submits, Esc in the app or Stop cancels. Local Whisper is shared with passive listening; manual questions take priority over queued passive audio. Typed drafts are preserved. Recordings are bounded at 45 seconds and discarded with a message if that limit is reached.
- Qt queued wakeups deliver worker events without waiting for the 20 ms poll. The first answer chunk renders immediately; hidden chat views pause streaming renders. Default audio devices skip unnecessary device enumeration.
- Error text uses at most three lines with full details in its tooltip, keeping the compact view usable.

## 3.1.0 — 2026-10-16

- OpenAI Responses API support with GPT-5.4 mini by default and GPT-5.5 for Solve / Deeper. Anthropic profiles remain available.
- Provider selection in the connection dialog, separate DPAPI key files, and key replacement in Settings → Model.
- Shared conversation lifecycle, image support, portable text history between providers, cancellation and failed-request rollback.
- Provider-aware mode presets, summaries, error messages and token pricing, including OpenAI cache accounting and GPT-5.5 long-context rates.
- Python 3.14 installation compatibility for onnxruntime; setup stops on dependency installation failure.
- Russian quick start and a separate list of unimplemented future ideas.

## 3.0.0 — 2026-10-16

The window is rewritten on Qt (PySide6) and the app is split into a toolkit-free core and the window.

- **Window.** Own title bar with a pin, minimize and close; native resizing, snapping and shadow kept. Dark theme with a honey accent. Chat with message cards, highlighted code, rendered formulas and tables, a caret while an answer streams, actions under each answer. Editable Heard panel, a question box that grows as you type; both can be dragged taller. Status line with a dot, a sound level meter, context and budget meters, session cost and model download progress. Mode chips. Settings window, profile manager, first-start dialog with an API key check, tray icon, close to tray, text zoom.
- **Bobo**, a pixel-art bear: window and tray icons, animated states (asleep, listening, hearing, thinking, writing, happy, error, curious, waving, on air), a still logo in the title bar, a big Bobo in an empty chat that starts listening on click.
- **Recap** of the last 2, 5 or 15 minutes and a **call summary** (`Ctrl+Alt+M`), the summary savable as Markdown.
- **Profiles** with settings, notes about you and txt/md/pdf documents, sent to Claude as a cached system block.
- **Cost.** The cost of each answer, an estimate before sending, a context meter, an optional monthly budget.
- **Screen sharing.** Every window of the process (menus, tooltips, dialogs, the area overlay) is hidden from capture. An explicit visible mode with a red banner and an on-air Bobo.
- **Audio.** Choice of output device and microphone, a live level meter.
- **Hotkeys** are recorded in the settings; a new one for the recap.
- **Speed.** The window opens in under a second (the Anthropic SDK and the audio library load off the startup path).
- **Code.** Packages by domain (`core`, `speech`, `claude`, `win`, `gui`), absolute imports, dead code removed, 122 unit tests including off-screen Qt tests.
- **Removed.** The Tk window and its renderer.

## 2.1 — 2026-10-15

- Recording moves to a new default audio device when the current one disappears (unplugged headphones, Bluetooth profile switch).
- Whisper hallucinations are dropped sentence by sentence instead of discarding the whole phrase.
- A question that failed or was stopped goes back to the window so it can be sent again.
- A failing UI callback no longer stops the other threads from reaching the window.

## 2.0 — 2026-10-14

- Code split into the `overlay` package; English codebase; English and Russian interface.
- Whisper on the GPU with CPU fallback; question detection in the transcript; speaker labels for PC + mic.
- Answer modes (quick, interview, explain, solve), follow-ups, math and tables rendered as aligned text.
- API key moved from `.env` into DPAPI-encrypted storage; the window hidden from screen capture.
- README rewritten for users, technical details moved to `docs/DEVELOPMENT.md`.

## 1.0

The first single-file prototype on Tkinter: listen to PC audio, transcribe with Whisper, ask Claude.
