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
import math
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
CONF_ENABLE_PASSTHROUGH = "enable_app_passthrough"
CONF_PASSTHROUGH_PORT = "passthrough_port"
# Tank geometry / calibration (level is derived from the raw ullage reading)
CONF_ORIENTATION = "orientation"
CONF_TANK_HEIGHT = "tank_height_inches"
CONF_RAW_DIVISOR = "raw_divisor"
CONF_GAL_PER_INCH = "gallons_per_inch"

ORIENTATION_VERTICAL = "vertical"
ORIENTATION_HORIZONTAL = "horizontal"

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
DEFAULT_ENABLE_PASSTHROUGH = False  # raw TCP relay on 443 so the phone app works
DEFAULT_PASSTHROUGH_PORT = 443

# Geometry / calibration defaults (standard 275 gal vertical tank).
DEFAULT_ORIENTATION = ORIENTATION_VERTICAL
DEFAULT_TANK_HEIGHT = 44.0  # inches the ullage sensor spans (height; diameter if horizontal)
DEFAULT_RAW_DIVISOR = 254.0  # raw is tenths-of-mm; inches = raw / 254
DEFAULT_GAL_PER_INCH = 5.9  # vertical slope; matches the app (16.4 in -> 96.8 gal)

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


def parse_frame(raw: bytes) -> dict[str, Any] | None:
    """Parse one telemetry frame into its raw fields.

    Returns None when the payload does not match the expected shape, so the
    caller can still ACK (the device must always be acknowledged) without
    publishing garbage. Level/volume are NOT computed here — the coordinator
    derives them from the raw ullage and the configured tank geometry, because
    the device's own percentage field does not map linearly to fill.

    Frame: ``<id>_<fw>_<devpct>_<tempC>_<ullage_raw>_<rssi>_<flag>``. The
    ``ullage_raw`` field is the air gap (top of tank to oil surface) in tenths
    of a millimetre, e.g. ``7010`` -> 701.0 mm -> 27.6 in.
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

    device_id, fw, device_pct, temp, ullage_raw, rssi, flag = parts[:FRAME_FIELDS]

    return {
        "device_id": device_id,
        "version": fw,
        "device_pct": _num(device_pct),
        "ullage_raw": _num(ullage_raw),
        "temp_c": _num(temp),
        "rssi": _num(rssi),
        "flag": _num(flag),
    }


def horizontal_gallons(
    fill_height_in: float, diameter_in: float, rated_gallons: float
) -> float:
    """Volume of a round horizontal cylinder filled to ``fill_height_in``.

    Uses the circular-segment area fraction, scaled to the rated capacity.
    """
    if diameter_in <= 0:
        return 0.0
    r = diameter_in / 2.0
    h = max(0.0, min(fill_height_in, diameter_in))
    rh = r - h
    ratio = max(-1.0, min(1.0, rh / r))
    segment = r * r * math.acos(ratio) - rh * math.sqrt(max(0.0, 2 * r * h - h * h))
    fraction = segment / (math.pi * r * r)
    return fraction * rated_gallons


def compute_levels(
    raw_fields: dict[str, Any],
    *,
    orientation: str,
    tank_height_in: float,
    raw_divisor: float,
    gal_per_inch: float,
    rated_gallons: float,
) -> dict[str, Any]:
    """Derive air/oil height, gallons, litres and fill % from the raw ullage."""
    out = dict(raw_fields)
    try:
        ullage = float(raw_fields.get("ullage_raw"))
    except (TypeError, ValueError):
        ullage = None

    if ullage is None or raw_divisor <= 0 or tank_height_in <= 0:
        out.update(
            {"air_in": None, "oil_in": None, "gallons": None,
             "liters": None, "level": None}
        )
        return out

    air_in = ullage / raw_divisor
    oil_in = max(0.0, min(tank_height_in, tank_height_in - air_in))

    if orientation == ORIENTATION_HORIZONTAL:
        gallons = horizontal_gallons(oil_in, tank_height_in, rated_gallons)
    else:
        slope = gal_per_inch if gal_per_inch and gal_per_inch > 0 else (
            rated_gallons / tank_height_in
        )
        gallons = oil_in * slope

    gallons = max(0.0, gallons)
    level = (gallons / rated_gallons * 100.0) if rated_gallons > 0 else 0.0

    out.update(
        {
            "air_in": round(air_in, 1),
            "oil_in": round(oil_in, 1),
            "gallons": round(gallons, 1),
            "liters": round(gallons * LITERS_PER_GALLON, 1),
            "level": round(max(0.0, min(100.0, level)), 1),
        }
    )
    return out
