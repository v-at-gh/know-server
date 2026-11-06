import asyncio
import json
import socket

import pytest

from web.net import (
    PingServer, ping_app,
    encode_ping, encode_pong, encode_error, decode_line,
)


def test_encode_decode_ping():
    raw = encode_ping("admin").decode("utf-8")
    obj = decode_line(raw)
    assert obj is not None
    assert obj["type"] == "ping"
    assert obj["from"] == "admin"
    assert "ts" in obj


def test_decode_malformed():
    assert decode_line("not json") is None
    assert decode_line('{"no_type":1}') is None
    assert decode_line("[]") is None


@pytest.mark.asyncio
async def test_server_echoes_pong():
    server = PingServer(host="127.0.0.1", port=0)
    await server.start()
    assert server.is_running
    port = server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(encode_ping("admin"))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
    writer.close()
    await writer.wait_closed()

    obj = json.loads(line.decode("utf-8"))
    assert obj["type"] == "pong"

    pings = server.recent_pings()
    assert len(pings) == 1
    assert pings[0]["source_ip"] == "127.0.0.1"

    await server.stop()
    assert not server.is_running


@pytest.mark.asyncio
async def test_server_rejects_malformed():
    server = PingServer(host="127.0.0.1", port=0)
    await server.start()
    port = server.port

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"not json\n")
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
    writer.close()
    await writer.wait_closed()

    obj = json.loads(line.decode("utf-8"))
    assert obj["type"] == "error"
    assert obj["reason"] == "malformed"

    await server.stop()


@pytest.mark.asyncio
async def test_ping_app_timeout():
    async def silent(reader, writer):
        # Accept, then hang — do not respond.
        await asyncio.sleep(5)
        writer.close()

    silent_server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = silent_server.sockets[0].getsockname()[1]

    result = await ping_app("127.0.0.1", port=port, timeout=0.5)
    assert result["status"] == "error"
    assert "timeout" in result["reason"]

    silent_server.close()
    await silent_server.wait_closed()
