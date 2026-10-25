# PRIMUS - Local desktop control

## Syllabus increment 1: MCP and local document RAG

PRIMUS now has a bounded localhost MCP tools bridge and an explicitly approved document knowledge index. See [MCP and RAG setup](MCP_RAG_GUIDE.md) for commands, supported protocol/schema features, security boundaries and limitations. No new model or package is downloaded automatically.

- `python primus.py knowledge index "outputs/notes.md"` previews and asks permission to persist a document. Indexing/reindexing and forgetting always require exact approval.
- `python primus.py knowledge ask "What does my indexed document say about the release?"` retrieves current passages first and asks the local model to answer with checked source-citation labels. `knowledge search`, `list`, `read` and `forget` let you inspect and manage the index.
- Text/Markdown sources work with SQLite FTS5 BM25. Text PDFs additionally require `pypdf` in the chosen Python runtime. There is no OCR or embedding-model download in this increment. This is lexical RAG; it does not retrain the model or provide embedding-based semantic matching.
- CLI/tray/voice/API tasks can use `knowledge: <question>` or `rag: <question>` for the explicit retrieve-then-answer route. The agent also has `knowledge_*` tools. Documents are indexed individually; conversations and folders are never silently ingested.
- `python primus.py mcp servers` shows configured endpoints. `mcp tools <server>` discovers names and schemas; `mcp call <server> <tool> --arguments '<JSON>'` asks exact permission before a call. MCP uses the documented 2025-11-25 Streamable HTTP tools protocol, including JSON/SSE responses. Servers must already run at an explicitly configured `127.0.0.1` endpoint. None is enabled in the initial empty server list.
- MCP servers run under their own OS permissions, outside PRIMUS's script sandbox. Server hints do not grant permission. Calls are EXTERNAL, reviewed metadata/arguments are checked again before invocation, and calls are never automatically retried. Remote URLs, process/stdio launching, MCP sampling and elicitation are not supported in this increment.

The earlier syllabus mapping under `outputs/PRIMUS-course-mapping-2026-10-22.md` is a dated pre-increment review. MCP and document RAG have since been added as described here; named frameworks and the other missing topics remain later steps.

PRIMUS is a local-first assistant rooted at `C:\Users\Subhajeet Bose\Downloads\Primus`. The Python core calls an existing local Ollama model, discovers trusted plugins, checks permissions, audits tool calls, and stops at a bounded step limit. Phase 2 adds approved long-term memory, private task history, persistent reminders and recurring tasks, an authenticated localhost API, a tray and hotkey client, and optional web/Google read-only connectors.

Phase 3 adds an opt-in hands-free voice mode with a local wake phrase, speech recognition, and spoken replies. The user verified a spoken reply on this Windows laptop.

PRIMUS can also launch a small catalog of local apps from voice, CLI, or tray requests. When `tray.show_voice_indicator` is true, a rotating 3D voice orb appears after PRIMUS accepts the wake word, changes from amber to purple while processing and light blue while speaking, then hides again while listening. A voice error also makes it appear in red. The orb reflects process state only; it does not save speech or screenshots. On Windows, the optional `window_vision` tool can describe a specific visible app window through a separate local vision model after its manual setup below.

On Windows, PRIMUS can now search the current user's installed Start apps by name and request an exact app launch after interactive approval. Web and protocol shortcuts are excluded. Use **“Primus, list installed apps”** or **“Primus, find installed Photoshop apps”**, then ask it to open the exact app. The approval shows the app name and Windows app ID. This launches only that registered app; it does not grant PRIMUS access to the app's private files or account. Set `installed_apps.enabled=false` in `config.json` and restart PRIMUS to revoke this integration. macOS and Linux installed-app discovery currently fail closed; the fixed cross-platform app catalog remains available.

Exact commands such as **“open Google Chrome”** now take a direct local path through the same plugin permission and audit checks, avoiding two model calls. Other natural-language requests still use Ollama. `model.keep_alive` keeps the local model resident for 30 minutes after a request. `voice.stt_device=auto` tries the installed NVIDIA GPU with float16 speech recognition and falls back to CPU if its startup probe fails. The microphone adapts its detection threshold to the measured background noise, and the end-of-utterance pause is 0.5 seconds. Reduce noise or speak in one phrase for the quickest response; a long mid-sentence pause can be interpreted as the end of a command.

Voice and tray requests share recent conversation text in RAM for follow-up commands. A separate brief task context now survives restarts in the local SQLite database: the last successfully opened app, accessed file or folder, or created reminder ID. It stores no raw request, reply, microphone audio, or transcript, and expires after 30 days by default. This context helps resolve follow-ups such as “open it again” but is not a saved fact or preference. Long-term facts and preferences still require exact approval for each item.

## Desktop requests

Say **“Primus, open Google Chrome”** or type `python primus.py ask "open Google Chrome"`. Supported app names are Chrome, Edge, Firefox, Notepad/TextEdit, Calculator, file manager, and terminal when installed. `app_list` reports availability. The app launcher accepts only these known names and resolves installed executable paths without invoking a shell. On Windows it checks for a visible application window; when it cannot verify one, it reports that limitation instead of claiming success. Apps may manage their own profiles and settings outside PRIMUS; PRIMUS itself writes its code, logs, and data under the configured root.

For direct keyboard or mouse input, the `desktop_input` plugin supports exact text, a limited hotkey, or a screen-coordinate click. It requires a separate interactive EXTERNAL confirmation showing the action. CLI asks in the terminal; voice/tray/API show a 60-second desktop consent dialog that denies by default. PRIMUS reports only that input was sent; it cannot yet verify the target app completed a GUI task. Never rely on a click or keypress as proof that a send, payment, deletion, or settings change succeeded.

On Windows, say **“Primus, list open windows”** to inspect visible window titles, application names, bounds, and minimized/maximized state. PRIMUS can then focus, minimize, restore, maximize, place a window on the left or right half of its current screen, or request closure. For example, say **“Primus, put the Chrome window on the right half of the screen.”** Every window change shows a separate EXTERNAL approval with the exact title, process ID, window ID, action, and target bounds for layouts. The target uses the monitor's work area, leaving the taskbar clear. Closing warns that unsaved work may be lost. PRIMUS checks the window and monitor geometry again after approval and verifies its resulting state; if the target changes or an app refuses the action, it reports that instead of claiming success. Some apps enforce a minimum window size, so a half-screen placement may be reported as unverified. The audit log hashes window titles. macOS and Linux window actions currently fail closed until native adapters are implemented; app launch and the other cross-platform tools remain available.

