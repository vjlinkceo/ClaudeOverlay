# ChatGPT Overlay: development notes

Technical reference: requirements, the project layout, how the pieces work, every setting, privacy details, and how to check changes. For everyday use, see the [README](../README.md); for what changed between versions, [CHANGELOG.md](../CHANGELOG.md). The project is proprietary: see [LICENSE](../LICENSE) before sharing any of it.

## Requirements

- Windows 10 version 2004 or newer: hiding from screen capture needs `WDA_EXCLUDEFROMCAPTURE`.
- Python 3.12 or newer. Python 3.14 uses onnxruntime 1.31.0 because 1.21.1 has no compatible wheel; earlier versions retain 1.21.1.
- PySide6 (Qt 6.12) for the window. `PySide6-Essentials` is enough: no WebEngine, no QtSvg.
- Optional: an NVIDIA GPU with a recent driver. CUDA comes from pip (`requirements-gpu.txt`), so no CUDA toolkit is needed. The cuDNN version must match the one ctranslate2 was built against; it is 9.10.2.21 for ctranslate2 4.8.x. Upgrade both together.
- onnxruntime (the speech detector) is pinned to 1.21.1 on Python < 3.14 and 1.31.0 on Python >= 3.14. From 1.22 on, its import enumerates hardware devices for about 10 s while holding the GIL, which froze the window on the first Listen. Time `python -X importtime -c "import onnxruntime"` before upgrading it.

## Project layout

```
app.py                    entry point (start.bat runs it)
overlay/
  main.py                 startup: process setup, then the Qt application
  bootstrap.py            logging, CUDA library paths, Hugging Face settings
  config.py               file paths, defaults, config.json validation, model and mode presets
  i18n.py                 English and Russian interface strings
  core/                   application logic that needs no toolkit
    controller.py         all orchestration; talks to the window through the View protocol
    scheduler.py          the Scheduler protocol (after / after_cancel) that timers go through
    uiqueue.py            hands callbacks from worker threads to the UI thread
    autoanswer.py         when to send automatically
    meeting.py            everything heard in the session (memory only), for recaps and the call summary
    profiles.py           profiles: settings, notes and documents as plain files, the cached system block
    pricing.py            what an answer cost, and what the next one will roughly cost
    labels.py             option labels in the interface language
  speech/                 from sound to text
    audio.py              audio capture, device choice, signal level, phrase splitting
    vad.py                streaming Silero voice activity detector
    stt.py                Whisper: GPU/CPU, language choice, glossary, hallucination filter, download progress
    transcript.py         heard speech since the last question: utterances, question detection, echo filter
  ai/                     shared AI code
    base.py               conversation lifecycle, cancellation, history, stats, rollback on errors
    client.py             provider routing, portable history and deferred key reload
    openai_client.py      OpenAI Responses API, images, streaming, usage normalization
    prompts.py            shared prompts, answer modes, follow-ups, recaps and summaries
    keys.py               provider-specific .env and DPAPI storage
  claude/
    client.py             Anthropic transport and fast-mode fallback
    prompts.py, keys.py   compatibility exports for older imports
  win/                    Windows specifics
    winapi.py             cursor, hiding windows and menus from screen capture, saved geometry
    hotkeys.py            global hotkeys (RegisterHotKey)
    screen.py             screenshots and image encoding
  gui/                    everything that imports PySide6
    app.py                QApplication, stylesheet, QtScheduler, the capture guard for every window of the process
    window.py             MainWindow: the View implementation, layout, tray, settings, profiles wiring
    frameless.py          the window frame: Windows resizing and snapping without the system title bar
    theme.py              colors and the Qt stylesheet
    icons.py              window and tray icons from the mascot sprites
    mascot.py             the animated (or still) Bobo widget
    tray.py               the tray icon and its menu
    region.py             drag-to-select screen area
    chat/
      view.py             the conversation in a QTextBrowser: cards, streamed answers, actions under answers
      render.py           markdown -> HTML for QTextDocument (mistune, pygments, formula placeholders)
      mathimg.py          LaTeX -> PNG through matplotlib mathtext
      mathtext.py         LaTeX -> plain text, the fallback for what mathtext cannot draw
    panels/
      heard.py            the editable Heard panel
      composer.py         the question box: Enter sends, images by paste or drop
      status.py           status line: dot, text, level meter, context and budget meters, download progress
    dialogs/
      settings.py         the settings window
      profiles.py         the profile manager
      first_run.py        the first start: API key check, audio source
      hotkey_edit.py      a button that records a key combination
  assets/
    bobo.py               Bobo's sprites as text, rendering, the contact sheet and the .ico builder
    bobo.ico              the Windows icon (generated: python -m overlay.assets.bobo --ico overlay/assets/bobo.ico)
tools/preview_window.py   renders the window with sample content to PNG files, for docs and visual checks
tests/                    unit tests for the logic (no audio or network; off-screen Qt smoke tests)
docs/                     this file and the screenshots
```

