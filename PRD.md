# PRD — EMOTIV Movement Bridge

## 1. Product Summary

The EMOTIV Movement Bridge is a desktop application that connects to EMOTIV Cortex, reads live headset motion and mental command streams, maps those signals to abstract movement states, and optionally simulates keyboard input based on those movements.

The application provides a compact Italian-language UI for calibration, live movement visualization, movement activation feedback, keyboard simulation control, and configuration management.

The system should remain movement-agnostic internally. Although the default UI displays WASD-style buttons and the default keyboard bindings are W/A/S/D, the core logic must use abstract movement names such as `forward`, `backward`, `left`, and `right`.

---

## 2. Goals

### 2.1 Primary Goals

* Provide a Python desktop replacement for the existing Node.js EMOTIV-to-WASD bridge.
* Connect to EMOTIV Cortex using the configured client credentials.
* Subscribe to EMOTIV motion and optional mental command streams.
* Allow the user to calibrate a neutral head position.
* Persist the calibrated neutral position and app settings locally.
* Detect movement activations relative to the neutral position.
* Show live movement status in a small compact UI.
* Optionally simulate keyboard presses from detected movements.
* Allow keyboard simulation to be enabled or disabled from settings and via a global shortcut.
* Display a crosshair that visualizes the current live position relative to the neutral position.

### 2.2 Secondary Goals

* Make thresholds configurable.
* Keep the architecture extensible for non-WASD bindings in the future.
* Provide clear debug information for live x/y values and neutral x/y values.
* Keep the UI simple enough to use during demos, tests, or gameplay.

---

## 3. Non-Goals

* The first version does not need advanced key rebinding UI.
* The first version does not need cloud persistence.
* The first version does not need multi-profile support.
* The first version does not need advanced smoothing, filtering, or machine learning movement classification.
* The first version does not need packaged installers for Windows/macOS/Linux.
* The first version does not need full EMOTIV training or mental command profile management.

---

## 4. Target Users

### 4.1 Primary User

A developer, researcher, gamer, or demo operator using an EMOTIV headset to control software through movement-based or mental-command-based inputs.

### 4.2 User Needs

The user needs to:

* Start the bridge quickly.
* See whether Cortex is connected.
* Calibrate a neutral position.
* Confirm that movement detection works visually.
* Enable or disable real keyboard output safely.
* Adjust sensitivity when movement activation is too weak or too sensitive.
* Debug live x/y motion values.

---

## 5. Supported Platforms

Initial target:

* Desktop Python application.
* Windows is expected to be the main runtime environment because simulated keyboard control is likely used for games or desktop apps.

Secondary support:

* macOS and Linux should be possible when dependencies support keyboard simulation and global shortcuts.

---

## 6. Assumptions

* EMOTIV Launcher and Cortex are installed and running locally.
* Cortex is available by default at `wss://localhost:6868`.
* The user has valid EMOTIV client credentials.
* The headset is paired and visible to Cortex.
* The motion stream provides x/y values at the last two positions of the `mot` payload, matching the existing Node.js behavior.
* Keyboard simulation may require OS permissions on some platforms.

---

## 7. Configuration

### 7.1 Environment Configuration

The app should read the following values from `.env`:

```env
EMOTIV_CLIENT_ID=your_client_id
EMOTIV_CLIENT_SECRET=your_client_secret
EMOTIV_LICENSE=
EMOTIV_DEBIT=1
CORTEX_URL=wss://localhost:6868
STREAMS=mot,com
COM_POWER_THRESHOLD=0.25
```

### 7.2 Persisted Local Configuration

The app should persist user settings in a local JSON file, for example:

```json
{
  "neutral_x": 70.12,
  "neutral_y": -0.31,
  "threshold": 5.0,
  "keyboard_enabled": false,
  "com_power_threshold": 0.25,
  "key_bindings": {
    "forward": "w",
    "left": "a",
    "backward": "s",
    "right": "d"
  }
}
```

### 7.3 Default Values

| Setting                        | Default |
| ------------------------------ | ------: |
| Movement threshold             |   `5.0` |
| Keyboard simulation            | `false` |
| Mental command power threshold |  `0.25` |
| Forward key                    |     `w` |
| Left key                       |     `a` |
| Backward key                   |     `s` |
| Right key                      |     `d` |

---

## 8. Core Concepts

### 8.1 Movement-Agnostic Model

The app must not hard-code WASD as the internal movement model. Instead, it should use movement identifiers:

* `forward`
* `backward`
* `left`
* `right`

WASD should only be used as default labels and default keyboard bindings.

### 8.2 Neutral Position

The neutral position is the average x/y motion value collected during a 10-second calibration period.

Stored values:

* `neutral_x`
* `neutral_y`