For a listed window, PRIMUS can also send exact text or a limited hotkey after a separate approval. It checks that the same window still exists and actually has focus before sending input. It cannot identify the exact field inside an arbitrary app or verify the app's resulting action, so it reports input as sent but unverified. This is suitable for supervised actions; use an app-specific API or connector for reliable edits, sending, and transactions.

For Windows desktop apps that expose accessibility controls, PRIMUS can inspect visible buttons, menus, checkboxes, tabs, list items, and input fields in a specific listed window with `ui_controls`. Ask **“Primus, find the Save button in the open editor”**. The `ui_activate` tool can invoke a button, toggle a checkbox, select an item, or focus a field only after an exact approval naming the app, window, control, and operation. `ui_set_text` can replace a non-password Edit field after showing the exact text and warning that existing field text will be overwritten. Both tools recheck the window and control immediately before acting. `window_input` can send a separately approved hotkey to the focused window. Password fields, credential manager windows, and password manager windows are excluded. PRIMUS does not request field values, but accessibility labels may contain private app text; they are redacted from the audit log and remain local. This path works for apps exposing Windows UI Automation controls; some custom-rendered apps expose too little information. A successful control action reports input delivery, not proof that the app finished a save, send, or other task. macOS and Linux accessibility backends are not implemented yet.

## Browser and approved user folders

PRIMUS can read and create files in your Downloads, Documents, and Desktop folders. Deleting or overwriting still needs exact confirmation. Credential stores, keys, and existing browser profiles remain blocked. Remove a path from `approved_folders` in `config.json` and restart PRIMUS to revoke it.

In tool requests, the short paths `Documents`, `Downloads`, and `Desktop` refer to the matching folders in `approved_folders`. For example, `Documents/my-script.py` targets your approved Documents folder; other relative paths still start at the PRIMUS root. An unapproved folder name does not grant access outside the root.

Say **“Primus, open https://example.org”** to navigate Chrome. `browser.enabled=true` enables only the browser tools; Brave search and Google connectors remain disabled. Each HTTPS navigation, page click, or field entry asks for exact approval, including the URL or text. The browser uses a separate profile at `data/browser-automation` under the PRIMUS root. It does not use your regular Chrome tabs, cookies, or saved passwords. Browser page text is treated as untrusted data. Downloads are disabled in this browser. Only a specific visible CSS selector can be clicked or filled; password and file inputs are blocked. An action is rejected if the matching element changes during approval.

The installed Google Chrome is controlled through Playwright's private pipe, without a browser download or remote debugging port. For another machine, install the pinned browser package with `& ".\scripts\setup.ps1" -InstallBrowser` on Windows or `INSTALL_BROWSER=1 sh "./scripts/setup.sh"` on macOS/Linux. The script asks before using PyPI. Chrome must already be installed and available. Disable browser access by setting `browser.enabled=false` and restarting PRIMUS. The separate profile can be removed after Chrome closes if you no longer need its site data.

## Run other commands

### Write and check code

Ask PRIMUS to create code in an approved folder, for example **“Primus, write a Python script at outputs/my-tool/main.py that prints a greeting, then check its syntax.”** When no path is given, it uses a new folder under `outputs`. New files are created with `file_write` and read back for verification. For an existing file, PRIMUS can use `code_patch` to replace one exact snippet; a desktop or CLI confirmation shows the path and both snippets, and the patch is rejected if the file changed during approval. Existing-file overwrites still require exact confirmation. `code_check` parses Python or JSON locally without executing it. To actually run a named Python or shell script, ask separately; `script_run` requires its own approval and uses the offline sandbox below. Coding requests use the normal multi-step agent, permissions, audit log, and kill switch through CLI, tray, voice, and localhost API.

The current 4B local model can create small programs, but complex projects may need review and revision. A successful syntax check verifies syntax only; it does not prove the code does what you intended. HTML, CSS, and JavaScript currently get file read-back verification, not a syntax or behavior check. In a live landing-page demo, the model created a styled link when the request asked for a button, so review visual and behavior details before using generated pages. PRIMUS reports the full paths and checks it actually completed.

PRIMUS can run a named `.py` or `.sh` script from its root or one of your approved folders. Say **“Primus, run C:\Users\Subhajeet Bose\Documents\example.py”** or use `python primus.py ask "run C:\Users\Subhajeet Bose\Documents\example.py"`. The exact script path, arguments, read-only input files, and output folder appear in a one-action approval prompt. Supply input files by name when you ask; each approved file is mounted as `/work/inputs/0`, `/work/inputs/1`, and so on. Script output goes to a fresh `outputs/script-runs` folder under PRIMUS. The result includes exit code, limited console output, and file hashes. A nonzero exit or unverified output is reported for review.

On this Windows laptop, PRIMUS runs scripts through the already-installed Ubuntu WSL and Bubblewrap. The script has no network, Windows drive mount, WSL interop socket, or inherited environment; it sees its own read-only script snapshot, explicitly named read-only input snapshots, a fresh writable output folder, and a minimal Linux runtime. PRIMUS compares SHA-256 hashes after approval so changed files are rejected. Runs are limited to 25 seconds, 1 GB of address space, 10 MB per file, and 50 MB of output. Windows `.exe`, `.bat`, `.cmd`, and `.ps1` files are not run through this tool because they do not stay inside the Linux sandbox; supported desktop apps use `app_open`. Set `script_execution.enabled=false` and restart PRIMUS to disable it. Linux requires Bubblewrap and the same basic Linux tools; macOS script execution fails closed until an enforced sandbox backend is available. The original `shell_run` tool remains limited to read-only inspection.