Imports are absolute (`from overlay.core.controller import AppController`), so a module can be moved without touching its body.

## How it works

**Controller and View.**
- `core/controller.py` owns the state (config, the AI client, transcripts, the meeting log, the profile, listeners, auto-answer) and every decision: what to send, what to put back after a failure, when to restart the chat, when to clear. It never imports Qt.
- The window implements the `View` protocol (`set_status`, `refresh_heard`, `begin_answer`, `update_answer`, `finish_answer`, `select_region`, …). `tests/test_controller.py` drives the controller with a fake view and a fake chat; `tests/test_gui_smoke.py` builds the real window off-screen.
- Timers go through the `Scheduler` protocol (`after`, `after_cancel`): `QtScheduler` in the app, a fake clock in tests.

**Speech to text.**
- `speech/audio.py` records PC audio (WASAPI loopback of the default output, or the device chosen in the settings) and/or the microphone in 32 ms frames, and the Silero VAD marks speech. Every third frame reports the signal level for the meter in the status line.
- When the default device changes, or the device breaks while recording (headphones unplugged, a Bluetooth profile switch), recording moves to the new default device and the cut phrase is still transcribed. After 10 s without a working device the listener reports an error; with PC + mic the other source keeps listening. A device chosen explicitly is followed instead of the default while it is present.
- A phrase ends after `end_silence` seconds of silence.
- Speculative transcription: as soon as a pause starts, the phrase is sent to Whisper. If the pause becomes the end of the phrase, that result is reused, so text appears about 0.6–0.9 s after speech ends on an RTX-class GPU.
- During long speech a live partial is transcribed about once a second. Monologues are cut at 15 s at the quietest point.
- `speech/stt.py` runs `large-v3-turbo` in float16 on the GPU, or `small` in int8 on the CPU. If the GPU fails, it falls back to the CPU automatically. While a model downloads, the controller polls the Hugging Face cache size against a known model size for the progress bar.
- Language detection is limited to Russian, Ukrainian and English and smoothed between phrases. Terms go to Whisper as `hotwords`; the topic and the previous phrase go in as `initial_prompt`.
- Known hallucinations ("thanks for watching", subtitle credits, repeated words, echoes of the prompt) are dropped sentence by sentence. A sentence that only mentions such words, and any question, is kept.

**Finding the question.**
- `speech/transcript.py` keeps what was heard since the last question: timestamped lines, merged per speaker and labeled `Them`/`Me` in PC + mic mode. Echo of the speakers in the microphone is removed by word overlap.
- The Heard panel is editable. Once the user changes its text, the panel becomes the source of truth (`View.heard_override`) and new phrases are appended as plain lines until the text is sent or cleared.
- `ai/prompts.py` sends this transcript with rules for picking the latest question addressed to the user, plus an `<answer_style>` block for the selected mode.
- Auto-answer (`core/autoanswer.py`) fires 0.4–1 s after a phrase that ends with a question or a request, and gives up if the speaker keeps talking for 8 s.