### 8.3 Movement Threshold

Movement detection should compare live x/y values against the persisted neutral position.

Default threshold: `5.0`.

Detection rules:

| Movement | Rule                         |
| -------- | ---------------------------- |
| Forward  | `x <= neutral_x - threshold` |
| Backward | `x >= neutral_x + threshold` |
| Left     | `y <= neutral_y - threshold` |
| Right    | `y >= neutral_y + threshold` |

### 8.4 Keyboard Simulation

Keyboard simulation should be optional.

When disabled:

* The UI still shows live active movements.
* No keyboard keys are pressed.
* Any previously pressed keys must be released immediately.

When enabled:

* Active movements press their configured keys.
* Inactive movements release their configured keys.
* Pressed state must be tracked to avoid repeated press events.

---

## 9. User Experience

### 9.1 Language

All UI text should be in Italian.

Examples:

* `Controllo movimenti`
* `Inizializza`
* `Impostazioni`
* `Resta in posizione neutra per 10 secondi.`
* `Salva`
* `Riprova`
* `Annulla`
* `Tastiera simulata`

### 9.2 Window Layout

The app should use a compact desktop window.

Recommended initial size:

* Width: approximately 420 px.
* Height: approximately 460 px.

The viewport should feel small and almost square.

### 9.3 Visual Style

The UI should be simple, high-contrast, and readable.

Recommended style:

* Dark background.
* Dim inactive movement buttons.
* Green active movement buttons.
* Crosshair behind the main UI elements.
* Debug x/y values visible in relevant screens.

---

## 10. App Views

## 10.1 Main View

### Purpose

The default view for monitoring movement state and controlling the app.

### Content

The main view should show:

* App title.
* Cortex connection status.
* Live x/y values.
* Movement buttons laid out like WASD:

  * Forward at top.
  * Left, backward, right below.
* Keyboard simulation status.
* Button to start initialization/calibration.
* Button to open settings.
* Crosshair in the background.

### Behavior

* Movement buttons are dim when inactive.
* Movement buttons turn green when active.
* Live x/y values update continuously.
* Crosshair updates continuously.
* If keyboard simulation is enabled, active movements are converted into keyboard presses.

---

## 10.2 Initialization / Calibration View

### Purpose

Guide the user through neutral-position calibration.

### Entry Point

The user clicks `Inizializza` from the main view.

### Content

The view should show:

* Title: `Inizializzazione`.
* Instruction: `Resta in posizione neutra per 10 secondi.`
* Countdown timer.
* Running neutral x/y average for debugging.
* Cancel button.

### Behavior

* The user must remain in a neutral position for 10 seconds.
* The app collects live x/y motion samples during this period.
* The app calculates average x/y values.
* The app does not persist values immediately after the timer ends.
* After 10 seconds, the app moves to the calibration review view.

### Error State

If no motion samples are received during calibration:

* Show an error message in Italian.
* Return to the main view or allow retry.

---

## 10.3 Calibration Review View

### Purpose

Allow the user to test the new neutral position before saving it.

### Content

The review view should show:

* Title: `Verifica configurazione`.
* Live x/y values.
* Proposed neutral x/y values.
* Movement buttons with live active/inactive feedback.
* Buttons:

  * `Annulla`
  * `Salva`
  * `Riprova`

### Behavior

* The proposed neutral position is used temporarily for movement detection.
* Movement buttons are dim when inactive and green when active.
* The live crosshair uses the proposed neutral position.
* Clicking `Salva` persists the proposed neutral position.
* Clicking `Riprova` restarts the calibration view.
* Clicking `Annulla` discards the proposed values and returns to the main view.

---

## 10.4 Settings View

### Purpose

Allow the user to configure app behavior.

### Content

The settings view should show:

* Toggle for keyboard simulation.
* Shortcut hint for keyboard simulation toggle.
* Movement threshold input.
* Mental command power threshold input.
* Save button.
* Back button.

### Behavior

* The user can enable or disable simulated keyboard presses.
* The user can change the movement threshold.
* The user can change the mental command power threshold.
* Changes should be persisted when saved.
* Returning without saving should not persist changes.

---

## 11. Global Shortcut

### Requirement

The app should provide a global shortcut for toggling keyboard simulation.

Default shortcut:

```text
Ctrl + Shift + K
```

### Behavior

When triggered:

* If keyboard simulation is disabled, enable it.
* If keyboard simulation is enabled, disable it.
* Persist the updated value.
* Release all currently pressed simulated keys when disabling.
* Update UI status if the settings or main view is visible.

---

## 12. Crosshair

### Purpose

The crosshair provides a live visual representation of the user's current x/y position relative to the neutral position.

### Visual Behavior

