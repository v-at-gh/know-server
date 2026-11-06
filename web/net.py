"""TCP ping/pong channel between the admin panel and Android devices.

Wire format: one UTF-8 line of JSON terminated by '\\n'. See the Android
task for the mirrored protocol. The two implementations must agree on:
  - {"type":"ping","from":"app"|"admin","ts":"<ISO>"}
  - {"type":"pong","ts":"<ISO>"}
  - {"type":"error","reason":"malformed"|"unknown type"}
  - no keep-alive; one pair per connection
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("web.net")

DEFAULT_PORT = 42069
DEFAULT_TIMEOUT = 5.0


# ---------- wire format ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def encode_ping(from_side: str) -> bytes:
    payload = {"type": "ping", "from": from_side, "ts": _now_iso()}
    return (json.dumps(payload) + "\n").encode("utf-8")


def encode_pong() -> bytes:
    payload = {"type": "pong", "ts": _now_iso()}
    return (json.dumps(payload) + "\n").encode("utf-8")


def encode_error(reason: str) -> bytes:
    payload = {"type": "error", "reason": reason}
    return (json.dumps(payload) + "\n").encode("utf-8")


def decode_line(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if "type" not in obj:
        return None
    return obj


# ---------- server ----------

class PingServer:
    """Async TCP listener. Records incoming pings, replies with pongs.

    start()/stop() are idempotent. If the port is unavailable, start()
    logs a warning and returns; is_running stays False.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        max_events: int = 50,
    ) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def port(self) -> int:
        if self._server is not None:
            for sock in self._server.sockets or []:
                return sock.getsockname()[1]
        return self._port

    async def start(self) -> None:
        if self._server is not None:
            return
        try:
            self._server = await asyncio.start_server(
                self._handle, self._host, self._port,
                family=socket.AF_INET,
            )
            log.info("ping server listening on %s:%s", self._host, self.port)
        except OSError as e:
            log.warning("ping server failed to bind %s:%s — %s",
                        self._host, self._port, e)
            self._server = None

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:
            pass
        self._server = None

    def recent_pings(self) -> list[dict[str, Any]]:
        return list(self._events)  # newest last; caller reverses if needed

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        source_ip = peer[0] if peer else "?"
        try:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=DEFAULT_TIMEOUT)
            except asyncio.TimeoutError:
                return

            text = raw.decode("utf-8", errors="replace")
            msg = decode_line(text)
            if msg is None:
                writer.write(encode_error("malformed"))
                await writer.drain()
                return

            mtype = msg.get("type")
            if mtype == "ping":
                self._events.append({
                    "ts": _now_iso(),
                    "source_ip": source_ip,
                    "from": msg.get("from", "?"),
                })
                writer.write(encode_pong())
                await writer.drain()
            elif mtype == "pong":
                log.warning("unexpected pong received on listener from %s", source_ip)
            else:
                writer.write(encode_error("unknown type"))
                await writer.drain()
        except Exception as e:
            log.debug("ping handler error from %s: %s", source_ip, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---------- client ----------

async def ping_app(
    host: str,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Send one ping, wait for one pong. Never raises.

    Returns {"status": "ok", "rtt_ms": N} or {"status": "error", "reason": "..."}.
    """
    t0 = time.monotonic()
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.write(encode_ping("admin"))
        await writer.drain()

        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        rtt_ms = int((time.monotonic() - t0) * 1000)

        text = raw.decode("utf-8", errors="replace")
        msg = decode_line(text)
        if msg is None:
            return {"status": "error", "reason": "malformed response"}
        if msg.get("type") != "pong":
            return {"status": "error", "reason": f"unexpected {msg.get('type')}"}
        return {"status": "ok", "rtt_ms": rtt_ms}
    except asyncio.TimeoutError:
        return {"status": "error", "reason": f"timeout after {int(timeout)}s"}
    except ConnectionRefusedError:
        return {"status": "error", "reason": "connection refused"}
    except (OSError, socket.gaierror):
        return {"status": "error", "reason": "unreachable"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
