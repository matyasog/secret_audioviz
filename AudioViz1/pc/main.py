#!/usr/bin/env python3
"""
main.py  —  PC Node
Captures system audio via WASAPI loopback, analyses frequency bands,
polls Spotify for the current track, and publishes a combined JSON
payload to the local MQTT broker on the Raspberry Pi at 30 fps.

Run from the pc/ directory:
    python main.py
"""
import os
import json
import time
import signal
import sys
import threading

import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# ── Local imports ─────────────────────────────────────────────────────
from band_analyzer import BandAnalyzer
from spotify_poller import SpotifyPoller

# ── Config ────────────────────────────────────────────────────────────
MQTT_BROKER  = os.getenv("MQTT_BROKER",  "192.168.0.165")
MQTT_PORT    = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC   = os.getenv("MQTT_TOPIC",   "audio/bands/matvitt")
SAMPLE_RATE  = 44100
BLOCK_SIZE   = 2048          # ~46 ms per block  →  ~21 audio callbacks/s
TARGET_FPS   = 30

# ── Shared state ──────────────────────────────────────────────────────
_bands      : dict[str, bool] = {"low": False, "mid": False, "high": False}
_bands_lock = threading.Lock()
_analyzer   = BandAnalyzer(sample_rate=SAMPLE_RATE)
_spotify    = SpotifyPoller(poll_interval=2.0)

# ── Audio callback (runs in PortAudio thread – keep it fast) ──────────
def _audio_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
    if status:
        print(f"[Audio] PortAudio status: {status}", flush=True)
    mono = indata.mean(axis=1)          # stereo → mono
    bands = _analyzer.analyze(mono)
    
    if any(bands.values()):
        active = [k.upper() for k, v in bands.items() if v]
        print(f"[Audio] Triggers: {' | '.join(active)}", flush=True)
        
    with _bands_lock:
        _bands.update(bands)


def _find_loopback() -> tuple[int | None, sd.WasapiSettings | None]:
    """
    Try to find a dedicated loopback or Stereo Mix device by name.
    """
    # Prefer WASAPI Stereo Mix or Loopback
    for idx, dev in enumerate(sd.query_devices()):
        name = dev["name"].lower()
        hostapi = sd.query_hostapis(dev["hostapi"])["name"].lower()
        if dev["max_input_channels"] > 0 and ("loopback" in name or "stereo mix" in name):
            if "wasapi" in hostapi:
                print(f"[Audio] Found loopback device [{idx}]: {dev['name']} ({hostapi})")
                return idx, None

    # Fallback to any stereo mix
    for idx, dev in enumerate(sd.query_devices()):
        name = dev["name"].lower()
        if dev["max_input_channels"] > 0 and ("loopback" in name or "stereo mix" in name):
            print(f"[Audio] Found fallback device [{idx}]: {dev['name']}")
            return idx, None

    print("[Audio] No loopback or Stereo Mix device found! Defaulting to system default input.")
    return None, None


# ── MQTT ─────────────────────────────────────────────────────────────
_mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def _on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT}")
    else:
        print(f"[MQTT] Connection failed (reason {reason_code})")

def _on_disconnect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[MQTT] Unexpected disconnect ({reason_code}), will auto-reconnect…")

_mqttc.on_connect    = _on_connect
_mqttc.on_disconnect = _on_disconnect
_mqttc.reconnect_delay_set(min_delay=1, max_delay=10)


# ── Publish loop (main thread) ────────────────────────────────────────
def _publish_loop() -> None:
    interval = 1.0 / TARGET_FPS
    while True:
        t0 = time.perf_counter()

        with _bands_lock:
            payload = dict(_bands)
        payload["spotify"] = _spotify.get_current()

        _mqttc.publish(MQTT_TOPIC, json.dumps(payload), qos=0)

        elapsed = time.perf_counter() - t0
        sleep_s = interval - elapsed
        if sleep_s > 0:
            time.sleep(sleep_s)


# ── Graceful shutdown ─────────────────────────────────────────────────
def _shutdown(sig, frame) -> None:
    print("\n[Main] Shutting down…")
    _spotify.stop()
    _mqttc.loop_stop()
    _mqttc.disconnect()
    sys.exit(0)

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


# ── Entry point ───────────────────────────────────────────────────────
def main() -> None:
    # 1. Start Spotify poller (OAuth prompt happens here on first run)
    print("[Spotify] Starting poller (browser will open for auth if first run)…")
    _spotify.start()

    # 2. Connect MQTT
    _mqttc.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    _mqttc.loop_start()

    # 3. Open WASAPI loopback stream
    device_idx, wasapi_extra = _find_loopback()

    stream_kwargs: dict = dict(
        channels    = 2,
        samplerate  = SAMPLE_RATE,
        blocksize   = BLOCK_SIZE,
        callback    = _audio_callback,
        dtype       = "float32",
    )
    if device_idx is not None:
        stream_kwargs["device"] = device_idx
        
        # WASAPI requires using the exact native sample rate of the device
        dev_info = sd.query_devices(device_idx)
        native_sr = int(dev_info["default_samplerate"])
        if native_sr > 0:
            stream_kwargs["samplerate"] = native_sr
            _analyzer.sample_rate = native_sr
            print(f"[Main] Using native sample rate: {native_sr} Hz")
            
    if wasapi_extra is not None:
        stream_kwargs["extra_settings"] = wasapi_extra

    print(f"[Main] Publishing to mqtt://{MQTT_BROKER}:{MQTT_PORT}/{MQTT_TOPIC} at {TARGET_FPS} fps")
    print("[Main] Press Ctrl+C to stop.\n")

    with sd.InputStream(**stream_kwargs):
        _publish_loop()      # blocks forever


if __name__ == "__main__":
    main()
