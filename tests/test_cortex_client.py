import json
import threading
from unittest.mock import MagicMock

import pytest

from app import CortexClient


def _client():
    return CortexClient(
        on_stream=MagicMock(),
        on_status=MagicMock(),
        on_error=MagicMock(),
    )


def test_request_v2_success():
    client = _client()
    mock_ws = MagicMock()
    sent_ids: list[int] = []

    def send(payload: str):
        req = json.loads(payload)
        rid = req["id"]
        sent_ids.append(rid)
        client._on_message(
            mock_ws,
            json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}),
        )

    mock_ws.send = send
    client.ws = mock_ws
    client.ws_open = True

    assert client.request_v2("authorize", {"x": 1}) == {"ok": True}
    assert sent_ids == [1]


def test_request_v2_error_raises():
    client = _client()
    mock_ws = MagicMock()

    def send(payload: str):
        req = json.loads(payload)
        rid = req["id"]
        client._on_message(
            mock_ws,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"message": "access denied"},
                }
            ),
        )

    mock_ws.send = send
    client.ws = mock_ws
    client.ws_open = True

    with pytest.raises(RuntimeError, match="access denied"):
        client.request_v2("requestAccess", {})


def test_request_v2_timeout():
    client = _client()
    mock_ws = MagicMock()
    mock_ws.send = MagicMock()
    client.ws = mock_ws
    client.ws_open = True

    with pytest.raises(TimeoutError, match="Timeout: foo"):
        client.request_v2("foo", {}, timeout=0.01)


def test_on_message_rpc_does_not_call_on_stream():
    client = _client()
    mock_ws = MagicMock()
    client.pending[99] = {"event": threading.Event(), "response": None}
    client._on_message(
        mock_ws,
        json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"rpc": True}}),
    )
    client.on_stream.assert_not_called()


def test_on_message_stream_forwards_to_on_stream():
    client = _client()
    mock_ws = MagicMock()
    payload = {"mot": [0, 0, 1.0, 2.0]}
    client._on_message(mock_ws, json.dumps(payload))
    client.on_stream.assert_called_once_with(payload)