The animated orb is a small desktop window that appears on a confirmed wake and hides after the reply. Run `python primus.py orb-open` to show the listening orb manually; close its window to dismiss that manual view. Closing the orb after a wake hides it until the next wake without stopping its process. Set `tray.show_voice_indicator` to `false` in `config.json` and restart the tray to disable it. The tray **Quit** action closes it too. The global kill switch closes the indicator and stops new actions. macOS may ask for accessibility permission for desktop input; Linux Wayland may restrict it. No new package is needed on this Windows installation because pynput and tkinter were already present.

## Local computer vision (Windows)

PRIMUS can visually describe the foreground app window or a specific window returned by `window_list`. It captures one window into memory and sends that image only to a separate Ollama server at `127.0.0.1:11435`. It does not save screenshot files. The `window_vision` plugin is READ, local-only, and blocked for recognized credential windows or visible password/verification fields. The visual model can misread small text or overlook UI details; verify anything consequential before acting. macOS and Linux vision backends are not implemented yet.

The vision server and model require a one-time manual setup. After reviewing the script, run this in your own PowerShell window:

```powershell
& "C:\Users\Subhajeet Bose\Downloads\Primus\scripts\vision-setup.ps1"
```

This starts a second Ollama server using the installed `ollama.exe`, sets `OLLAMA_MODELS` to `Primus\models\ollama`, and downloads `qwen3-vl:4b` (about 3.3 GB) only if absent. Its logs and temp files live under the Primus root. It binds to localhost and disables Ollama cloud access. Leave the server running; after a reboot, run the same script again (it skips the download when the model is already installed). Set `vision.enabled=false` in `config.json` and restart PRIMUS to hide the plugin. To ask PRIMUS, focus an app and say “Primus, what is visible in this window?” or type the same request into the CLI/tray. The active-window path fails if no visible app has focus; a different app can be selected using `window_list` first.

## Fast local answers

PRIMUS can use `qwen3:1.7b` for short, general-knowledge questions without advertising any tools. The 4B model remains responsible for laptop actions, files, personal context, current information, and other complex requests. `fast_answer.auto_simple=true` routes conservative questions such as “What is recursion?” automatically. Say “Primus, quick, explain photosynthesis” or type `python primus.py ask "Quick: explain photosynthesis"` to request the fast route explicitly. Requests about your device, files, reminders, web information, or current events go through the standard agent even with the prefix. If the private fast-model server is unavailable, PRIMUS falls back to the standard model. Fast answers are local and have no tools or live data; they may still be inaccurate.

The fast model is stored under `models/ollama` and served by the same root-local Ollama server as the vision model at `127.0.0.1:11435`. On Windows, after starting that server with `scripts/vision-setup.ps1`, run `scripts/fast-model-setup.ps1` once to download and verify the roughly 1.4 GB model. It skips the download if already installed. After a reboot, restart the private server with `scripts/vision-setup.ps1`; the fast model then becomes available again without downloading. `python primus.py doctor` shows whether it is reachable. `fast_answer.keep_alive` keeps the model in GPU memory for 30 minutes after use; shorten it to free memory sooner. Set `fast_answer.enabled=false` in `config.json` and restart PRIMUS to disable fast routing. The fast model has no separate cloud dependency or telemetry. On macOS/Linux, the core route works with a root-local Ollama server at the same loopback endpoint, but a setup helper for that private server is not included yet.

On this Windows laptop, three warm full-CLI answers measured 0.49, 0.48, and 0.41 seconds through the 1.7B route, compared with 1.60, 1.63, and 3.74 seconds through the normal 4B route. The first cold load took 18.53 seconds. These are local samples, not a general speed guarantee; voice recognition and speech output add time.

## Setup

Python 3.11+ and a local Ollama server/model are required. Base setup does not install Ollama or pull the main model. The default tool model is the already-installed `qwen3:4b-instruct-2507-q4_K_M`; the fast and vision models have separate opt-in setup scripts. All PRIMUS source, SQLite data, logs, outputs, dependency files, and temp files live beneath `root` in `config.json`; move the full folder and edit that one setting together.

Windows PowerShell, from the root:

```powershell
& ".\scripts\setup.ps1"
& ".\scripts\setup.ps1" -InstallPhase2
```

The second command offers an explicit YES prompt before downloading the four pinned optional packages into `vendor`. To choose an existing Python: `& ".\scripts\setup.ps1" -Python "C:\path\to\python.exe" -InstallPhase2`.

macOS/Linux, after moving the whole project and editing `config.json` root:

```sh "./scripts/setup.sh"
INSTALL_PHASE2=1 sh "./scripts/setup.sh"
```

The optional install also asks for YES. Set `PYTHON_BIN` to an existing interpreter if needed. Both scripts keep pip cache and temporary files in the configured project folder during install. The tray may additionally need operating-system GUI support; Linux Wayland global hotkeys can be restricted.

## Hands-free local voice (Phase 3)

Voice mode listens for **“Primus”**, transcribes the following request locally, sends the text through the authenticated localhost API, and speaks the answer with a local Piper voice. Say “Primus, list my outputs” in one utterance, or say “Primus”, wait for “Yes?”, and then speak the request. The wake word must be at the start, with optional “Hey”, “Okay”, or “OK”; similar-sounding words are rejected. PRIMUS now checks a possible wake phrase with two unprompted speech decodes before accepting it. If either decode misses the wake word, it ignores the utterance. A one-utterance command runs only when both decodes hear a command. Say “Primus, stop listening” or press Ctrl+C to exit. The microphone is closed during speech output. Audio stays in RAM; PRIMUS does not save recordings. Actions requiring destructive or external confirmation show an exact, one-action approval dialog on the signed-in desktop; no response within 60 seconds denies the action.

This phase needs an explicit one-time package and model download. The pinned top-level packages are `faster-whisper==1.2.1`, `sounddevice==0.5.6`, and `piper-tts==1.8.0`, plus their dependencies. The English STT model is about 76 MB; the English Piper voice is about 63 MB. They and the download caches stay under the PRIMUS root. The setup scripts require typing `YES` before installation. Voice never downloads a model at runtime.

Windows PowerShell, from the PRIMUS root:

```powershell
& ".\scripts\setup.ps1" -InstallVoice
& ".\primus.py" voice-doctor
& ".\primus.py" voice
```

