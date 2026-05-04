"""
Minimal fake EMOTIV Cortex WebSocket for manual testing of the Movement Bridge.

Emits the same JSON-RPC responses as Cortex for requestAccess → subscribe, then
pushes synthetic ``mot`` (and optionally ``com``) stream frames.

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


def _quat_mul(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    w0, x0, y0, z0 = a
    w1, x1, y1, z1 = b
    return (
        w0 * w1 - x0 * x1 - y0 * y1 - z0 * z1,
        w0 * x1 + x0 * w1 + y0 * z1 - z0 * y1,
        w0 * y1 - x0 * z1 + y0 * w1 + z0 * x1,
        w0 * z1 + x0 * y1 - y0 * x1 + z0 * w1,
    )


async def _stream_loop(websocket: Any, interval: float, include_com: bool) -> None:
    """Send mot (and optionally com) messages until the connection dies."""
    t = 0.0
    while True:
        await asyncio.sleep(interval)
        t += interval
        pitch_rad = math.radians(12.0 * math.sin(t * 1.5))
        roll_rad = math.radians(8.0 * math.cos(t * 1.1))
        q_pitch = (math.cos(pitch_rad / 2), 0.0, math.sin(pitch_rad / 2), 0.0)
        q_roll = (math.cos(roll_rad / 2), math.sin(roll_rad / 2), 0.0, 0.0)
        w, x, y, z = _quat_mul(q_roll, q_pitch)
        mot = [
            0,
            0,
            round(w, 6),
            round(x, 6),
            round(y, 6),
            round(z, 6),
            0.948257,
            -0.354986,
            -0.083497,
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
        except Exception:
            break


async def _connection_handler(websocket: Any, interval: float, include_com: bool) -> None:
    stream_task: asyncio.Task | None = None
    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("jsonrpc") != "2.0" or "method" not in msg:
                continue
            req_id = msg.get("id")
            if req_id is None:
                continue
            method = msg["method"]
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            result = _handle_method(method, params)
            await websocket.send(json.dumps(_rpc_result(req_id, result)))
            if method == "subscribe" and stream_task is None:
                stream_task = asyncio.create_task(
                    _stream_loop(websocket, interval, include_com)
                )
    finally:
        if stream_task is not None:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass


async def _run_server(host: str, port: int, interval: float, include_com: bool) -> None:
    async with websockets.serve(
        lambda ws: _connection_handler(ws, interval, include_com),
        host,
        port,
    ):
        print(f"Fake Cortex listening on ws://{host}:{port}", flush=True)
        print(f"Stream interval {interval}s; com={'on' if include_com else 'off'}", flush=True)
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
    args = p.parse_args()
    if args.interval <= 0:
        print("interval must be positive", file=sys.stderr)
        sys.exit(2)
    try:
        asyncio.run(_run_server(args.host, args.port, args.interval, args.com))
    except KeyboardInterrupt:
        print("Stopped.", flush=True)


if __name__ == "__main__":
    main()
