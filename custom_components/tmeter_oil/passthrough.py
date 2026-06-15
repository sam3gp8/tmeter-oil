"""Transparent TCP passthrough.

Forwards a local port straight through to the vendor cloud without terminating
TLS, so the phone app's HTTPS requests (which DNS now sends to Home Assistant)
reach the real cloud with a valid certificate. Pure byte pump — no parsing, no
decryption.

This lets the device redirect (DNS -> Home Assistant) coexist with a working
app: the device talks to the integration on its own port, while the app's
443 traffic is relayed here to the cloud unchanged.
"""

from __future__ import annotations

import asyncio
import logging

_LOGGER = logging.getLogger(__name__)


async def _pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # pragma: no cover
            pass


class TMeterPassthrough:
    """Relay listen_host:listen_port <-> target_host:target_port (raw TCP)."""

    def __init__(
        self,
        *,
        listen_host: str,
        listen_port: int,
        target_host: str,
        target_port: int,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._target_host = target_host
        self._target_port = target_port
        self._server: asyncio.AbstractServer | None = None

    async def async_start(self) -> None:
        """Bind and serve. Raises OSError if the port can't be bound."""
        self._server = await asyncio.start_server(
            self._handle, self._listen_host, self._listen_port, reuse_address=True
        )
        _LOGGER.info(
            "App passthrough %s:%d -> %s:%d",
            self._listen_host, self._listen_port,
            self._target_host, self._target_port,
        )

    async def async_stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover
                pass
            self._server = None

    async def _handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self._target_host, self._target_port), 10
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Passthrough upstream connect failed: %s", err)
            try:
                client_writer.close()
            except Exception:  # pragma: no cover
                pass
            return

        # Pump both directions until either side closes.
        await asyncio.gather(
            _pipe(client_reader, up_writer),
            _pipe(up_reader, client_writer),
            return_exceptions=True,
        )