**Recaps and the call summary.**
- `core/meeting.py` keeps every final phrase of the session (copies, so merging in the transcript does not touch them). Recap takes the last N minutes, the call summary everything; both go through `build_summary_content` as a `<transcript_window>` with a task instead of a question. The recap uses the current model at low effort, the summary GPT-5.4 mini (OpenAI) or Sonnet (Anthropic) at medium.
- The log is wiped by New chat and the idle clear, never written by the app; "save .md" under a summary is the only path to the disk, and it is the user's click.

**Profiles.**
- `profiles/<slug>/profile.json` holds the name, the settings subset (`SETTING_KEYS`), notes and the document list; `docs/*.txt` holds the documents' text (`.txt`/`.md` copied, `.pdf` extracted with pypdf). The folder is ignored by git.
- `system_block` renders notes and enabled documents into `<profile>`, trimmed to `MAX_CHARS` (30 000). `AIChat.system_extra` passes it to the selected transport: OpenAI instructions or an Anthropic system block. Cached-input billing follows the API usage report.
- Switching profiles changes the system prompt, so a running conversation is restarted with the usual recap of the last answers. The profile manager saves on every change but notifies the window only when it closes, so typing notes does not restart the chat per keystroke.

**AI requests.**
- `ai/client.py` selects the provider from the model profile. Changing providers preserves user text and images and converts assistant replies to portable text; provider-specific thinking blocks are discarded. Busy requests finish on their original transport.
- `ai/openai_client.py` calls the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/streaming-responses) at the fixed `https://api.openai.com/v1` endpoint with `stream=True`, `store=False`, reasoning effort and a 16,000-token output limit. Both `response.completed` and `response.incomplete` terminate an answer. Interrupted, failed and empty streams roll back the question.
- OpenAI input counts include cached tokens; the adapter subtracts `cached_tokens` from the uncached bucket before pricing, preventing double counting. Output tokens include reasoning. OpenAI pre-send estimates conservatively price the whole input as uncached; GPT-5.5 prompts above 272K input tokens use the documented long-context multipliers.
- The key dialog verifies OpenAI access using `models.retrieve`, without generating a paid answer. This check does not establish billing balance or all inference permissions. The dialog ignores stale results after a key or provider change.
- `claude/client.py` streams from `api.anthropic.com` (hard-coded) with automatic prompt caching and server-side refusal fallback.
- Cancelling closes the connection, so Stop also works while the model is thinking.
- If a request fails or is stopped, the typed question, the heard transcript and the image go back to the window so they can be sent again, and auto-answer does not resend them by itself.
- Fast mode falls back to standard speed on a rate limit. A zero limit disables it for the session.
- The history is append-only: the system prompt never changes within a conversation, and the topic, reply language and style travel inside new messages. Editing earlier turns would invalidate the cache and thinking blocks, and newer API accounts reject it with a 400.
- When the conversation passes `max_history_tokens`, the next question starts a new chat that carries the last 2 answers as text.
- `core/pricing.py` turns the reported token counts into a cost with the list prices in `PRICES`; update them when either provider's price list changes. Before a question is sent, `AppController.estimate` guesses its cost without the network: tokens from UTF-8 bytes (4.5 per token, which fits both Cyrillic and Latin roughly), image tokens from pixels / 750, the system prompt, profile block and history at cache-read prices, and a typical answer length per mode plus thinking per effort. The estimate is refreshed on typing, new phrases, attachments and option changes.
- The context meter is `AIChat.context_tokens / max_history_tokens`; the budget meter is `spent_usd / budget_usd`, with `spent_usd` written to `config.json` after every answer.