Use `& "C:\path\to\python.exe" ".\primus.py" voice` if `.py` file associations are unavailable. macOS/Linux, after moving the project and updating `config.json` root:

```sh
INSTALL_VOICE=1 sh "./scripts/setup.sh"
python3 "./primus.py" voice-doctor
python3 "./primus.py" voice
```

The speech model paths, input device, energy threshold, silence duration, and command timeout are in the `voice` section of `config.json`. `voice-doctor` checks for installed packages and both model files. The tray menu has **Start Voice** and **Stop Voice**. With `tray.auto_start_voice` enabled, starting the tray starts listening immediately. The first build uses faster-whisper to detect the wake phrase, so detection runs after a short utterance ends and may consume noticeable CPU. This is turn-based speech, not full-duplex audio; local inference can add seconds of latency. On Linux, PortAudio and an audio device must be available; macOS may ask for microphone permission.

### Listen automatically after Windows sign-in

The Windows helper registers one current-user Scheduled Task named **PRIMUS Always On**. It runs the existing `pythonw.exe` and `primus.py tray` in the interactive desktop session with limited privileges. The tray starts offline wake listening automatically. The task definition is an OS setting outside the PRIMUS root, so registering it requires the user's explicit approval. It starts once when the user signs in and immediately when enabled; listening pauses while the laptop sleeps and resumes after wake. No network plugin is enabled by this setting.

Run these commands in PowerShell, using the project path and Python runtime on this laptop:

```powershell
& "C:\Users\Subhajeet Bose\Downloads\Primus\scripts\always-on.ps1" -Mode Enable
& "C:\Users\Subhajeet Bose\Downloads\Primus\scripts\always-on.ps1" -Mode Enable -Apply
& "C:\Users\Subhajeet Bose\Downloads\Primus\scripts\always-on.ps1" -Mode Status
```

The first command previews the exact task and makes no OS change. Use `-Apply` only after approval. To remove sign-in startup, run `& "C:\Users\Subhajeet Bose\Downloads\Primus\scripts\always-on.ps1" -Mode Disable -Apply`; a currently running tray continues until you choose **Quit** from its menu. Choose **Stop Voice** to turn off the microphone without quitting the tray. PRIMUS writes process state, PID, timestamp, and active speech device to `data/voice-status.json`, with no audio or transcripts. `python primus.py voice-status` reads that state. The task currently points to the existing Codex-bundled Python runtime; if that runtime is removed, update the task's `-Pythonw` path to a compatible Python installation. macOS and Linux can use the same tray auto-start behavior, but sign-in registration scripts for those operating systems are not included yet.

## Commands

### Mission Control: planner → coder → verifier

Right-click the PRIMUS tray icon and choose **Mission Control**, or choose **Mission Control** in the Ctrl+Alt+P prompt. It opens the dashboard in installed Chrome with a dedicated profile under `data/browser-automation/mission-control`. All page requests are limited to the existing localhost API. No new browser, package, cloud service, or network listener is installed.

```powershell
& "C:\Users\Subhajeet Bose\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "C:\Users\Subhajeet Bose\Downloads\Primus\primus.py" dashboard
```

Enter a coding brief and click **Launch mission**. The page shows real role states, model waits, tool calls, exact approval requests, output files, checks, and an independent model review. It has no fabricated activity or percent-complete estimate. Click an output file to inspect its text and hash; file contents are displayed as text and never executed by the dashboard. Cancel stops that mission's next actions. The original voice orb and task prompt remain available.

- **Planner:** returns a validated title, 1–6 steps, and acceptance criteria. It has no action tools.
- **Coder:** uses reviewed file/coding plugins to create artifacts in a fresh `outputs/missions/<mission-id>` workspace. Reads still obey approved-folder and credential guards. Writes, patches, and overwrites cannot escape the mission workspace. Every overwrite/patch asks for exact approval and checks the reviewed file hash.
- **Verifier:** re-reads actual files, parses Python/JSON, parses HTML, records hashes, and independently reviews the contents against the goal. A model's passing verdict cannot override a failed deterministic check. It can request one coder repair cycle. Other text formats receive model review only.

The three roles run in order using separate conversations with the configured local model. One coding mission runs at a time to limit laptop memory pressure. `missions.enabled` and `missions.max_steps` (3–12, default 8 per coder pass) are in config. Each mission is limited to 12 files of 100 KB each and 24 KB of text for model review; split larger work into smaller missions. The planner and reviewer retry invalid structured output once. The existing independent read-only `team` command is still available for parallel inspections.

Repairs read the existing file and propose a full-file `file_overwrite` with exact user approval. Snippet patches are unavailable during the repair pass, avoiding repeated failures when a small model miscopies an old snippet. Reusing `file_write` on an existing file returns an explicit instruction to request an approved overwrite. A failed mission displays the stopping reason and last tool error directly below its title; generated files and review evidence remain available in the current session.

**Verification scope:** completion means the workflow's available checks and model review passed. Python/JSON parsing proves syntax, and HTML parsing is not a browser layout or functional test. Generated code is executed only through a requested, individually approved sandbox run. **Request sandbox tests** asks the coder for `verify.py`, which imports the actual project modules and reads project data by relative path, with no command-line inputs. The verifier proposes an exact `script_run` action through the existing offline sandbox. You must approve that individual execution in the dashboard. Existing Windows WSL/Bubblewrap or Linux Bubblewrap setup is required; the workflow fails closed if the sandbox is unavailable. Sandbox outputs go to the explicitly reviewed `outputs/script-runs/<run-id>` folder. A zero exit code is evidence of that check, not proof of every possible behavior.

**Run generated code and see its actual output:** after the mission finishes, click **Run project** beside the desired `.py` or `.sh` entry point. Review the entry point, every project file's SHA-256, arguments and output folder, then click **Approve exact action**. The verifier executes an immutable, read-only snapshot of the whole mission workspace through the existing offline sandbox. **Actual runtime output** shows stdout and stderr separately, exit code, execution status, output folder, and an expandable list of the exact project file versions used by each run. The sandbox-test option's `verify.py` output appears in the same panel. Streams are capped at 4 KB each with explicit truncation labels; raw output stays in service-session memory and is redacted from audit logs. HTML-like output is displayed as inert text. Rejection, expiry, cancellation and setup failure show an unexecuted/error state, never a fabricated success or host-Python fallback. Each rerun requires new approval. At most 10 execution attempts are retained per mission. Runs do not bypass failed code review or other verification checks.

