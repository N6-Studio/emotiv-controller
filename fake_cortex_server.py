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
        return {"failure": []}
    return {}


async def _stream_loop(websocket: Any, interval: float, include_com: bool) -> None:
    """Send mot (and optionally com) messages until the connection dies."""
    t = 0.0
    while True:
        await asyncio.sleep(interval)
        t += interval
        x = 50.0 + 12.0 * math.sin(t * 1.5)
        y = 0.0 + 8.0 * math.cos(t * 1.1)
        payload: dict = {"mot": [0, 0, round(x, 4), round(y, 4)]}
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