* A fixed reference crosshair should be drawn at the center of the viewport.
* The live position indicator should move relative to the neutral position.
* If no neutral position exists, the live indicator should default to the center.
* The crosshair should be behind all main UI elements.

### Position Mapping

The app should calculate:

```text
relative_x = live_y - neutral_y
relative_y = live_x - neutral_x
```

The UI may scale the relative values for visibility.

The live indicator should be clamped so it remains inside the viewport.

---

## 13. EMOTIV Cortex Integration

### 13.1 Connection Flow

The app should:

1. Connect to Cortex WebSocket.
2. Request access using client ID and client secret.
3. Authorize using client ID, client secret, license, and debit value.
4. Query available headsets.
5. Select the connected headset if available, otherwise select the first available headset.
6. Connect the headset if it is not already connected.
7. Create an active session.
8. Subscribe to configured streams.
9. Process incoming stream messages.

### 13.2 Supported Streams

Initial supported streams:

* `mot`
* `com`

### 13.3 Motion Stream Handling

For `mot` messages:

* Extract `x` from the second-last item in the `mot` array.
* Extract `y` from the last item in the `mot` array.
* Update live x/y display.
* Update crosshair.
* Detect active movements.

### 13.4 Mental Command Stream Handling

For `com` messages:

* Extract action from `com[0]`.
* Extract power from `com[1]`.
* Ignore the command if power is below the configured threshold.

Mapping:

| Mental command | Movement   |
| -------------- | ---------- |
| `push`         | `forward`  |
| `pull`         | `backward` |
| `left`         | `left`     |
| `right`        | `right`    |

---

## 14. Movement Resolution

### 14.1 Multiple Inputs

If both motion and mental command streams are active:

* The app should combine detected movements from both sources.
* Duplicate movements should be removed.

### 14.2 Opposing Movements

Initial version may allow one vertical and one horizontal movement at the same time:

* `forward` + `left`
* `forward` + `right`
* `backward` + `left`
* `backward` + `right`

For the same axis, motion mapping should prefer one direction using `if / else if` logic:

* Either `forward` or `backward`, not both from motion.
* Either `left` or `right`, not both from motion.

If mental commands conflict with motion, the first version may simply combine states. A future version may add conflict-resolution priority.

---

## 15. Safety Requirements

* Keyboard simulation must default to disabled.
* All simulated keys must be released when keyboard simulation is disabled.
* All simulated keys must be released when the app closes.
* All simulated keys must be released on connection errors where possible.
* All simulated keys must be released on process interruption where possible.
* The UI must clearly show whether keyboard simulation is enabled.

---

## 16. Error Handling

### 16.1 Missing Credentials

If `EMOTIV_CLIENT_ID` or `EMOTIV_CLIENT_SECRET` is missing:

* Show an Italian error/status message.
* Do not crash silently.

### 16.2 Access Not Granted

If Cortex access is not granted:

* Show a message instructing the user to approve the app in EMOTIV Launcher.

### 16.3 No Headset Found

If no headset is found:

* Show an Italian error/status message.
* Keep the UI open if possible.

### 16.4 WebSocket Errors

If the WebSocket connection fails:

* Show an Italian error/status message.
* Release all simulated keys.

### 16.5 Calibration Without Data

If no samples are collected during calibration:

* Show an Italian error message.
* Do not overwrite the previous neutral configuration.

---

## 17. Data Persistence

### 17.1 File Location

Initial version may store settings in a local `config.json` file next to the application script.

### 17.2 Persistence Timing

Persist when:

* The user saves calibration.
* The user saves settings.
* The user toggles keyboard simulation using the shortcut.

Do not persist when:

* The user cancels calibration.
* The user exits settings without saving.
* Calibration completes but the user has not clicked `Salva`.

---

## 18. Functional Requirements

### FR-1: Cortex Connection

The app shall connect to EMOTIV Cortex via WebSocket using the configured Cortex URL.

### FR-2: Cortex Authorization

The app shall request access and authorize using environment-provided EMOTIV credentials.

### FR-3: Headset Session

The app shall find a headset, connect it if needed, create an active session, and subscribe to configured streams.

### FR-4: Motion Data Processing

The app shall process incoming `mot` stream messages and extract live x/y values.

### FR-5: Mental Command Processing

The app shall process incoming `com` stream messages and map supported mental commands to abstract movements.

### FR-6: Neutral Calibration

The app shall provide a 10-second neutral-position calibration flow.

### FR-7: Calibration Review

The app shall allow users to preview, save, cancel, or retry a newly calculated neutral position.

### FR-8: Persisted Configuration

The app shall save and load neutral position, thresholds, keyboard enabled state, and key bindings from local JSON configuration.

### FR-9: Movement Detection

The app shall detect abstract movements by comparing live x/y values against neutral x/y values and configured thresholds.

