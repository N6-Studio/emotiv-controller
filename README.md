# EMOTIV Movement Bridge

Desktop bridge between **EMOTIV Cortex** and your PC: it reads live **motion** (and optionally **mental command**) streams from the headset, maps them to abstract directions (`forward`, `backward`, `left`, `right`), shows them in a small UI, and can **simulate keyboard keys** (default WASD-style bindings).

The interface text is **Italian**. Core logic stays movement-agnostic; defaults are WASD for convenience.

---

## Prerequisites

- **EMOTIV Launcher** and **Cortex** installed and running locally, headset paired and visible in Cortex.
- **Python 3** (3.10+ recommended; the project is tested with recent 3.x releases).
- **Windows** is the primary target (global hotkey and keyboard simulation are tuned for it). macOS/Linux may work where `pynput` and Cortex allow.

---

## Install

From this directory (`python/`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install websocket-client python-dotenv pynput
```

For development and tests:

```powershell
pip install -r requirements-dev.txt
```

---

## Configure

### 1. Environment (`.env`)

Create a `.env` file **next to** `app.py` (same folder you run the app from). Cortex credentials come from the EMOTIV developer portal.

```env
EMOTIV_CLIENT_ID=your_client_id
EMOTIV_CLIENT_SECRET=your_client_secret
EMOTIV_LICENSE=
EMOTIV_DEBIT=1
CORTEX_URL=wss://localhost:6868
STREAMS=mot,com
```

| Variable | Meaning |
|----------|---------|
| `EMOTIV_CLIENT_ID` / `EMOTIV_CLIENT_SECRET` | Required Cortex API client credentials. |
| `EMOTIV_LICENSE` | Optional license string if your account requires it. |
| `EMOTIV_DEBIT` | Session debit flag (default `1`). |
| `CORTEX_URL` | Cortex WebSocket URL (default local Cortex). |
| `STREAMS` | Comma-separated Cortex streams; `mot` = motion, `com` = mental commands. Use `mot` only if you do not need mental commands. |

### 2. Local settings (`config.json`)

On first run, the app creates or updates `config.json` in the **current working directory** (typically the `python/` folder). It stores:

- Calibrated **neutral** head pose (`neutral_x`, `neutral_y`)
- **Threshold** for motion activation (global or per-direction)
- **Mental command power threshold** (minimum COM signal strength to treat a command as active)
- **Keyboard simulation** on/off
- **Key bindings** for motion and mental commands

You can edit `config.json` while the app is closed, or change most values from the in-app settings.

---

## Run

Always run from the folder that contains `app.py`, `.env`, and `config.json` so paths resolve correctly:

```powershell
cd D:\path\to\emotiv-wasd-bridge\python
.\.venv\Scripts\Activate.ps1
python app.py
```

---

## Using the app

1. **Start Cortex** and ensure the headset is connected before or shortly after opening the bridge.
2. Watch the **connection status** in the UI; if it fails, fix Cortex/Launcher and use any **retry** control offered.
3. **Calibrate neutral**: hold a comfortable “center” head pose and run the neutral calibration so movement is measured relative to that pose.
4. Use the **crosshair / pads** to confirm motion (and mental commands if `STREAMS` includes `com`) match what you expect.
5. **Simulated keyboard** can be turned on or off from settings. When off, no keys are sent—useful for testing the UI only.
6. **Sensitivity**: adjust the motion threshold (global or per direction) if activations are too weak or too twitchy.

### Global shortcut (toggle keyboard simulation)

- **Windows**: the app prefers **Ctrl+Shift+K**. If that combo is already registered by another program, it falls back to **Ctrl+Alt+K** and may show a short status hint. If native registration fails, **pynput** handles **Ctrl+Shift+K** or **Ctrl+Alt+K**.
- **Non-Windows**: **Ctrl+Shift+K** or **Ctrl+Alt+K** via `pynput`.

The shortcut flips keyboard simulation on/off and saves the choice to `config.json`.

---

## Optional: Windows executable

PyInstaller is configured in `app.spec`. From the **repository root** (this folder), use **Git Bash** (or another bash) on Windows:

```bash
bash ./scripts/build.sh --skip-sign
```

Output is under `dist/`. For a console build add `--debug`. Authenticode signing needs Windows, the Windows SDK (`signtool`), and either `--cert-thumbprint` or `--pfx-path` plus `PFX_PASSWORD` in the environment (omit `--skip-sign`). See comments at the top of [`scripts/build.sh`](scripts/build.sh).

### GitHub releases and updates

Releases for [N6-Studio/emotiv-controller](https://github.com/N6-Studio/emotiv-controller) are built by GitHub Actions when you push a version tag matching `v*` (for example `v1.0.0`). The workflow is [`.github/workflows/release-windows.yml`](.github/workflows/release-windows.yml): it runs on **windows-latest** with **bash**, calls [`scripts/build.sh`](scripts/build.sh), writes `latest.json`, and attaches `dist/app.exe` and `latest.json` to the GitHub Release.

#### Automating the tag from a `VERSION` file

The repo root file [`VERSION`](VERSION) holds the semver string **without** a `v` prefix (for example `0.1.0` or `0.2.0-beta.1`). When that file changes on `main` or `master`, [`.github/workflows/tag-from-version.yml`](.github/workflows/tag-from-version.yml) runs: if `refs/tags/v{VERSION}` already exists on the remote, it exits successfully and does nothing; otherwise it creates an **annotated** tag on the pushed commit and pushes it.

Pushes performed with the default `GITHUB_TOKEN` do not trigger other workflows, so the tag push uses a separate credential:

1. Create a **classic** personal access token with the `repo` scope, or a **fine-grained** token with **Contents: Read and write** for this repository only.
2. In the GitHub repo settings, add a secret named **`RELEASE_TAG_PAT`** with that token.

Without `RELEASE_TAG_PAT`, the workflow fails when a new tag is needed (existing tags still skip cleanly with no secret use).

You can still create tags manually with `git tag` / `git push`. To **retry** tag creation after a failed run without editing `VERSION`, use **Actions → Tag from VERSION → Run workflow**.

If a release build failed but the tag already exists, delete the tag (or bump `VERSION`) before expecting a new release; the automation will not move an existing tag.

**CI on Linux:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs [`scripts/ci-test.sh`](scripts/ci-test.sh) on **ubuntu-latest** (pytest).

Use this **stable manifest URL** when baking update checks into the shipped EXE:

`https://github.com/N6-Studio/emotiv-controller/releases/latest/download/latest.json`

Example (match `--app-version` to the tag you are shipping):

```bash
bash ./scripts/build.sh --skip-sign \
  --app-version "1.0.0" \
  --update-manifest-url "https://github.com/N6-Studio/emotiv-controller/releases/latest/download/latest.json"
```

After the first successful release, users with that URL in the build can use **Check for updates** under **Settings**.

---

## Tests

```bash
bash ./scripts/ci-test.sh
```

Or, with a venv already activated: `pytest`.

---

## Troubleshooting

| Issue | What to check |
|--------|----------------|
| Cannot connect | Cortex running, `CORTEX_URL`, firewall, correct `.env` credentials. |
| No motion | Headset streaming, calibration done, thresholds not too high. |
| No mental commands | `STREAMS` includes `com`; trained profile in Cortex; mental-command power threshold in Settings / `config.json`. |
| Keys not sent | “Keyboard simulation” enabled; OS permissions for accessibility/input monitoring where required. |
| Wrong working directory | Run from `python/` so `config.json` and `.env` are found. |

For product behavior and architecture detail, see `PRD.md` in this folder.