**Multi-file projects:** relative paths, Python sibling imports, nested packages (including relative package imports) and JSON/text data files are preserved under `/work/project`. That is also the working directory. Python starts in isolated mode; only the approved project and entry directory are added to its module search path. Shell entry points can invoke approved helpers through `sh helper.sh`. Source/data are read-only; programs must write generated files under `os.environ['PRIMUS_OUTPUT_DIR']` (`/work/output`) or the equivalent shell variable. A run pins every file, including helpers that are not directly imported: changed, added or removed files invalidate approval before launch. Links, junctions, hard links, blocked credential paths and case-colliding paths are refused. Limits remain 12 files of 100 KB each, 64 directories, 24 KB for model review, and the existing runtime resource limits. The run button passes no arguments or interactive stdin. It does not run HTML/JavaScript, install dependencies or support long-running services. Standard-library Python and POSIX shell batch projects are the supported baseline. No execution controls are available for missions from a previous service session; start a new mission to review the code again. The general `script_run` plugin still supports standalone scripts and numbered explicit inputs when `project_root` is omitted; an optional approved `project_root` enables a complete reviewed project snapshot.

**Automatic runtime repair:** if an approved project run exits nonzero with a program error, the actual stdout, stderr, exit code and project manifest are sent to the coder as untrusted diagnostic data. One focused repair pass may correct multiple related source/data files or create a missing helper inside the mission workspace. Each existing-file overwrite still asks for its own exact approval; new files follow the configured WRITE policy. Independent `verify.py`, `tests/`, `test_*.py` and `*_test.py` files are protected from runtime-repair changes. The coder cannot run code, install dependencies or edit outside the project. The verifier then re-reads the entire project, checks syntax and reviews the source; only changed source plus a passing review leads to a fresh, separately approved full-project rerun. Helper-only changes count even if the entry point stays unchanged. No source change or a failed review means no rerun. Mission Control shows repair progress, a `0/1` or `1/1` repair counter and all execution records. One automatic runtime repair pass is allowed per mission; a second execution error stops with needs review. Rejection/expiry at any approval, cancellation, sandbox setup errors, unavailable dependencies/commands, timeouts and signal termination stop the loop. The independent `verify.py` test script is excluded from this direct-run repair loop. Manual edits require a new reviewed mission; automatic corrections are reviewed within the current mission. A zero exit with incorrect output still requires explicit tests or review and is not detected by the exit-code trigger alone.

Approval cards show the exact action and arguments, expire after 120 seconds, and accept one decision only. A fixed notice with a countdown and **Review action** button stays visible while approval is pending; it takes you to the complete action before you decide. If the deadline passes without a decision, the result says **Approval expired**, never **rejected by user**, and shows that nothing was executed. **Request fresh run approval** in the expired output card requests a new approval for the current reviewed project; it does not approve or execute anything. The expired approval cannot be reused. Rejection and expiry do not trigger an automatic repair cycle that re-asks the same action. Approval/rejection metadata is appended to `decision.md`; expiry is audited separately as a non-decision. Code and prompts are redacted from audit/decision logs. Neither a gesture nor a model verdict grants consent. Local cancellation and the global kill switch are checked before tools and while sandbox scripts run. A model HTTP request already in flight may finish after cancellation, but its result cannot start more tools.

The optional live repair fixture, `python scripts/test-runtime-repair-live.py --multi-file --wait-for-run`, opens a seeded four-file project with a known empty-list division error. It requests no approval until you click **Run project** beside `main.py`. Review and approve the initial run, each proposed existing-file correction and the separate rerun in Mission Control. The normal tray API must already be running; the fixture uses a separate authenticated localhost port, real configured models and the real sandbox, and stops when its dashboard closes. Its report under `outputs/runtime-repair-e2e` updates after each completed attempt, including a fresh attempt after expiry. A seeded fixture is test data, not evidence that a model generated it or that execution succeeded.

The dashboard opens with a single-use launch code that expires in 60 seconds. The permanent API token stays in the OS keychain; JavaScript receives no API token. The browser exchanges the code for a scoped HttpOnly, SameSite=Strict session cookie, valid for one hour. Host/origin checks, a custom request header, no CORS access, and a strict content security policy protect the dashboard API. The dedicated profile is inside the already blocked `browser-automation` folder. Reopen Mission Control from the tray after expiry or a service restart; no API key needs to be pasted into the page. Only the empty page shell and static assets are available without a session.

Mission goals, plans, event details, reviews, and replies stay in bounded service-session memory (20 recent missions, 200 recent events per mission). SQLite stores mission status/workspace metadata and the existing hashed task history, including planner/coder/verifier lineage. After a service restart, unfinished missions become **interrupted** and old details are unavailable; output files remain. Automatic resumption is not part of this increment. When the dashboard launcher starts its own API because the tray is not running, closing that launcher also stops its API and active missions.

CLI and voice can submit to the same core:

```text
python primus.py mission "Create settings.json with project PRIMUS and offline true"
python primus.py mission "Create a Python utility and a verifier" --run-tests
python primus.py ask "Mission: create a small HTML landing page for PRIMUS"
```

Voice can say “Primus, mission, create a small Python utility.” Follow progress and approvals in Mission Control. `POST /missions` accepts `{"goal":"...","run_tests":false}`; authenticated clients can list missions, inspect `/missions/<id>`, preview `/missions/<id>/files/<index>`, request execution with `POST /missions/<id>/run` and `{"artifact_index":0}`, and post to `/missions/<id>/cancel` or `/missions/<id>/approval`. Requesting a run never grants its approval. Browser sessions are scoped to mission routes and cannot authorize the general task, memory, or settings APIs.

### Describe an image locally