**Rendering.**
- `gui/chat/render.py` turns markdown into HTML that `QTextDocument` understands: inline styles only, code boxes as one-cell tables (the document has no block padding), tables with `border` and `bordercolor`, pygments highlighting with inline spans. Formulas (`$…$`, `$$…$$`, `\(…\)`, `\[…\]`) outside code become `<img src="math://…">` placeholders.
- `gui/chat/mathimg.py` draws a formula with matplotlib's mathtext into a transparent PNG; what mathtext cannot typeset (matrices, environments) falls back to `gui/chat/mathtext.py`'s aligned text. matplotlib is imported in a background thread after start, since its first import builds a font cache.
- `gui/chat/view.py` inserts each exchange with a text cursor and re-renders only the streaming answer's range, coalesced to one render per 40 ms. While an answer streams, its start stays at the top of the view once the answer no longer fits. Actions under an answer are anchors (`act:copy:N`, `act:followup:kind`, `act:save:N`).

**The mascot.**
- When the window is not hidden from capture (`hide_from_capture` off, or the exclusion failed), the window shows a red banner with a "hide" link, and the calm mascot states map to `visible`: ears up, wide eyes, a drop of sweat, a surprised mouth and a blinking on-air lamp above the head; the tray icon gets the lamp too. Busy states (thinking, talking) are shown as they are.
- `assets/bobo.py` describes the 32×32 bust and the 16×16 face as text, one character per pixel, with the palette as a legend; states are recipes (ears, eyes, patches) rather than copied grids. `render` scales with nearest-neighbour only, and `BoboWidget` picks an integer scale for the current screen's pixel ratio. `python -m overlay.assets.bobo --preview DIR` writes a contact sheet of every state.

**Threads.**
- Recording, Whisper, AI requests and hotkeys run on their own threads and reach the window only through `core/uiqueue.py`, which the UI thread polls every 20 ms through the scheduler. A callback that fails is logged, shown in the status line and skipped, so one UI error cannot stop every other thread from reaching the window.
- A question counts as in flight from send until its done event is handled, not only while the request thread runs. Otherwise auto-answer could start the next question in that gap and the done event, with the previous question's error and unsent data, would be dropped.

**The window frame.**
- The main window uses `FramelessWindowHint` and draws its own title row (Bobo, the name, pin, minimize, close). `gui/frameless.py` puts `WS_CAPTION | WS_THICKFRAME` back on the native window, answers `WM_NCCALCSIZE` with 0 (the whole window is client area, so no system title bar appears) and `WM_NCHITTEST` with the resize edges and `HTCAPTION` for the title row, so dragging, resizing, Aero snap, the DWM shadow and rounded corners stay native.
- Changing a window flag (the pin toggles `WindowStaysOnTopHint`) makes Qt recreate the native window: the frame styles and the capture exclusion are applied again right after `show()`.
- Heard, the chat and the question box sit in a vertical `QSplitter`. Heard and the question box ask for 3 to 8 lines as their text grows (`height_hint`) until the user drags a handle; those heights are kept in `heard_height` and `composer_height`.

**Startup time.**
- The `anthropic` SDK takes about 4 s to import, so the selected transport imports its SDK on a background thread; `AIChat.configured` says whether a key exists, and `client` waits for the import only inside the request thread. `soundcard` loads on the first Listen (on the main thread, which initializes COM). Off-screen, the window shows about 0.7 s after start.
- While an answer streams, the chat re-renders its markdown at most every 40 ms, or 90 ms once the answer passes 4 000 characters; a 7 500-character answer renders in about 20 ms.

**Hiding from screen capture.**
- `win/winapi.set_capture_excluded` applies `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` to the main window before its first frame.
- `gui/app.py`'s `CaptureGuard` is an application-wide event filter: every top-level widget of the process (combo lists, menus, tooltips, dialogs, the tray menu, the area overlay) is excluded on its Show event, which Qt sends before the native window becomes visible. The WinEvent hook in `win/winapi.py` still covers the native system menu.
- When the window is hidden this way, screenshots are taken immediately, without the 90 ms opacity trick.
- Qt sets per-monitor DPI awareness itself; screenshot regions are converted from logical to physical pixels with the screen's device pixel ratio.

