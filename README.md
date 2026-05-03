# EMOTIV Movement Bridge

**Turn head movements from your EMOTIV headset into keys on your PC** — so you can move in games or apps using **W, A, S, D** (or keys you choose) without touching the keyboard.

A small window shows what the app thinks you are doing (forward, back, left, right). The on-screen text is **Italian**; you do not need to change anything for that.

---

## What you need

- An **EMOTIV** headset that works with **EMOTIV Cortex** (Launcher + Cortex installed on your computer, headset paired and visible in Cortex).
- **Windows** is what this app is built for first. Other systems might work but are not the main focus.
- An **EMOTIV developer account** so you can get the **Client ID** and **Client Secret** Cortex asks for (the app needs these once to connect).

---

## If you downloaded the Windows program (.exe)

1. Put the file somewhere you like (for example your Desktop or a folder you use for games).
2. Run **EMOTIV Launcher** and **Cortex**, and connect your headset.
3. Double-click the app. Enter your **Client ID** and **Client Secret** when the app asks (or in **Settings**), if you have not already.
4. Follow the steps inside the app: wait until it says it is connected, then **calibrate** your “neutral” head position when prompted.
5. Turn **keyboard simulation** on when you want keys to be sent to your game or app. You can turn it off anytime to test without sending keys.

**Updates:** If your build supports it, use **Check for updates** under **Settings** after releases are published.

---

## If you are running it from the project folder (for advanced users)

You need **Python 3** (3.10 or newer is a good choice). Open a terminal in the `python` folder, create a virtual environment, install dependencies, then start the app:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install websocket-client python-dotenv pynput
python app.py
```

Always start the app from the same folder as `app.py` so it finds your settings files.

---

## First-time tips

1. **Start Cortex first** and make sure the headset is connected before or right after you open the bridge.
2. **Calibrate “neutral”** while sitting comfortably straight — later movements are compared to that pose.
3. If movement feels **too sensitive or too weak**, open **Settings** and adjust the sensitivity / thresholds until it feels right.
4. **Keyboard off** is useful while you learn the app: you see the directions on screen, but no keys are pressed.
5. **Quick shortcut (Windows):** **Ctrl+Shift+K** turns keyboard simulation on or off. If another program already uses that, try **Ctrl+Alt+K**.

---

## Something not working?

| What you notice | What to try |
|-----------------|-------------|
| Will not connect | Cortex and Launcher running? Headset on? Client ID and Secret correct? |
| No movement | Calibrate again; lower the sensitivity threshold a little. |
| Mental commands do nothing | They must be trained in Cortex and enabled in your stream/settings; if you only care about head motion, you can ignore mental commands. |
| Keys do not reach the game | Turn on keyboard simulation; on some PCs, security software may ask for permission to “control” the keyboard — allow it if you trust this app. |
| Odd behavior | Start the app from the folder that contains `app.py` and your config files so nothing is “lost.” |

---

## For developers

Tests, packaging with PyInstaller, release workflows, and deeper behavior are described in **`PRD.md`** and in **`scripts/build.sh`** (comments at the top). CI runs `scripts/ci-test.sh` (pytest on Linux). Release automation lives under `.github/workflows/`.
