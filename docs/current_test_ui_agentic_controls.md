# Current Test UI Agentic Controls

## Launch Commands

Use the Windows host entrypoint with the runtime mode flag:

```powershell
.\.venv\Scripts\python main.py --brain-mode harness
.\.venv\Scripts\python main.py --brain-mode auto
.\.venv\Scripts\python main.py --brain-mode openclaw
```

You can also set the default with an environment variable:

```powershell
$env:ECHOES_BRAIN_MODE = "harness"
.\.venv\Scripts\python main.py
```

## Runtime Modes

| Mode | Purpose | OpenClaw startup | Live Conversation |
| --- | --- | --- | --- |
| `harness` | Offline-safe dashboard and UI validation | Disabled | Disabled |
| `auto` | Preserve the normal LangchainDev runtime path | Enabled when configured | Enabled |
| `openclaw` | Force OpenClaw runtime | Enabled | Enabled |

The browser badge and diagnostics now expose the runtime mode contract:

- `brain_mode`
- `live_runtime_available`
- `harness_runtime_available`
- `openclaw_enabled`

## Stage Layout

The stage is now organized as a fixed six-layer 2K model.

### Design tokens

`style.css` defines these root variables:

- `--stage-design-width: 2560`
- `--stage-design-height: 1440`
- `--stage-scale`
- `--agentic-panel-offset`
- `--pet-anchor-x`
- `--pet-floor-y`
- `--pet-width`
- `--pet-height`
- `--pet-scale`
- `--pet-z-index`

### DOM layers

`index.html` contains these stage containers:

- `stage-root`
- `stage-background`
- `stage-pet-layer`
- `stage-live-ui`
- `stage-bottom-ui`
- `stage-agentic-panel`

`app.js` uses `ResizeObserver` to keep `--stage-scale` aligned with:

```text
min(viewport_width / 2560, viewport_height / 1440)
```

## Character Anchoring

The pet anchor now uses bottom-center positioning:

```css
left: var(--pet-anchor-x);
bottom: var(--pet-floor-y);
transform: translateX(-50%);
```

This keeps the stage stable while still allowing per-character offsets and scale from runtime layout data.

## Live Conversation vs Harness Test Input

The UI now separates the two text paths explicitly:

- `Live Conversation` uses `data-conversation-path="live"` and routes to the normal runtime bridge method `sendLiveText`.
- `Harness Test Input` uses `data-conversation-path="harness"` and routes to the harness adapter method `sendText`.

This keeps BrainEngine input separate from PetHarnessEngine input and prevents the previous shared-input ambiguity.

## Background Resolution

Background ownership is now handled by `ui/background_resolver.py`.

Resolution order:

1. Character-configured background
2. `assets/backgrounds/default_room.*`
3. CSS placeholder fallback

Reported statuses:

- `loaded`
- `fallback_default`
- `fallback_placeholder`

Diagnostics expose only masked or relative sources. Absolute local paths are not shown in normal UI output.

## Voice Status

Harness voice diagnostics are now backed by `VoiceRuntimeStatusAdapter`.

DTO fields:

- `stt_status`
- `tts_primary_status`
- `tts_fallback_status`
- `audio_worker_status`
- `last_voice_error`
- `overall_status`

Supported status values:

- `configured_and_ready`
- `runtime_available`
- `runtime_present_trigger_not_wired`
- `configured_missing_runtime`

The adapter is read-only and offline-safe. It does not start capture, playback, or network calls just to build status.

## Diagnostics Groups

The Diagnostics panel is now grouped into:

- `Runtime`
- `UI`
- `Voice`
- `Harness`
- `Security`

Important UI diagnostics:

- `stage_size`
- `stage_scale`
- `pet_anchor_x`
- `pet_anchor_y`
- `pet_scale`
- `background_status`
- `idle_motion_candidates_count`

Important security diagnostics:

- OpenAI / ChatGPT key presence
- Azure STT key and region presence
- ElevenLabs key and model presence

All security entries are summarized as `[configured]` or `[missing]`.

## JavaScript Bridge Contract

The PyQt <-> Web bridge now explicitly includes the missing idle-motion contract.

Python to JS:

- `setIdleMotionCandidates`
- `setRuntimeMode`
- `hydrateAgenticUI`
- existing motion / background / audio bridge methods

JS to Python:

- `sendLiveText`
- `sendText`
- skill and tool configuration actions

`app.js` now defines both:

- `window.echoes.setIdleMotionCandidates`
- `window.setIdleMotionCandidates`

This removes the previous runtime warning path for the missing function.

## Offline-safe Validation

The current validation set is expected to remain offline-safe:

```powershell
.\.venv\Scripts\python -m pytest -q
openspec.cmd validate --all
```

For `py_compile`, use a temporary output location when repository `__pycache__` writes are restricted:

```powershell
.\.venv\Scripts\python -c "import os,tempfile,py_compile; files=['main.py']; out=tempfile.mkdtemp(prefix='echoes-pyc-'); [py_compile.compile(f, cfile=os.path.join(out, os.path.basename(f)+'.pyc'), doraise=True) for f in files]"
```

## Current Smoke Scope

What is covered now:

- harness startup smoke
- normal runtime startup smoke in headless mode when available
- bridge contract tests
- background resolver tests
- voice runtime adapter tests
- grouped diagnostics contract tests

What still requires interactive visual checking on a real desktop:

- multi-viewport visual polish
- exact panel spacing and collapse feel
- live audio device behavior with real credentials and hardware