Press **Ctrl+Alt+P**, choose **Attach image**, select a JPEG, PNG, or WebP in the PRIMUS root or an approved folder, optionally type a question, and press **Run**. Choose **scene** for an overview, **detail** for objects and spatial relationships, **text** for legible non-credential text, or **gestures** for static hand poses. **Focus area…** lets you drag a box around a small object or text; **Use full image** resets the selection. The prompt shows a progress bar while PRIMUS works. You can also use:

```powershell
& "C:\Users\Subhajeet Bose\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" "C:\Users\Subhajeet Bose\Downloads\Primus\primus.py" image "C:\Users\Subhajeet Bose\Downloads\example.png" --question "What is happening in this image?"
```

The authenticated local API accepts `POST /images/analyze` with `{"path":"...","question":"...","mode":"detail","region":[0.1,0.1,0.8,0.9]}`. Only `path` is required; region coordinates are left/top/right/bottom fractions from 0 to 1 in the correctly oriented image. The CLI accepts `--mode detail` and `--region 0.1 0.1 0.8 0.9`. The image must be at most 10 MB and 25 million pixels. PRIMUS removes metadata, applies orientation, composites transparency on white, and crops/resizes in memory. Detail/text modes preserve up to 2048×1536 pixels; scene mode uses up to 1600×1200. The result includes resolution, brightness, and contrast notices when appropriate. These are quality hints, not an accuracy score.

Scene/detail/text modes use only the configured vision server on `127.0.0.1:11435`. The model is released after 30 idle seconds to reduce competition with local speech for GPU memory. Original pixels, recognized text, hand labels, and descriptions are not saved in task history or audit logs; audit paths and hashes remain. The private vision server must be running; after a reboot, run the existing `scripts/vision-setup.ps1` in your PowerShell session. Small or blurred text can still be read incorrectly; use a sharp original image and focus on the relevant area. A model description cannot verify events outside the image.

### Offline hand gestures

Choose **Gesture camera** in the PRIMUS tray menu, then **Start Camera** in the preview window. The camera is off until you press Start. Show an open palm, closed fist, pointing finger, victory/two-finger pose, thumb up/down, or pinch. The largest detected hand is the primary hand. Its label changes from **hold steady to confirm** to **held steady** after at least four frames and the configured hold duration. Uncertain or lost hands clear confirmation immediately. Colored landmarks and a hand tracking score help you see what the engine detects; that score is not the probability that the gesture label is correct. Press **Stop Camera**, close the window, press Escape, or activate the global kill switch to end the session. Frames and landmarks stay in memory; only session start/stop/error metadata enters the audit log.

```text
python primus.py gestures
python primus.py gesture-doctor
python primus.py image "path/to/hand.jpg" --mode gestures
```

Gesture mode uses two local ONNX hand models through OpenCV DNN on the CPU and does not need Ollama. It recognizes a limited set of static poses using hand landmarks and geometric rules. It does not translate sign language or trigger laptop commands. Good lighting, a visible palm, and an unobstructed hand matter; small, occluded, or side-on hands may be uncertain or missed. `gestures` in config controls enablement, the local camera index (0–9), minimum hand score, hold duration, and maximum frame rate. It never starts automatically with the tray. macOS/Linux require their usual camera and desktop permissions; the camera path has not been physically tested there.

For another machine, preview the optional installer with `python scripts/gesture-setup.py`, review its actions, then use `python scripts/gesture-setup.py --apply` after approval. It installs pinned OpenCV without replacing dependencies, so the existing PRIMUS NumPy 2 or newer is required. Downloads, cache, models, source wrappers, and licenses stay under the root. The source revision and model checksums are pinned; see `src/primus/_hand_models/README.md`. No MediaPipe Python runtime or runtime network request is used. Public test samples and their source links are listed in `outputs/image-demos/SOURCES.md`.

### Parallel task agents

PRIMUS can coordinate 2–3 independent local, read-only agents for one request. The coordinator plans subtasks with the configured local model, validates the plan, runs up to two agents concurrently, and reports each result in the original order. Each agent has its own model conversation; the existing permission and audit checks still apply. Parallel agents cannot use network, write, app-control, or dynamically tiered tools. A request that cannot be split safely returns a review message; send it as a normal task if it needs actions. The limit is set by `agents.max_parallel` and `agents.max_subtasks` in `config.json` (2–4). On this laptop the default is two concurrent agents to limit local model memory pressure. Ollama may serialize inference, so parallel agents need not finish faster than one agent.

```text
python primus.py team "Check my outputs folder and scheduled reminders"
python primus.py team --task "list my outputs folder" --task "list reminders"
python primus.py ask "Team: check my outputs folder and scheduled reminders"
```

The authenticated localhost API accepts `POST /teams` with either `{"task":"..."}` for model planning or `{"tasks":["...","..."]}` for explicit independent subtasks. `POST /tasks` also accepts a `Team:` prefix, so voice can say “Primus, team, check my outputs folder and reminders.” Task history stores a coordinator record and child records linked by `parent_task_id` and `agent_label`; requests and answers remain hashes. Audit entries label each worker without storing the team request text. The global kill switch stops the coordinator and workers between model and tool calls.

```text
python primus.py doctor
python primus.py plugins
python primus.py ask "list my outputs folder"
python primus.py team --task "list my outputs folder" --task "list reminders"
python primus.py chat
python primus.py memory-add preference "I prefer concise reports"
python primus.py memory-list
python primus.py memory-edit ID "I prefer one-sentence reports"
python primus.py memory-delete ID
python primus.py context-list
python primus.py context-clear
python primus.py history
python primus.py schedule-add reminder "2026-10-20T18:00:00+05:30" "Take a break"
python primus.py schedule-add reminder "2026-10-20T18:00:00+05:30" "Take a break" --repeat-seconds 86400
python primus.py schedule-list
python primus.py schedule-disable ID
python primus.py scheduler
python primus.py api-token-init
python primus.py serve
python primus.py tray
python primus.py stop
python primus.py resume
```