## Settings (`config.json`)

The window saves settings on exit. Invalid values are replaced with defaults and a warning goes to `log.txt`.

| Key | Default | Meaning |
|---|---|---|
| `ui_language` | `en` | Interface language: `en` or `ru` |
| `mode` | `quick` | `quick`, `interview`, `explain` or `solve` |
| `model` | `sonnet` | `sonnet`, `opus`, `opus_fast` or `haiku` |
| `effort` | `low` | Thinking depth: `low`, `medium` or `high` |
| `audio_source` | `system` | `system`, `mic` or `both` |
| `mic_device`, `speaker_device` | `""` | Device names; empty means the system default |
| `auto_answer` | `false` | Send automatically after a question |
| `hide_from_capture` | `true` | Hide the window from screen sharing and screenshots |
| `close_to_tray` | `true` | Closing the window hides it to the tray |
| `speech_language`, `answer_language` | `auto` | `auto`, `ru`, `uk` or `en` |
| `context`, `glossary` | `""` | The topic and terms |
| `profile` | `""` | The active profile's folder name under `profiles/` |
| `summary_minutes` | `5` | Minutes the Recap hotkey covers (1–180) |
| `whisper_model` | `large-v3-turbo` | Model on the GPU |
| `whisper_model_cpu` | `small` | Model on the CPU |
| `whisper_device` | `auto` | `auto`, `cuda` or `cpu` |
| `vad_threshold` | `0.5` | Speech detector sensitivity (0.1–0.95). Higher ignores more noise |
| `end_silence` | `0.6` | Seconds of silence that end a phrase (0.3–2.0) |
| `max_transcript_chars` | `24000` | How much recent transcript one question sends |
| `max_history_tokens` | `40000` | Conversation size that triggers a chat restart |
| `auto_clear_minutes` | `120` | Idle minutes before the chat is wiped from memory; `0` disables it |
| `screenshot_max_side` | `1920` | Screenshot size along the long side (640–2576) |
| `window_alpha` | `0.97` | Window opacity |
| `font_scale` | `1.0` | Text size (0.8–1.6); also `Ctrl` + wheel |
| `always_on_top` | `true` | The pin in the title bar |
| `heard_height`, `composer_height` | `0` | Heights of the Heard area and the question box once the user dragged a splitter handle; `0` fits the text |
| `budget_usd` | `0` | Monthly budget shown as a meter; `0` hides it |
| `spent_usd`, `spent_month` | `0`, `""` | Spending this month, kept across restarts and reset when the month changes |
| `geometry` | — | Window size and position, saved on exit |
| `hotkeys` | see README | `answer`, `screenshot`, `area`, `listen`, `toggle_window`, `summary` |

## Privacy and security details

**Network.**
- The app connects only to `api.anthropic.com`, and only when a question is sent. The first-run dialog's "Check" button makes one `count_tokens` request when clicked.
- Hugging Face is contacted once to download the speech model; later loads use `local_files_only`.

**API keys.**
- `ai/keys.py` loads each provider independently: its system environment variable, then `.env`, then its encrypted file. OpenAI uses `OPENAI_API_KEY` / `openai_api_key.dpapi`; Anthropic retains `ANTHROPIC_API_KEY` / `api_key.dpapi`.
- Plaintext `.env` keys are cleared only after successful Windows DPAPI encryption. Keys entered in the UI go straight to DPAPI. Both encrypted file patterns are ignored by Git.
- Only `HF_TOKEN` from `.env` is copied to the process environment. API endpoints are fixed in the transports.

**Data on disk.**
- Conversations, transcripts and screenshots are never written to disk by the app. A call summary is written only through "save .md".
- `config.json` stores settings, including the topic and terms. `profiles/` stores the user's own notes and documents.
- `log.txt` holds technical events only and rotates at 1 MB.