### FR-10: Movement UI Feedback

The app shall show dim inactive movement buttons and green active movement buttons.

### FR-11: Live Debug Values

The app shall show live x/y values in the main and review views.

### FR-12: Crosshair

The app shall show a centered crosshair and a live position indicator relative to neutral position.

### FR-13: Keyboard Simulation Toggle

The app shall allow users to enable or disable simulated keyboard presses from settings.

### FR-14: Keyboard Simulation Shortcut

The app shall allow users to toggle simulated keyboard presses via a global shortcut.

### FR-15: Safe Key Release

The app shall release all simulated keys when disabling keyboard simulation, closing the app, or encountering a connection error.

---

## 19. Non-Functional Requirements

### NFR-1: Responsiveness

The UI should update live values and movement state smoothly enough for interactive use.

Target refresh rate:

* Approximately 30 updates per second.

### NFR-2: Reliability

The app should avoid leaving keys stuck in a pressed state.

### NFR-3: Simplicity

The UI should remain compact and easy to understand.

### NFR-4: Extensibility

Movement logic should be separated from keyboard binding logic.

### NFR-5: Local-First Behavior

All configuration should work without internet access once Cortex and credentials are available locally.

---

## 20. Acceptance Criteria

### AC-1: App Launch

Given valid credentials and a running Cortex instance, when the user launches the app, then the app connects to Cortex and displays a ready/connected status.

### AC-2: Missing Credentials

Given missing credentials, when the user launches the app, then the app shows an error and does not attempt to control the keyboard.

### AC-3: Calibration Timer

Given the user clicks `Inizializza`, when calibration starts, then the app displays a 10-second countdown and records live x/y samples.

### AC-4: Calibration Review

Given calibration completes with valid samples, when the timer ends, then the app shows the review screen with proposed neutral x/y values.

### AC-5: Save Calibration

Given the review screen is visible, when the user clicks `Salva`, then the neutral x/y values are saved to local configuration.

### AC-6: Cancel Calibration

Given the review screen is visible, when the user clicks `Annulla`, then the proposed neutral values are discarded.

### AC-7: Retry Calibration

Given the review screen is visible, when the user clicks `Riprova`, then a new 10-second calibration starts.

### AC-8: Movement Activation

Given a saved neutral position and threshold of 5, when live x is at least 5 below neutral x, then the `forward` movement becomes active.

### AC-9: Movement Deactivation

Given a movement is active, when live x/y returns within threshold range, then the movement becomes inactive.

### AC-10: UI Highlight

Given a movement is active, then its UI button is green. Given it is inactive, then it is dim.

### AC-11: Keyboard Disabled Default

Given a fresh configuration, when the app launches, then keyboard simulation is disabled by default.

### AC-12: Keyboard Toggle Shortcut

Given the app is running, when the user presses `Ctrl + Shift + K`, then keyboard simulation toggles and the setting is persisted.

### AC-13: Safe Release

Given keyboard simulation is enabled and a key is pressed, when keyboard simulation is disabled, then the key is released.

### AC-14: Crosshair Neutral Center

Given a neutral position exists and the live position equals neutral position, then the live crosshair indicator is centered.

### AC-15: Crosshair Without Neutral

Given no neutral position exists, then the crosshair indicator defaults to the center.

---

## 21. Future Enhancements

* Add key rebinding UI.
* Add profile management for different users or games.
* Add smoothing or filtering for noisy motion data.
* Add dead zones separate from activation thresholds.
* Add per-axis thresholds.
* Add sensitivity sliders for crosshair visualization.
* Add conflict-resolution strategy between motion and mental commands.
* Add connection retry and headset selection UI.
* Add packaging as a standalone executable.
* Add logs export for debugging calibration and stream data.
* Add visual graph of x/y over time.

---

## 22. Open Questions

1. Should mental commands override motion commands, or should they be merged equally?
2. Should threshold be global, per-axis, or per-movement?
3. Should keyboard bindings be editable in version 1 or left as config-only?
4. Should the app support multiple saved calibration profiles?
5. Should the crosshair movement use raw values or smoothed values?
6. Should calibration ignore outlier samples?
7. Should the app auto-disable keyboard simulation when it loses Cortex connection?

---

## 23. Suggested MVP Scope

The MVP should include:

* Python Cortex connection.
* Motion stream support.
* Optional mental command stream support.
* Main live movement view.
* 10-second neutral calibration.
* Calibration review with save/retry/cancel.
* Config persistence.
* Keyboard simulation toggle.
* Global shortcut.
* Crosshair visualization.
* Italian UI text.

The MVP should not include:

* Full key rebinding UI.
* Multi-profile support.
* Advanced smoothing.
* Installer packaging.