Memory changes, context clearing, scheduling, API-token creation, and destructive tool calls show the exact action and ask for confirmation. Approved memory holds only explicitly approved facts/preferences and rejects likely credential text. The new task context is limited to short, structured references from successful tools; it never stores the task text or answer. File paths and app names can still be personal, so inspect them with `context-list` or the authenticated `GET /context` API and use `context-clear` to remove them. Set `memory.task_context_enabled=false` in `config.json` and restart PRIMUS to stop future context saves; `memory.context_retention_days` controls the recall window (1–365 days). Task history stores hashes, byte counts, status, and timestamps, not raw requests/results. The SQLite database lives at `data/primus.sqlite3`. Scheduler notifications use Windows MessageBox, macOS notification, or Linux `notify-send`. The scheduler must be running through its process for reminders to fire. Run `tray` to start the local API and scheduler with the tray; Ctrl+Alt+P opens the task prompt. After you press Run, the prompt stays visible with an animated progress bar and elapsed time until a result or error appears. It also shows when PRIMUS may be waiting for a permission dialog. Spoken requests use the voice orb: purple means processing and light blue means speaking. These indicators show that the request is active; they do not estimate remaining time. `serve` is available when no tray is wanted. API requests require a bearer token stored in the OS keychain and accept requests only on `127.0.0.1`. The token is never printed. API tasks needing destructive/external consent show the signed-in desktop dialog and deny if no interactive desktop is available. Scheduler jobs cannot approve their own external actions.

General network plugins are off by default. The browser is individually enabled; to enable the other network plugins, manually set `network_plugins_enabled` to true in `config.json` after reviewing the change. Every EXTERNAL call still requires exact confirmation. Web search uses a Brave API key stored via `python primus.py secret-set brave-api-key`. For Google, create a Google Desktop OAuth client with Gmail read-only and Calendar events read-only access, set `google.enabled` and `google.client_id` in config, then run `python primus.py google-connect`; it asks before opening the browser and stores the refresh token in the OS keychain. No network connector is exercised unless enabled. Web search, Gmail, and Calendar need an internet connection.

Files and source under PRIMUS are guarded; `outputs` is the intended file output directory. Additional approved folders must be entered in `approved_folders` in config. Third-party Python plugins execute on discovery and are trusted code; load only plugins you reviewed. See `PLUGIN_GUIDE.md`.

## Tests

```text
python -m unittest discover -s tests -v
```

Tests use fixtures under `temp`. Set `TMP`/`TEMP` (Windows) or `TMPDIR` (Unix) to the PRIMUS temp folder if your Python runtime otherwise uses a system temp directory.

## Known limitations

- Clear single-purpose requests advertise only their relevant built-in plugin family to the local model. Multi-step, contextual, unfamiliar, or custom-plugin requests retain the full catalog. Exact requests to list PRIMUS outputs or active reminders, plus the existing app shortcuts, call their read-only plugins directly with the same authorization and audit checks. On this Windows laptop, the outputs listing measured 14.6 seconds with the full model path, 7.5 seconds with file-tool routing, and 0.3 seconds with the direct path; a model-assisted file read still measured about 8 seconds. Times vary with model load, context, and task complexity.

- The optional tray/keychain packages are installed on this Windows laptop. Other machines need the opt-in dependency install and an available OS keychain/display service. The user confirmed that Ctrl+Alt+P opened the prompt in their desktop session.
- The direct path is deliberately narrow. Complex requests still need local model planning and will be slower. GPU speech recognition needs a compatible NVIDIA CUDA runtime; auto mode falls back to CPU if the startup probe fails. The wake detector still uses Whisper transcription rather than a dedicated wake-word model.
- The API token protects HTTP access from websites and casual local callers, but a malicious process running as the same OS user may be able to access that user's keychain. Run PRIMUS only in a trusted user session.
- Browser navigation, clicks, field entry, and desktop input need the signed-in user's approval for each action. A desktop dialog may be unavailable in a headless session; those calls fail closed. Site scripts can react to a click or field entry, so inspect the confirmation and resulting page before treating a transaction as complete.
- The scheduler runs while its process is active. Restart recovery may repeat a job that was interrupted after its action but before its completion was recorded.
- Google and Brave connectors require user-provided credentials and opt-in network config. They have no live account verification in this phase.
- Web fetch is text only, limited to public HTTPS port 443 and 100 KB, and does not follow redirects. Google OAuth uses the official Desktop loopback flow.
- The path guard covers bundled file tools, not arbitrary third-party Python plugin code. The kill switch prevents new actions but cannot terminate an OS call already in progress.
- Parallel team agents are read-only and local. Their task plans are checked for shape and count, but a small local model can still choose an unhelpful split. Worker results are shown separately rather than merged into an unsupported claim. Already running OS calls may finish after the kill switch is set.
- Fast answers deliberately skip tools and live data. Their speed depends on whether the 1.7B model is already loaded; the private server must be restarted manually after a reboot. The smaller model can give less accurate explanations than the 4B tool model. Voice wake detection and TTS time remain even when the answer model is faster.
- Voice packages and models are installed on this Windows laptop, and the user confirmed a spoken reply from the physical microphone and speaker. Other machines need the explicit voice install. Wake detection uses transcription rather than a dedicated trained “Primus” model, so recognition can vary by pronunciation and background noise. The stricter two-decode check can miss quiet or unclear wake phrases and adds inference time only to possible wake phrases. False activations cannot be ruled out without a dedicated wake detector and physical testing in the user's environment. TTS waits for the full task result before speaking; interruption while PRIMUS speaks is not supported. The orb shows the discrete voice states after wake confirmation, but it is not an audio waveform. The window-vision plugin needs its manually installed local model and works on Windows only; it does not control GUI apps from visual coordinates. Web UI and mobile access remain later work. The approved built-in local MCP status server and document RAG are available; see `MCP_RAG_GUIDE.md` for exact approval, transport and retrieval limits.


## Formal planning

The grounded STRIPS planner computes model-based plans offline using explicit facts, preconditions and add/delete effects, then independently replays the result. It never executes or authorizes planned actions. Use `python primus.py planning solve "examples/planning/downloads.json"` or the explicit task `planning: examples/planning/downloads.json`. `formal_plan` and `formal_validate` are local READ plugins. See [FORMAL_PLANNING_GUIDE.md](FORMAL_PLANNING_GUIDE.md) for the input format, source hashes, search limits, three demonstrations and full PDDL exclusions. Existing Mission Control role plans remain separate; these symbolic models do not prove real-world task success.