**Data in memory.** New chat and the idle auto-clear wipe the chat history, the heard transcript, the meeting log, the echo-filter buffer and Whisper's remembered phrases.

**Provider processing.** Text, screenshots, profile context and the sent conversation history go to the provider of the selected model. OpenAI requests disable response storage (`store=False`); provider-side processing and retention policies still apply.

**What the app does not do.**
- No keyboard hooks.
- It does not execute anything from answers.
- It reads the clipboard only on `Ctrl+V` in the question box.

## Checking changes

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m ruff check overlay tests tools app.py
.venv\Scripts\python -m ruff format overlay tests tools app.py
```

Screenshots for the README: `.venv\Scripts\python tools\preview_window.py out\ en` writes `window_en.png`, `settings_en.png`, `first_run_en.png` and `empty_en.png` into `out\` (copy the first two to `docs\window.png` and `docs\settings.png`); `python -m overlay.assets.bobo --preview out\` writes `bobo_sheet.png` (copy it to `docs\bobo.png`), and `--ico overlay\assets\bobo.ico` rebuilds the icon after a sprite change.

The GUI smoke test runs Qt off-screen (`QT_QPA_PLATFORM=offscreen`); `SetWindowDisplayAffinity failed: error 1400` in its output is expected there. Test and preview code must never call the controller's `shutdown` with the real config: it saves `config.json`.

Manual checks after a change to the window: the window, its menus, tooltips, the settings window and the area overlay are absent from a Teams or OBS screen share; hotkeys work on a Cyrillic layout; Ctrl+V and drag-and-drop of images; a long streamed answer keeps its ❓ line at the top; 100 % and 150 % display scaling; closing to the tray and returning with the hotkey.

Code style:
- Code and comments are in English.
- Comments explain only the non-obvious "why".
- Every user-facing string goes through `i18n.t()` with both `en` and `ru` entries; a test checks that the keys and placeholders match.

The local git `pre-commit` hook (`.git/hooks/pre-commit`) blocks commits that contain `.env`, `*.dpapi`, `log.txt`, `config.json` or anything that looks like an API key. It is not versioned, so copy it when you clone the repository.

## OpenAI migration verification

`tests/test_openai_client.py` uses the installed OpenAI SDK against `httpx.MockTransport`, including SSE streaming, images, history, cancellation, refusals, incomplete and interrupted answers, API errors, token accounting and provider changes. No real credentials or paid requests are used.

Key migration tests substitute encryption to verify provider routing independently of the executing Windows account. A separate native DPAPI roundtrip is skipped if the restricted process cannot access a Windows master key. Do not replace DPAPI with plaintext storage to make this check pass.

## Version 3.2: compact mode, regions, voice and responsiveness

- `core/regions.py` validates persisted physical pixel coordinates and tests containment on a connected monitor. Area reuse never widens a failed region to a whole monitor.
- `speech/voice.py` opens the microphone only for an explicit hold, captures raw 16 kHz mono without a VAD silence delay, and reports its samples on release. Recordings are bounded; cancellation or the duration limit discards them.
- `win/hotkeys.py` uses RegisterHotKey for activation and a 15 ms WM_TIMER only during a voice hold to check release of its known trigger keys. No keyboard hook is installed. Stop safely handles a thread that has not entered its message loop yet.
- `Transcriber` reuses its loaded Whisper and prioritizes explicit voice tasks over queued passive speech. Cancellation tokens and controller generations prevent late voice results from sending a question after Stop or New chat.
- `UiQueue` uses an optional scheduler `wake` to signal Qt immediately, retaining its timer fallback for periodic housekeeping and test schedulers. The chat renders the first chunk immediately and pauses streaming renders for the hidden representation.
- Compact and normal geometry are independent. The compact chat contains only the latest answer; full history remains in the normal chat and AI transport. Only the active representation renders each streamed update.
