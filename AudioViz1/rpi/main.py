#!/usr/bin/env python3
"""
main.py  —  RPi Node
MQTT subscriber that feeds band triggers to the LED strip
and album-cover URLs to the HUB75 matrix.

Must be run with sudo so rpi_ws281x and rgbmatrix can access
the DMA / GPIO hardware:

    cd ~/AudioViz1/rpi
    sudo .venv/bin/python main.py
"""
# ── SSL fix FIRST — before any other import that might touch the network ──
import os
os.environ["REQUESTS_CA_BUNDLE"] = "/etc/ssl/certs/ca-certificates.crt"
os.environ["CURL_CA_BUNDLE"]     = "/etc/ssl/certs/ca-certificates.crt"

import json
import signal
import sys
import time

import paho.mqtt.client as mqtt

from led_strip  import LEDStrip
from led_matrix import LEDMatrix

# ── Config loading ────────────────────────────────────────────────────
_CFG_PATH  = os.path.join(os.path.dirname(__file__), "config.json")
_cfg_mtime : float | None = None
cfg        : dict         = {}


def _load_cfg(force: bool = False) -> None:
    """
    Hot-reload config.json whenever its mtime changes.
    Silently keeps the old config if a re-read fails
    (e.g. permission error mid-write).
    """
    global _cfg_mtime
    try:
        mtime = os.path.getmtime(_CFG_PATH)
        if not force and mtime == _cfg_mtime:
            return
        with open(_CFG_PATH) as fh:
            cfg.update(json.load(fh))
        _cfg_mtime = mtime
        print(f"[Config] Loaded {_CFG_PATH}")
    except Exception as exc:
        if _cfg_mtime is not None:
            # Already loaded once — silently ignore reload failures
            return
        print(f"[Config] FATAL: cannot load config: {exc}")
        sys.exit(1)


_load_cfg(force=True)

# ── MQTT settings ─────────────────────────────────────────────────────
MQTT_BROKER = "127.0.0.1"           # local Mosquitto on this Pi
MQTT_PORT   = 1883
MQTT_TOPIC  = "audio/bands/matvitt"

# ── Hardware init ─────────────────────────────────────────────────────
strip  = LEDStrip(cfg)
matrix = LEDMatrix(cfg)

# ── MQTT callbacks ────────────────────────────────────────────────────
def _on_connect(client, userdata, flags, reason_code, properties) -> None:
    if reason_code == 0:
        client.subscribe(MQTT_TOPIC, qos=0)
        print(f"[MQTT] Connected and subscribed to '{MQTT_TOPIC}'")
    else:
        print(f"[MQTT] Connection failed — reason code {reason_code}")


def _on_disconnect(client, userdata, flags, reason_code, properties) -> None:
    if reason_code != 0:
        print(f"[MQTT] Unexpected disconnect ({reason_code}), reconnecting…")


def _on_message(client, userdata, msg) -> None:
    try:
        data = json.loads(msg.payload)
    except Exception as exc:
        print(f"[MQTT] Bad payload: {exc}")
        return

    # Config hot-reload check (cheap mtime poll, ~0 overhead)
    _load_cfg()

    strip.trigger(
        low  = bool(data.get("low",  False)),
        mid  = bool(data.get("mid",  False)),
        high = bool(data.get("high", False)),
    )
    matrix.update(data.get("spotify", {}))


# ── Build and connect MQTT client ────────────────────────────────────
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.on_connect    = _on_connect
mqttc.on_disconnect = _on_disconnect
mqttc.on_message    = _on_message
mqttc.reconnect_delay_set(min_delay=1, max_delay=10)
mqttc.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqttc.loop_start()

# ── Render loop ───────────────────────────────────────────────────────
TARGET_FPS     = 30
FRAME_INTERVAL = 1.0 / TARGET_FPS


def _shutdown(sig, frame) -> None:
    print("\n[Main] Shutting down…")
    mqttc.loop_stop()
    mqttc.disconnect()
    strip.clear()
    matrix.clear()
    sys.exit(0)


signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

print(f"[Main] Render loop running at {TARGET_FPS} fps — press Ctrl+C to stop")

while True:
    t0 = time.perf_counter()
    strip.render()
    elapsed = time.perf_counter() - t0
    sleep_s = FRAME_INTERVAL - elapsed
    if sleep_s > 0:
        time.sleep(sleep_s)
