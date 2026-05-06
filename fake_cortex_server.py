"""
Minimal fake EMOTIV Cortex WebSocket for manual testing of the Movement Bridge.

Emits the same JSON-RPC responses as Cortex for requestAccess → subscribe, then
pushes synthetic ``mot`` (and optionally ``com``) stream frames. For a few seconds
after streaming starts, the headset is upright (no lean) for calibration, then
tilt is driven in **random** mode by default: smoothed random waypoints within
``±lean_deg``. **ACCY** / **ACCZ** carry the synthetic lean (what the app uses).
Quaternions are filled for column compatibility only.

Setup::

    pip install -r requirements-dev.txt

Run from this directory (same folder as ``run.py``)::

    python fake_cortex_server.py

With no command-line arguments, timing and tilt parameters are picked at random
(bound address and port stay ``127.0.0.1:6868``). Pass flags to fix values.

Point the app at this server (``.env`` or environment settings UI).
Use ``ws://`` (not ``wss://``); this process does not terminate TLS::

    CORTEX_URL=ws://127.0.0.1:6868

Keep non-empty ``EMOTIV_CLIENT_ID`` and ``EMOTIV_CLIENT_SECRET`` in ``.env``; this
server does not validate them. Then start the app with ``python run.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import websockets

_root = Path(__file__).resolve().parent
_src = _root / "src"
for _path in (_src, _root):
    _s = str(_path)
    if _s not in sys.path:
        sys.path.insert(0, _s)

# Clamp synthetic euler-ish degrees before mapping to ACC (asin domain margin for quats).
_PITCH_CLAMP_MARGIN_DEG = 0.75
_JITTER_SIGMA_DEG = 0.25

_TILT_PITCH_MIN_DEG = -90.0
_TILT_PITCH_MAX_DEG = 90.0
_TILT_ROLL_MIN_DEG = -180.0
_TILT_ROLL_MAX_DEG = 180.0

# Baselines and gain chosen so ``--lean-deg`` ~14 produces ACC deltas comparable to real headset tuning.
_ACC_NEUT_Y = 0.12
_ACC_NEUT_Z = -0.01
_ACC_GAIN = 0.03
_ACC_X_IDLE = 0.92


def _client_addr(websocket: Any) -> str:
    try:
        host, port = websocket.remote_address  # type: ignore[attr-defined]
        return f"{host}:{port}"
    except Exception:
        return "unknown"


def _log_event(prefix: str, message: str) -> None:
    print(f"[fake-cortex {prefix}] {message}", flush=True)


def _rpc_result(req_id: int, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _handle_method(method: str, _params: dict) -> dict:
    if method == "requestAccess":
        return {"accessGranted": True}
    if method == "authorize":
        return {"cortexToken": "fake-cortex-token-for-testing"}
    if method == "queryHeadsets":
        return [{"id": "FAKE-EMOTIV-HEADSET-1", "status": "connected"}]
    if method == "controlDevice":
        return {}
    if method == "createSession":
        return {"id": "fake-session-1"}
    if method == "subscribe":
        return {
            "failure": [],
            "success": [
                {
                    "cols": [
                        "COUNTER_MEMS",
                        "INTERPOLATED_MEMS",
                        "Q0",
                        "Q1",
                        "Q2",
                        "Q3",
                        "ACCX",
                        "ACCY",
                        "ACCZ",
                        "MAGX",
                        "MAGY",
                        "MAGZ",
                    ],
                    "sid": "fake-session-mot",
                    "streamName": "mot",
                },
            ],
        }
    return {}


def _quat_from_pitch_roll_rad(pitch_rad: float, roll_rad: float) -> tuple[float, float, float, float]:
    """Hamilton ``(w, x, y, z)`` for optional quaternion columns (not used by the app)."""
    cp = math.cos(pitch_rad / 2.0)
    sp = math.sin(pitch_rad / 2.0)
    cr = math.cos(roll_rad / 2.0)
    sr = math.sin(roll_rad / 2.0)
    w = cp * cr
    x = cp * sr
    y = sp * cr
    z = -sp * sr
    return w, x, y, z


def _pitch_roll_deg_to_acc(pitch_deg: float, roll_deg: float) -> tuple[float, float, float]:
    """Map synthetic pitch/roll degrees to ACC X/Y/Z (same sign conventions as the live bridge)."""
    ay = _ACC_NEUT_Y + pitch_deg * _ACC_GAIN
    az = _ACC_NEUT_Z + roll_deg * _ACC_GAIN
    ax = _ACC_X_IDLE
    return ax, ay, az


def _pitch_roll_clamp_deg(pitch_deg: float, roll_deg: float) -> tuple[float, float]:
    pm = _PITCH_CLAMP_MARGIN_DEG
    p_lo = _TILT_PITCH_MIN_DEG + pm
    p_hi = _TILT_PITCH_MAX_DEG - pm
    p = max(p_lo, min(p_hi, pitch_deg))
    r = max(_TILT_ROLL_MIN_DEG, min(_TILT_ROLL_MAX_DEG, roll_deg))
    return p, r


def _sample_waypoint(lean_deg: float) -> tuple[float, float]:
    p = random.uniform(-lean_deg, lean_deg)
    r = random.uniform(-lean_deg, lean_deg)
    return _pitch_roll_clamp_deg(p, r)


def _randomize_stream_defaults(args: argparse.Namespace) -> None:
    """Fill motion/timing fields with random valid values (host/port unchanged)."""
    args.pattern = random.choice(("random", "circle"))
    args.interval = round(random.uniform(0.05, 0.12), 3)
    args.com = random.random() < 0.35
    args.cycle_seconds = round(random.uniform(10.0, 22.0), 1)
    args.lean_deg = round(random.uniform(9.0, 20.0), 1)
    args.still_seconds = round(random.uniform(2.0, 8.0), 1)
    args.waypoint_seconds = round(random.uniform(2.5, 6.0), 1)
    args.smooth_seconds = round(random.uniform(0.8, 2.5), 2)
    args.seed = None


_QUAT_UPRIGHT = _quat_from_pitch_roll_rad(0.0, 0.0)

# Synthetic mental-command stream: sharp rise → hold near max → ramp down (repeating).
_COM_ACTIONS = ("push", "pull", "lift", "drop")
_COM_PEAK_POWER = 0.92
_COM_RAMP_UP_S = 0.35
_COM_HOLD_HIGH_S = 2.0
_COM_RAMP_DOWN_S = 0.8
_COM_ACTION_PERIOD_S = 0.9


def _synthetic_com_sample(t: float) -> tuple[str, float]:
    """``pow`` ramps up quickly, stays high ~``_COM_HOLD_HIGH_S``, then falls; repeats."""
    ru = max(_COM_RAMP_UP_S, 1e-6)
    h = max(_COM_HOLD_HIGH_S, 0.0)
    rd = max(_COM_RAMP_DOWN_S, 1e-6)
    peak = max(0.0, min(1.0, _COM_PEAK_POWER))
    period = ru + h + rd
    u = (t % period) if period > 0 else 0.0
    if u < ru:
        power = peak * (u / ru)
    elif u < ru + h:
        power = peak
    else:
        down_u = u - ru - h
        power = peak * (1.0 - down_u / rd)
    power = max(0.0, min(1.0, power))
    idx = int(t / max(_COM_ACTION_PERIOD_S, 1e-6)) % len(_COM_ACTIONS)
    act = _COM_ACTIONS[idx]
    return act, round(power, 4)


async def _stream_loop(
    websocket: Any,
    interval: float,
    include_com: bool,
    *,
    still_seconds: float,
    cycle_seconds: float,
    lean_deg: float,
    pattern: str,
    waypoint_seconds: float,
    smooth_seconds: float,
    seed: int | None,
    client: str,
) -> None:
    """Send ``mot`` frames with synthetic ACC after an optional upright phase."""
    if seed is not None:
        random.seed(seed)
    _log_event(
        client,
        f"stream tick started (interval {interval}s, com={'on' if include_com else 'off'}, "
        f"pattern={pattern})",
    )
    if still_seconds > 0:
        _log_event(
            client,
            f"upright / calibration: no lean for {still_seconds}s, then "
            f"{'random waypoints' if pattern == 'random' else 'lean cycle'}",
        )
    t = 0.0
    two_pi = 2.0 * math.pi
    last_progress_log = 0.0
    progress_log_every = 2.0
    pitch_deg = 0.0
    roll_deg = 0.0
    target_pitch = 0.0
    target_roll = 0.0
    time_to_waypoint = 0.0
    tau = max(smooth_seconds, 1e-6)
    alpha = 1.0 - math.exp(-interval / tau)
    while True:
        await asyncio.sleep(interval)
        t += interval
        if t <= still_seconds:
            pitch_deg = 0.0
            roll_deg = 0.0
        else:
            if still_seconds > 0 and t - interval <= still_seconds:
                _log_event(
                    client,
                    "still phase ended; "
                    + ("random lean" if pattern == "random" else "lean cycle running"),
                )
            t_rel = t - still_seconds
            if pattern == "circle":
                phase = (t_rel / max(cycle_seconds, 0.5)) * two_pi
                pitch_deg = -lean_deg * math.cos(phase)
                roll_deg = lean_deg * math.sin(phase)
            else:
                if time_to_waypoint <= 0.0:
                    target_pitch, target_roll = _sample_waypoint(lean_deg)
                    time_to_waypoint = waypoint_seconds
                time_to_waypoint -= interval
                pitch_deg += alpha * (target_pitch - pitch_deg) + random.gauss(0.0, _JITTER_SIGMA_DEG)
                roll_deg += alpha * (target_roll - roll_deg) + random.gauss(0.0, _JITTER_SIGMA_DEG)
                pitch_deg, roll_deg = _pitch_roll_clamp_deg(pitch_deg, roll_deg)
        pitch_deg_s, roll_deg_s = _pitch_roll_clamp_deg(pitch_deg, roll_deg)
        pitch_rad = math.radians(pitch_deg_s)
        roll_rad = math.radians(roll_deg_s)
        q0, q1, q2, q3 = (
            _QUAT_UPRIGHT if t <= still_seconds else _quat_from_pitch_roll_rad(pitch_rad, roll_rad)
        )
        acc_x, acc_y, acc_z = _pitch_roll_deg_to_acc(pitch_deg_s, roll_deg_s)
        if t > still_seconds and t - last_progress_log >= progress_log_every:
            last_progress_log = t
            _log_event(
                client,
                f"lean tick t={t:.1f}s pitch={pitch_deg_s:.1f}° roll={roll_deg_s:.1f}° "
                f"ACC=({acc_x:.4f},{acc_y:.4f},{acc_z:.4f}) Q=({q0:.4f},{q1:.4f},{q2:.4f},{q3:.4f})",
            )
        mot = [
            0,
            0,
            round(q0, 6),
            round(q1, 6),
            round(q2, 6),
            round(q3, 6),
            round(acc_x, 6),
            round(acc_y, 6),
            round(acc_z, 6),
            -44.656766,
            -86.970985,
            23.221568,
        ]
        payload: dict = {"mot": mot}
        if include_com:
            ca, cp = _synthetic_com_sample(t)
            payload["com"] = [ca, cp]
        try:
            await websocket.send(json.dumps(payload))
        except Exception as exc:
            _log_event(client, f"stream send stopped: {exc!r}")
            break


async def _connection_handler(
    websocket: Any,
    interval: float,
    include_com: bool,
    *,
    still_seconds: float,
    cycle_seconds: float,
    lean_deg: float,
    pattern: str,
    waypoint_seconds: float,
    smooth_seconds: float,
    seed: int | None,
) -> None:
    client = _client_addr(websocket)
    stream_task: asyncio.Task | None = None
    _log_event(client, "client connected")
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                _log_event(client, f"ignored non-JSON message ({exc!r}): {raw[:200]!r}")
                continue
            if msg.get("jsonrpc") != "2.0" or "method" not in msg:
                _log_event(client, f"ignored non-RPC message: {msg!r}")
                continue
            req_id = msg.get("id")
            if req_id is None:
                _log_event(client, f"ignored notification (no id): {msg!r}")
                continue
            method = msg["method"]
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            _log_event(client, f"RPC in id={req_id} method={method} params={params!r}")
            result = _handle_method(method, params)
            await websocket.send(json.dumps(_rpc_result(req_id, result)))
            _log_event(client, f"RPC out id={req_id} method={method} ok")
            if method == "subscribe" and stream_task is None:
                _log_event(client, "subscribe accepted; starting mot stream task")
                stream_task = asyncio.create_task(
                    _stream_loop(
                        websocket,
                        interval,
                        include_com,
                        still_seconds=still_seconds,
                        cycle_seconds=cycle_seconds,
                        lean_deg=lean_deg,
                        pattern=pattern,
                        waypoint_seconds=waypoint_seconds,
                        smooth_seconds=smooth_seconds,
                        seed=seed,
                        client=client,
                    )
                )
    finally:
        _log_event(client, "client disconnected")
        if stream_task is not None:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass


async def _run_server(
    host: str,
    port: int,
    interval: float,
    include_com: bool,
    *,
    still_seconds: float,
    cycle_seconds: float,
    lean_deg: float,
    pattern: str,
    waypoint_seconds: float,
    smooth_seconds: float,
    seed: int | None,
) -> None:
    async def _handler(ws: Any) -> None:
        await _connection_handler(
            ws,
            interval,
            include_com,
            still_seconds=still_seconds,
            cycle_seconds=cycle_seconds,
            lean_deg=lean_deg,
            pattern=pattern,
            waypoint_seconds=waypoint_seconds,
            smooth_seconds=smooth_seconds,
            seed=seed,
        )

    async with websockets.serve(_handler, host, port):
        print(f"Fake Cortex listening on ws://{host}:{port}", flush=True)
        print(
            f"Stream interval {interval}s; com={'on' if include_com else 'off'}; pattern={pattern}",
            flush=True,
        )
        if still_seconds > 0:
            print(
                f"Still (no lean) for {still_seconds}s after subscribe, then motion starts.",
                flush=True,
            )
        if pattern == "circle":
            print(
                f"Lean cycle {cycle_seconds}s, radius {lean_deg}° pitch/roll "
                "(forward-right-back-left-forward; ACC tracks pitch/roll)",
                flush=True,
            )
        else:
            print(
                f"Random lean: ±{lean_deg}° targets every {waypoint_seconds}s, "
                f"smooth τ={smooth_seconds}s",
                flush=True,
            )
            if seed is not None:
                print(f"RNG seed {seed} (per-client stream)", flush=True)
        await asyncio.get_running_loop().create_future()


def main() -> None:
    no_cli_args = len(sys.argv) <= 1
    p = argparse.ArgumentParser(
        description="Fake EMOTIV Cortex WebSocket for local testing.",
        epilog="With no arguments, stream tuning (pattern, intervals, lean, etc.) is chosen at random; "
        "host/port stay 127.0.0.1:6868.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=6868, help="TCP port (default 6868, same as Cortex)")
    p.add_argument(
        "--interval",
        type=float,
        default=0.08,
        help="Seconds between stream ticks (default 0.08)",
    )
    p.add_argument(
        "--com",
        "--include-com",
        action="store_true",
        help="Include synthetic mental-command frames (com) alongside mot",
    )
    p.add_argument(
        "--cycle-seconds",
        type=float,
        default=16.0,
        help="Seconds for one full forward-right-back-left-forward lean loop (circle pattern; default 16)",
    )
    p.add_argument(
        "--lean-deg",
        type=float,
        default=14.0,
        help="Tilt magnitude in degrees (default 14; ACC swing scales with this)",
    )
    p.add_argument(
        "--still-seconds",
        type=float,
        default=5.0,
        help="Upright / no lean before the loop starts (default 5; use 0 to skip)",
    )
    p.add_argument(
        "--pattern",
        choices=("random", "circle"),
        default="random",
        help="Synthetic tilt: smoothed random waypoints (default) or legacy circle",
    )
    p.add_argument(
        "--waypoint-seconds",
        type=float,
        default=4.0,
        help="Random pattern: seconds between new pitch/roll targets (default 4)",
    )
    p.add_argument(
        "--smooth-seconds",
        type=float,
        default=1.5,
        help="Random pattern: exponential smoothing time constant toward target (default 1.5)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for reproducible random tilt (per connected client stream)",
    )
    args = p.parse_args()
    if no_cli_args:
        _randomize_stream_defaults(args)
        print(
            "No CLI args: random tuning — "
            f"pattern={args.pattern}, interval={args.interval}s, com={'on' if args.com else 'off'}, "
            f"still={args.still_seconds}s, lean=±{args.lean_deg}°, cycle={args.cycle_seconds}s, "
            f"waypoint={args.waypoint_seconds}s, smooth={args.smooth_seconds}s",
            flush=True,
        )
    if args.interval <= 0:
        print("interval must be positive", file=sys.stderr)
        sys.exit(2)
    if args.cycle_seconds <= 0:
        print("--cycle-seconds must be positive", file=sys.stderr)
        sys.exit(2)
    if args.lean_deg <= 0:
        print("--lean-deg must be positive", file=sys.stderr)
        sys.exit(2)
    if args.still_seconds < 0:
        print("--still-seconds must be >= 0", file=sys.stderr)
        sys.exit(2)
    if args.pattern == "random":
        if args.waypoint_seconds <= 0:
            print("--waypoint-seconds must be positive", file=sys.stderr)
            sys.exit(2)
        if args.smooth_seconds <= 0:
            print("--smooth-seconds must be positive", file=sys.stderr)
            sys.exit(2)
    try:
        asyncio.run(
            _run_server(
                args.host,
                args.port,
                args.interval,
                args.com,
                still_seconds=args.still_seconds,
                cycle_seconds=args.cycle_seconds,
                lean_deg=args.lean_deg,
                pattern=args.pattern,
                waypoint_seconds=args.waypoint_seconds,
                smooth_seconds=args.smooth_seconds,
                seed=args.seed,
            )
        )
    except KeyboardInterrupt:
        print("Stopped.", flush=True)


if __name__ == "__main__":
    main()
