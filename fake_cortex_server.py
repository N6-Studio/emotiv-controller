"""
Minimal fake EMOTIV Cortex WebSocket for manual testing of the Movement Bridge.

Emits the same JSON-RPC responses as Cortex for requestAccess → subscribe, then
pushes synthetic ``mot`` (and optionally ``com``) stream frames. For a few seconds
after streaming starts, the headset is upright (no lean) for calibration, then the
quaternion ``Q0..Q3`` follows a smooth circle in pitch/roll so lean cycles
**forward → right → backward → left** with constant outward tilt magnitude.

Setup::

    pip install -r requirements-dev.txt

Run from this directory (same folder as ``run.py``)::

    python fake_cortex_server.py

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
import sys
from typing import Any

import websockets


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
    """Hamilton ``(w, x, y, z) = q_pitch * q_roll`` matching ``core.quaternion_to_pitch_roll``.

    Pitch rotates about Y; roll rotates about X. Composing this way makes the
    decomposition recover the input pitch/roll exactly when |pitch| < 90°.
    """
    cp = math.cos(pitch_rad / 2.0)
    sp = math.sin(pitch_rad / 2.0)
    cr = math.cos(roll_rad / 2.0)
    sr = math.sin(roll_rad / 2.0)
    w = cp * cr
    x = cp * sr
    y = sp * cr
    z = -sp * sr
    return w, x, y, z


# Upright / no lean → identity quaternion → ~0° pitch and roll.
_QUAT_UPRIGHT = _quat_from_pitch_roll_rad(0.0, 0.0)


async def _stream_loop(
    websocket: Any,
    interval: float,
    include_com: bool,
    *,
    still_seconds: float,
    cycle_seconds: float,
    lean_deg: float,
    client: str,
) -> None:
    """Send ``mot`` frames whose quaternion traces a smooth lean cycle in pitch/roll.

    For ``still_seconds`` after streaming starts, the quaternion stays at identity
    (no tilt) so you can connect and calibrate. Then phase advances so combined
    tilt stays at fixed magnitude ``lean_deg`` (leaning "outward" around the
    compass): **forward** → **right** → **backward** → **left** → forward again.
    """
    _log_event(
        client,
        f"stream tick started (interval {interval}s, com={'on' if include_com else 'off'})",
    )
    if still_seconds > 0:
        _log_event(client, f"upright / calibration: no lean for {still_seconds}s, then lean cycle")
    t = 0.0
    two_pi = 2.0 * math.pi
    last_progress_log = 0.0
    progress_log_every = 2.0
    while True:
        await asyncio.sleep(interval)
        t += interval
        if t <= still_seconds:
            q0, q1, q2, q3 = _QUAT_UPRIGHT
            pitch_deg = 0.0
            roll_deg = 0.0
        else:
            if still_seconds > 0 and t - interval <= still_seconds:
                _log_event(client, "still phase ended; lean cycle running")
            t_rel = t - still_seconds
            phase = (t_rel / max(cycle_seconds, 0.5)) * two_pi
            # Circle in pitch-roll (degrees): forward at phase 0, then CCW to right, back, left.
            pitch_deg = -lean_deg * math.cos(phase)
            roll_deg = lean_deg * math.sin(phase)
            pitch_rad = math.radians(pitch_deg)
            roll_rad = math.radians(roll_deg)
            q0, q1, q2, q3 = _quat_from_pitch_roll_rad(pitch_rad, roll_rad)
            if t - last_progress_log >= progress_log_every:
                last_progress_log = t
                _log_event(
                    client,
                    f"lean tick t={t:.1f}s pitch={pitch_deg:.1f}° roll={roll_deg:.1f}° "
                    f"Q=({q0:.4f},{q1:.4f},{q2:.4f},{q3:.4f})",
                )
        mot = [
            0,
            0,
            round(q0, 6),
            round(q1, 6),
            round(q2, 6),
            round(q3, 6),
            0.0,
            0.0,
            1.0,
            -44.656766,
            -86.970985,
            23.221568,
        ]
        payload: dict = {"mot": mot}
        if include_com:
            if int(t / 2.0) % 3 == 0:
                payload["com"] = ["push", 0.55]
            else:
                payload["com"] = ["neutral", 0.0]
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
) -> None:
    async def _handler(ws: Any) -> None:
        await _connection_handler(
            ws,
            interval,
            include_com,
            still_seconds=still_seconds,
            cycle_seconds=cycle_seconds,
            lean_deg=lean_deg,
        )

    async with websockets.serve(_handler, host, port):
        print(f"Fake Cortex listening on ws://{host}:{port}", flush=True)
        print(f"Stream interval {interval}s; com={'on' if include_com else 'off'}", flush=True)
        if still_seconds > 0:
            print(
                f"Still (no lean) for {still_seconds}s after subscribe, then lean cycle starts.",
                flush=True,
            )
        print(
            f"Lean cycle {cycle_seconds}s, radius {lean_deg}° pitch/roll "
            "(forward→right→back→left→forward, constant outward lean)",
            flush=True,
        )
        await asyncio.get_running_loop().create_future()


def main() -> None:
    p = argparse.ArgumentParser(description="Fake EMOTIV Cortex WebSocket for local testing.")
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
        action="store_true",
        help="Include synthetic mental-command frames (com) alongside mot",
    )
    p.add_argument(
        "--cycle-seconds",
        type=float,
        default=16.0,
        help="Seconds for one full forward→right→back→left→forward lean loop (default 16)",
    )
    p.add_argument(
        "--lean-deg",
        type=float,
        default=14.0,
        help="Tilt magnitude in degrees (default 14, above typical 10° threshold)",
    )
    p.add_argument(
        "--still-seconds",
        type=float,
        default=5.0,
        help="Upright / no lean before the loop starts (default 5; use 0 to skip)",
    )
    args = p.parse_args()
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
            )
        )
    except KeyboardInterrupt:
        print("Stopped.", flush=True)


if __name__ == "__main__":
    main()
