"""Asyncio TCP listener for the T-Meter oil-tank protocol.

Accepts the device's connection on the configured port, replies with the
mandatory ACK so the device stops retransmitting and sleeps, optionally relays
the frame to the original cloud (so the vendor phone app keeps working), and
hands the raw bytes to a callback for parsing/publishing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant

from .const import ACK

_LOGGER = logging.getLogger(__name__)

# Hold a connection open this long waiting for the next frame before closing.
IDLE_TIMEOUT = 60
CLOUD_TIMEOUT = 5


class TMeterServer:
    """A small TCP server that ingests one device protocol."""

    def __init__(
        self,
        hass: HomeAssistant,
        on_frame: Callable[[bytes, str], Awaitable[None]],
        *,
        host: str,
        port: int,
        forward_cloud: bool = False,
        cloud_host: str | None = None,
        cloud_port: int | None = None,
    ) -> None:
        self._hass = hass
        self._on_frame = on_frame
        self._host = host
        self._port = port
        self._forward_cloud = forward_cloud
        self._cloud_host = cloud_host
        self._cloud_port = cloud_port
        self._server: asyncio.AbstractServer | None = None

    async def async_start(self) -> None:
        """Bind and start serving. Raises OSError if the port is unavailable."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port, reuse_address=True
        )
        sockets = ", ".join(
            str(s.getsockname()) for s in (self._server.sockets or [])
        )
        _LOGGER.info("T-Meter listener started on %s", sockets)

    async def async_stop(self) -> None:
        """Stop serving and release the port."""
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover - best effort
                pass
            self._server = None
            _LOGGER.debug("T-Meter listener stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        addr = peer[0] if peer else "?"
        _LOGGER.debug("Connection from %s", addr)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(256), IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    break
                if not data:
                    break

                # ALWAYS acknowledge first — the device retransmits until it
                # receives this and will not sleep otherwise.
                try:
                    writer.write(ACK)
                    await writer.drain()
                except (ConnectionError, OSError):
                    break

                if self._forward_cloud:
                    self._hass.async_create_background_task(
                        self._forward(data), "tmeter_oil_cloud_forward"
                    )

                try:
                    await self._on_frame(data, addr)
                except Exception:  # pragma: no cover - never kill the socket
                    _LOGGER.exception("Error handling T-Meter frame from %s", addr)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass

    async def _forward(self, data: bytes) -> None:
        """Relay a frame to the real cloud so the vendor app keeps updating."""
        if not self._cloud_host or not self._cloud_port:
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self._cloud_host, self._cloud_port),
                CLOUD_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Cloud forward connect failed: %s", err)
            return
        try:
            up_writer.write(data)
            await up_writer.drain()
            try:
                # Drain the cloud's ACK; we don't need it.
                await asyncio.wait_for(up_reader.read(64), 3)
            except asyncio.TimeoutError:
                pass
        except (OSError, ConnectionError) as err:  # pragma: no cover
            _LOGGER.debug("Cloud forward write failed: %s", err)
        finally:
            try:
                up_writer.close()
                await up_writer.wait_closed()
            except Exception:  # pragma: no cover
                pass