### Approved local speech recognition upgrade

On 22 October 2026 the user approved the `Systran/faster-whisper-base.en` download under `models/stt/faster-whisper-base.en`, changing only `voice.stt_model_dir` and restarting the existing task once. The publisher LFS hash and a local CUDA inference probe passed; `outputs/base-speech-model-setup.json` records the pinned revision and hashes. The headset selection, exact wake phrase and two unprompted recognition checks remain unchanged. The tiny English model remains available for a reviewed rollback. This model change is an attempt to improve missed wake phrases, not proof of a successful physical wake test.

`scripts/setup_base_speech_model.py` performs this explicit network download and local load probe only; it never edits config, installs packages or changes startup. Run it only when downloading this model is approved. `scripts/diagnose-wake.py` runs a short local microphone/recognition diagnostic using the configured model, writing only signal levels and acceptance categories to `outputs/wake-diagnostic.json`. It saves no audio or transcripts and dispatches no tasks. The existing always-on listener remains separate. A visible orb can be requested with `python primus.py orb-open`; close it before testing automatic wake-triggered visibility.

After this upgrade and restart, the user explicitly confirmed that saying the wake phrase made the orb appear and produced a spoken reply. This is one physical confirmation, not a guarantee against future wake misses. The microphone setting and both wake checks were preserved.


### Continuous wake capture

Wake listening now keeps the microphone open in a bounded capture worker while both exact, unprompted speech decodes run. Up to two recent utterances remain in RAM; older queued audio is dropped instead of accumulating a backlog. Single 100 ms sound spikes are rejected before recognition. Capture closes and queued audio is discarded before PRIMUS speaks, so this is not full-duplex speech or barge-in. `voice-status` includes `capture_active`, `wake_checks` and `wake_accepts`, with no audio or transcript. The checks/accepts are counts since this voice process started, not a measured recognition accuracy rate. The orb still appears only after an accepted wake phrase or an error, or when manually opened; it stays hidden for ordinary wake checks.

After the capture-gap correction, all 180 automated tests passed on the confirmation run. A real wake observation recorded capture active during checking, an accepted wake, the orb becoming visible without a manual marker, and capture closed while speaking. The user separately confirmed seeing the orb and hearing a reply. Speech recognition can still miss a phrase; this verifies that interaction and the repaired capture behavior rather than guaranteeing every future wake.


### Open websites in Google Chrome

Say or type `open YouTube`, `open Google in Chrome`, `open example.com`, or `open example dot com in Google Chrome`. Exact single-site requests now use the existing `browser_navigate` tool directly, skipping model planning. Typed HTTPS paths and queries retain their case; spoken `dot` normalization applies to bare domains. Named shortcuts cover Google, YouTube, Wikipedia, GitHub, Gmail, Reddit and Stack Overflow. Other sites need an explicit domain or HTTPS URL. Compound and ambiguous requests stay on the normal agent path.

Each navigation remains EXTERNAL and shows the exact URL for approval before a request is sent. Browser access must be enabled; generic web search/email/calendar settings are unchanged. Chrome uses PRIMUS's existing separate profile, not your personal Chrome login/profile. The reply reports the final page URL returned by Chrome, including redirects. It does not claim the site completed any transaction. A denied or failed navigation does not produce a successful result. Internet is required for public sites. `open Google Chrome` still opens the app through the existing app shortcut.

Website-opening verification: all 187 tests passed after the rejected-POST response correction. Following the approved service reload, a real API request opened https://example.com/ after the user chose Approve once. The returned final URL, browser audit and visible Example Domain Chrome window matched. Evidence: `outputs/website-opening-live-verification.json`.


### Gesture action commands

Open **Gesture camera** from the tray or run `python primus.py gestures`. Choose **Start Camera**, then check **Enable gesture commands for this camera session**. Remove your hand from view for one second before posing. Hold a thumb-up for YouTube, a victory V for Google Chrome, or pointing for the Google website. Review **Approve once** in the separate PRIMUS dialog. Requests use the actual authenticated task API, not simulated output. They keep existing EXTERNAL consent and browser profile rules.

A pose needs one second and at least four tracked frames. One held pose triggers at most once; remove your hand for one second to rearm. There is a five-second trigger cooldown and no new request while one is pending. Commands default off every new window. Stop Camera disables new gesture requests; it cannot revoke an already approved action. Failed requests are not retried automatically. Frame timestamps with gaps do not count as a continuous hold or release. Camera frames and landmarks remain in RAM. Recognition can misclassify, so a gesture never constitutes approval.

Wake spelling correction: a user mic-test screenshot showed both unprompted passes transcribing the direct address as `Hey, Premus.`. The matcher now accepts `premus` only after Hey/OK/Okay, alongside the existing Primus phrase. Bare Premus, promise, premise and primers remain rejected. Both recognition passes must accept a phrase, and no wake phrase grants sensitive-action approval. Previously running voice/test processes need restarting to load the changed matcher.
## Approval mode (current configuration)

`permissions.auto_approve=true` in `config.json` enables automatic authorization for every tool permission tier across CLI, voice, gestures, and Mission Control, including file replacements, deletions, browser actions, MCP calls, and sandbox execution. The current user explicitly requested this mode. Statements below describing individual approval dialogs apply when automatic mode is off. This is authorization to act without individual review; destructive effects may be irreversible.

To restore manual approval, set `permissions.auto_approve=false` and restart PRIMUS. The default when the setting is absent is manual mode. Existing per-tier settings then apply again. Automatic decisions are recorded as policy approvals in the audit and decision logs; they are never labeled individual human approvals. The authenticated `/health` response reports the loaded `approval_mode`.

Approved folder boundaries, credential blocking, disabled network/cloud tools, sandbox limits, verification, task cancellation, and the global kill switch still apply. This setting grants no administrator privileges or additional folders. Gestures still require enabling gesture commands for the camera session. A workflow must still be requested; automatic approval does not start arbitrary tasks or code runs by itself.

