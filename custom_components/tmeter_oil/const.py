"""Constants and frame parsing for the T-Meter Oil Tank (Local) integration.

The oil-tank sensor (an ESP32-class device) opens a raw TCP socket to its
cloud on port 9678 and sends, ~once per second until acknowledged, a single
underscore-delimited ASCII telemetry frame:

    <device_id>_<fw>_<level%>_<tempC>_<raw>_<rssi>_<flag>
    e.g.  58cf791f02b5_02.00.00_79_23_6960_-44_2

The cloud replies with the 4 bytes  b"00\\r\\n"  to acknowledge receipt; the
device then stops retransmitting and returns to deep sleep until its next
reporting interval. This integration listens on that same port, sends the same
ACK, parses the frame, and exposes the values as native Home Assistant
entities. Volume is derived locally from a user-supplied tank capacity.

No cloud, no TLS, no firmware modification. Point the device at Home Assistant
by adding a local DNS rewrite for the device's cloud hostname.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

DOMAIN = "tmeter_oil"

# Bytes the device expects back to consider the report delivered.
ACK = b"00\r\n"

# Number of underscore-delimited fields in a telemetry frame.
FRAME_FIELDS = 7

# Event fired on the HA bus when a delivery/refill is detected.
EVENT_REFILL = "tmeter_oil_refill"

# ---------------------------------------------------------------------------
# Config / options keys
# ---------------------------------------------------------------------------
CONF_PORT = "port"
CONF_HOST = "host"
CONF_TANK_GALLONS = "tank_gallons"
CONF_KWH_PER_GALLON = "kwh_per_gallon"
CONF_FORWARD_CLOUD = "forward_cloud"
CONF_CLOUD_HOST = "cloud_host"
CONF_CLOUD_PORT = "cloud_port"
CONF_OFFLINE_AFTER = "offline_after_minutes"
CONF_REFILL_THRESHOLD = "refill_threshold_gallons"
CONF_INVERT_LEVEL = "invert_level"
CONF_ENABLE_PASSTHROUGH = "enable_app_passthrough"
CONF_PASSTHROUGH_PORT = "passthrough_port"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9678
DEFAULT_TANK_GALLONS = 275.0  # standard residential heating-oil tank
DEFAULT_KWH_PER_GALLON = 40.6  # No. 2 heating oil, ~138,500 BTU/US gal
DEFAULT_FORWARD_CLOUD = False
DEFAULT_CLOUD_HOST = "45.77.120.52"
DEFAULT_CLOUD_PORT = 9678
DEFAULT_OFFLINE_AFTER = 0  # minutes; 0 = device stays "available" once seen
DEFAULT_REFILL_THRESHOLD = 10.0  # gallons; level rises >= this fire a refill event
DEFAULT_INVERT_LEVEL = True  # device reports ullage (empty %); fill = 100 - value
DEFAULT_ENABLE_PASSTHROUGH = False  # raw TCP relay on 443 so the phone app works
DEFAULT_PASSTHROUGH_PORT = 443

LITERS_PER_GALLON = 3.785411784

# Consumption odometer noise floor (gallons). Drops smaller than this are
# treated as jitter and not counted.
CONSUMPTION_NOISE_FLOOR_GAL = 0.05

ATTRIBUTION = "Local data from the T-Meter oil-tank sensor"

# Dispatcher signal fired when a new device id is first seen on the listener.
SIGNAL_NEW_DEVICE = "tmeter_oil_new_device_{entry_id}"


def _num(value: str) -> Any:
    """Parse a numeric field, preserving int vs float, else return the string."""
    try:
        return float(value) if "." in value else int(value)
    except (TypeError, ValueError):
        return value


def parse_frame(
    raw: bytes, tank_gallons: float, invert_level: bool = False
) -> dict[str, Any] | None:
    """Parse one telemetry frame into a value dict.

    Returns None when the payload does not match the expected shape, so the
    caller can still ACK (the device must always be acknowledged) without
    publishing garbage.

    The device transmits the *ullage* (empty) percentage on this field; with
    ``invert_level`` (the default) it is converted to fill percentage as
    ``100 - value``, matching what the vendor app displays.
    """
    try:
        text = raw.decode("ascii", "ignore").strip().strip("\x00").strip()
    except Exception:  # pragma: no cover - decode is defensive
        return None
    if not text:
        return None

    parts = text.split("_")
    if len(parts) < FRAME_FIELDS:
        _LOGGER.debug("Ignoring short frame (%d fields): %r", len(parts), text)
        return None

    device_id, fw, level, temp, raw_dist, rssi, flag = parts[:FRAME_FIELDS]

    try:
        raw_level = float(level)
    except ValueError:
        _LOGGER.debug("Frame has non-numeric level: %r", text)
        return None
    raw_level = max(0.0, min(100.0, raw_level))

    fill_pct = 100.0 - raw_level if invert_level else raw_level
    fill_pct = max(0.0, min(100.0, fill_pct))

    gallons = round(fill_pct / 100.0 * tank_gallons, 1)
    liters = round(gallons * LITERS_PER_GALLON, 1)

    return {
        "device_id": device_id,
        "version": fw,
        "level": round(fill_pct, 1),
        "level_raw": raw_level,
        "gallons": gallons,
        "liters": liters,
        "temp_c": _num(temp),
        "raw": _num(raw_dist),
        "rssi": _num(rssi),
        "flag": _num(flag),
    }
