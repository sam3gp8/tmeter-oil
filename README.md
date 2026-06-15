# T-Meter Oil Tank (Local) — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00.svg)](https://www.buymeacoffee.com/sam3gp8)

A **fully local** Home Assistant integration for **T-Meter** heating-oil tank
sensors (the `com.dayan.tank` app, sold as *T-Meter*). It listens for the
sensor's own network reports, replaces the vendor cloud entirely, and creates
native entities — including a cumulative **energy sensor for the Energy
Dashboard**.

No cloud account at runtime. No TLS interception. No firmware flashing. The
device keeps its stock firmware; you simply redirect its traffic to Home
Assistant with a one-line DNS rewrite.

> Unofficial, community-built, and not affiliated with or endorsed by the
> device or app vendor. Built by reverse engineering the device's own wire
> protocol for interoperability.

---

## How it works

The tank sensor (an ESP32-class device) wakes on its reporting interval, opens
a plain **TCP socket to its cloud on port 9678**, and sends a single ASCII
telemetry frame. The cloud replies with a 4-byte acknowledgement, and the
device goes back to deep sleep.

This integration stands up a tiny TCP listener inside Home Assistant that
speaks the same protocol: it sends the same ACK (so the device sleeps normally
and doesn't drain its battery retransmitting), parses the frame, and publishes
the values. Because Home Assistant OS runs the Core container on the host
network, the listener is reachable on your HA host's LAN address — the device
talks straight to it once DNS points the way.

### The protocol (for the curious)

Each report is one underscore-delimited frame:

```
<device_id>_<fw>_<level%>_<tempC>_<raw>_<rssi>_<flag>
58cf791f02b5_02.00.00_79_23_6960_-44_2
```

| Field | Example | Meaning |
|---|---|---|
| device_id | `58cf791f02b5` | Device id (its MAC, no colons) |
| fw | `02.00.00` | Firmware/protocol version |
| device % | `79` | Device's own percentage field — **non-linear, not used** |
| tempC | `23` | Sensor-head temperature, °C |
| ullage_raw | `7010` | **Air gap, tenths of a mm** (701.0 mm = 27.6 in) — the real reading |
| rssi | `-44` | Wi-Fi signal, dBm |
| flag | `2` | Status flag (exposed as a diagnostic) |

The server replies with the bytes `30 30 0d 0a` (`"00\r\n"`) to acknowledge.
Fill is derived from the ullage and tank geometry — see *How the level is
computed* below.

## Entities (per tank)

| Entity | Unit | Notes |
|---|---|---|
| Level | % | `gallons / rated`, matches the app |
| Remaining | gal | Computed from ullage + geometry |
| Oil height | in | Oil depth (matches the app's "Oil height") |
| Temperature | °C | Auto-displays °F on US systems |
| **Energy consumed** | **kWh** | **Cumulative — for the Energy Dashboard** |
| **Oil consumed** | gal | Cumulative volume — optional Water-section sensor |
| Air height | in | Ullage / air gap (diagnostic) |
| Signal strength | dBm | Diagnostic |
| Last report | timestamp | Diagnostic |
| Connectivity | on/off | Diagnostic (see *Offline detection*) |
| Remaining (litres) | L | Diagnostic, disabled by default |
| Device percentage | % | The device's own (non-linear) field; diagnostic, disabled |
| Raw ullage / Status flag | — | Diagnostic, disabled by default |
| Reset consumption | button | Zeroes the consumed/energy odometers |

A Home Assistant device is created automatically the first time each tank
reports; multiple tanks on the same network are supported by the one listener.

## Requirements

- Home Assistant **2024.4** or newer (HAOS / Supervised recommended, so the
  listener is reachable on the host network)
- A local DNS server you can add a rewrite to (the AdGuard Home or Pi-hole
  add-on, or your router)
- The tank sensor on the same LAN, using that DNS server

## Installation (HACS)

1. HACS → **⋮** → *Custom repositories*.
2. Add `https://github.com/sam3gp8/tmeter-oil`, category **Integration**.
3. Install **T-Meter Oil Tank (Local)**, then **restart Home Assistant**.

Manual install: copy `custom_components/tmeter_oil/` into your HA config folder
(so you have `config/custom_components/tmeter_oil/`) and restart.

Or use the one-click link (opens the dialog pre-filled):

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=sam3gp8&repository=tmeter-oil&category=integration)

## Setup

### 1. Add the integration

**Settings → Devices & Services → Add Integration → "T-Meter Oil Tank (Local)"**

- **Listen port** — `9678` (default; the port the sensor uses).
- **Tank capacity (gallons)** — e.g. `275`. Used to turn the reported
  percentage into gallons.
- **Energy content (kWh per gallon)** — `40.6` for No. 2 heating oil.
- **Also forward to the vendor cloud** — leave **off** to be fully cloud-free,
  or turn **on** to keep the phone app working (see below).

If the port is already in use you'll get an error; pick another and match it on
the device side isn't possible (the port is fixed in firmware), so free 9678
instead.

### 2. Redirect the sensor to Home Assistant

Point the device's cloud hostname at your Home Assistant host with a DNS
rewrite. In the **AdGuard Home** add-on: *Filters → DNS rewrites → Add*:

```
Domain:  csb.tumblevd.com
Answer:  <your Home Assistant host IP>
```

That's the whole redirect — because the device already connects on port 9678
and the integration listens on 9678, **no port forwarding or firewall rule is
needed**. Make sure your devices actually use this DNS server as their
resolver. (DNS-forwarding routers such as Nest Wifi still answer the rewrite
correctly; they only mask which client asked, which doesn't matter here.)

Then power-cycle the tank so it re-resolves the hostname and reports to Home
Assistant. The device appears within one reporting cycle (force one with the
device's report button if it has one).

## Energy Dashboard

Heating oil isn't a native Energy Dashboard source, so the integration provides
a kWh sensor the dashboard understands.

**Recommended — track it as a device:** Settings → Dashboards → Energy → *Add a
device* (under *Individual devices*) → pick `sensor.<tank>_energy_consumed`.

**Alternative — as a gas source:** when adding a gas source you can select the
same kWh sensor; the dashboard accepts energy-based gas sources.

**Volume too (optional):** add `sensor.<tank>_oil_consumed` (gallons) under the
**Water** section. It uses the `water` device class so the dashboard accepts
it; it tracks oil volume rather than water.

Consumption is computed by watching the level fall between reports: drops add to
the odometer, a large rise is treated as a refill and only resets the baseline.
The running total is persisted and survives restarts, so the Energy Dashboard
never sees a reset to zero.

## Options

Use the integration's **Configure** button to change:

- **Tank capacity** and **kWh per gallon**.
- **Forward to the vendor cloud** (+ cloud host/port) — relays each report
  upstream so the official app keeps updating alongside local control.
- **Mark offline after (minutes)** — `0` keeps the device "connected" once
  seen (sensible, since it deep-sleeps between reports). Set this to a bit more
  than your observed reporting interval to get a real connectivity sensor.
- **Tank orientation** — `vertical` (upright) or `horizontal` (on its side).
  Determines how oil height converts to volume. See *How the level is computed*.
- **Tank height / diameter (inches)** — the top-to-bottom distance the sensor
  spans: height for a vertical tank (e.g. 44), diameter for a horizontal one.
- **Gallons per inch** (vertical) and **Raw-to-inch divisor** — calibration; see
  below.

## How the level is computed

The sensor measures **ullage** — the air gap from the top of the tank to the oil
surface — and transmits it raw (in tenths of a millimetre). The integration does
*not* use the device's own percentage field (it isn't linear with fill). Instead
it computes everything from the ullage and your tank geometry, the same way the
vendor cloud does:

```
air_height  = raw / 254              (inches; 254 = tenths-of-mm per inch)
oil_height  = tank_height - air_height
vertical:   gallons = oil_height × gallons_per_inch
horizontal: gallons = round-cylinder segment volume (by oil_height & diameter)
level %     = gallons / rated_gallons × 100
```

This reproduces the app exactly. For a standard 275-gal vertical tank the
defaults (height 44 in, 5.9 gal/in, divisor 254) match the app and gauge to a
fraction of a gallon. Sensors for **Oil height** and **Air height** are exposed
so you can compare directly against the app's own readout.

**Calibrating gallons-per-inch (vertical):** open the device's detail screen in
the vendor app, read its gallons and oil-height inches, and set **Gallons per
inch** = gallons ÷ oil-height. (Setting it to `0` derives it from rated capacity
÷ tank height instead.) The disabled **Device percentage** and **Raw ullage**
sensors expose exactly what the device transmits, for verification.

A **Reset consumption** button is provided per tank (under Configuration) to
zero the *Oil consumed* / *Energy consumed* odometers — useful after changing the
tank geometry, which makes earlier accumulated totals meaningless.

## Delivery / refill detection

When the tank level jumps up by at least the delivery threshold, the
integration fires a `tmeter_oil_refill` event on the Home Assistant bus with:

| Field | Example | Meaning |
|---|---|---|
| `device_id` | `a1b2…` | Home Assistant device id (for device triggers) |
| `tank_id` | `58cf791f02b5` | The tank's own id |
| `gallons_added` | `182.4` | Estimated delivery size |
| `liters_added` | `690.5` | Same, in litres |
| `level` | `95` | Fill % after delivery |
| `gallons` | `261.3` | Remaining gallons after delivery |

(The consumption odometer already ignores the rise so a delivery never counts
as negative usage; this event simply lets you *react* to it.)

**Easiest — import the blueprint.** In Home Assistant: Settings → Automations &
Scenes → Blueprints → *Import Blueprint*, and paste:

```
https://github.com/sam3gp8/tmeter-oil/blob/main/blueprints/automation/sam3gp8/tmeter_oil_refill.yaml
```

Then create an automation from it, pick your tank and a notify service.

**Or with the UI:** New Automation → Add Trigger → *Device* → pick your oil
tank → **"Oil delivery detected."**

**Or in YAML:**

```yaml
automation:
  - alias: Oil delivery notification
    trigger:
      - platform: event
        event_type: tmeter_oil_refill
    action:
      - service: notify.persistent_notification
        data:
          title: Oil delivery detected
          message: >
            +{{ trigger.event.data.gallons_added }} gal delivered.
            Tank now {{ trigger.event.data.level }}%.
```

## Keeping the phone app working

Because your DNS rewrite sends the sensor's hostname to Home Assistant, the
vendor app — which talks to the *same* hostname over HTTPS — also gets pointed
here and can no longer reach the cloud. To keep the app working **and** stay on
one host, the integration can run a **transparent 443 passthrough**: it forwards
HTTPS straight through to the real cloud without touching the encryption, so the
certificate stays valid and the app behaves normally.

For a fully working app alongside local monitoring, enable **both**:

1. **Relay port 443 to the vendor cloud** (this passthrough), and
2. **Also forward to the vendor cloud** (so the cloud keeps receiving the
   sensor's readings that the app then displays).

The result: the sensor talks to Home Assistant on its own port; the app's 443
traffic is relayed to the cloud unchanged; the cloud stays fed by the forwarder.
Everything runs on the Home Assistant host, so it doesn't matter that your
router can't distinguish clients for DNS.

> The passthrough needs **port 443 free** on the Home Assistant host (it is, by
> default — HA serves on 8123). On Home Assistant OS the binding works because
> Core runs privileged. If 443 can't be bound, the integration logs a warning
> and keeps running; only the app relay is affected, not the tank.

If you don't care about the app, leave both off and you're fully cloud-free.

## Troubleshooting

- **No device appears.** Confirm the DNS rewrite resolves on the tank's network
  (`nslookup csb.tumblevd.com` from a device on the same DNS → your HA IP), that
  the tank uses that DNS server, and that you power-cycled it. The sensor may
  only report every few hours; use its report button to force one.
- **Device keeps reconnecting / never sleeps.** It isn't getting the ACK —
  verify the integration is running and listening on the right port (check the
  HA log for "T-Meter listener started").
- **Readings look like zero.** A freshly reset or offline sensor reports 0%
  until it takes a real measurement; check the device is actually online.
- **Wrong gallons.** Set the correct **Tank capacity** in options.
- **Port in use.** Something else is bound to 9678 on the HA host; stop it (a
  leftover debug listener is the usual culprit).

## Notes

- Field meanings (`raw`, `flag`) were inferred from observed traffic and may
  vary by firmware; they're exposed as diagnostics so you can confirm against
  your own device.
- This talks to the device on your LAN only; nothing leaves your network unless
  you enable cloud forwarding.

## Support

If this saved you from a cloud dependency, you can
[buy me a coffee](https://www.buymeacoffee.com/sam3gp8). ☕

## License

[MIT](LICENSE) © sam3gp8
