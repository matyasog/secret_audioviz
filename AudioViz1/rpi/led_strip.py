"""
led_strip.py  —  RPi Node
Controls the WS2812B LED strip via rpi_ws281x.

Three additive effect layers triggered by MQTT band messages:
  LOW  → Red pulse expanding from the centre of the strip
  MID  → Green wave travelling left-to-right
  HIGH → Random white sparkles that decay
"""
import time
import random
import threading
import numpy as np

try:
    from rpi_ws281x import PixelStrip, Color
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    print("[Strip] rpi_ws281x not found — running in mock mode")


class LEDStrip:
    def __init__(self, cfg: dict) -> None:
        self._N       = cfg.get("num_leds", 30)
        self._enabled = cfg.get("ws281_enabled", True)

        # ── Initialise hardware ─────────────────────────────────────
        if _HW_AVAILABLE and self._enabled:
            self._strip = PixelStrip(
                self._N,
                cfg.get("ws281_gpio",     19),
                cfg.get("ws281_freq_hz",  800000),
                cfg.get("ws281_dma",      10),
                cfg.get("ws281_invert",   False),
                cfg.get("ws281_brightness", 255),
                cfg.get("ws281_channel",  1),
            )
            self._strip.begin()
        else:
            self._strip = None

        # ── Effect parameters ────────────────────────────────────────
        self._pulse_decay_s  = cfg.get("pulse_decay_ms",       120) / 1000.0
        self._wave_speed_spl = cfg.get("wave_speed_ms_per_led",  8) / 1000.0  # s per LED
        self._sparkle_density= cfg.get("sparkle_density",      0.15)

        # ── Effect state (protected by _lock) ────────────────────────
        self._lock = threading.Lock()

        # Low — pulse
        self._pulse_t0: float | None = None          # None = inactive

        # Mid — wave
        self._wave_t0:  float | None = None          # None = inactive

        # High — sparkles  {led_index: brightness 0..1}
        self._sparkles: dict[int, float] = {}
        self._sparkle_on = False

        self._last_render = time.perf_counter()

    # ── Public API ───────────────────────────────────────────────────

    def trigger(self, low: bool, mid: bool, high: bool) -> None:
        """Called from the MQTT callback thread."""
        now = time.perf_counter()
        with self._lock:
            if low:
                self._pulse_t0 = now          # restart pulse

            if mid:
                # Restart wave only if it has finished or not started
                if self._wave_t0 is None:
                    self._wave_t0 = now
                # (if wave is already running we let it finish naturally)

            if high:
                self._sparkle_on = True

    def render(self) -> None:
        """
        Called from the main render loop at ~30 fps.
        Computes the additive frame and writes it to the strip.
        """
        now = time.perf_counter()
        dt  = now - self._last_render
        self._last_render = now

        frame = np.zeros((self._N, 3), dtype=np.float32)   # R G B  0..255

        with self._lock:
            self._render_pulse(frame, now)
            self._render_wave(frame, now)
            self._render_sparkles(frame, dt)

        # Clamp and write to hardware
        frame = np.clip(frame, 0, 255).astype(np.uint8)
        if self._strip:
            for i in range(self._N):
                self._strip.setPixelColor(i, Color(int(frame[i, 0]),
                                                    int(frame[i, 1]),
                                                    int(frame[i, 2])))
            self._strip.show()

    def clear(self) -> None:
        if self._strip:
            for i in range(self._N):
                self._strip.setPixelColor(i, Color(0, 0, 0))
            self._strip.show()

    # ── Effect renderers (called inside _lock) ───────────────────────

    def _render_pulse(self, frame: np.ndarray, now: float) -> None:
        if self._pulse_t0 is None:
            return
        t = 1.0 - (now - self._pulse_t0) / self._pulse_decay_s   # 1→0
        if t <= 0:
            self._pulse_t0 = None
            return
        center = (self._N - 1) / 2.0
        for i in range(self._N):
            dist = abs(i - center) / center      # 0 at centre, 1 at edges
            brightness = t * max(0.0, 1.0 - dist) * 255.0
            frame[i, 0] += brightness            # Red channel

    def _render_wave(self, frame: np.ndarray, now: float) -> None:
        if self._wave_t0 is None:
            return
        elapsed = now - self._wave_t0
        pos = elapsed / self._wave_speed_spl     # current LED position (float)

        if pos > self._N + 4:                    # wave has left the strip
            self._wave_t0 = None
            return

        width = 3.0                              # gaussian half-width in LEDs
        for i in range(self._N):
            dist = abs(i - pos)
            brightness = max(0.0, 1.0 - dist / width) * 200.0
            frame[i, 1] += brightness            # Green channel

    def _render_sparkles(self, frame: np.ndarray, dt: float) -> None:
        # Spawn new sparkles if HIGH is active
        if self._sparkle_on:
            for i in range(self._N):
                if random.random() < self._sparkle_density:
                    self._sparkles[i] = 1.0
            self._sparkle_on = False             # consume the trigger

        # Decay and draw
        decay_rate = 4.0                         # brightness units per second
        finished = []
        for i, b in self._sparkles.items():
            b -= decay_rate * dt
            if b <= 0:
                finished.append(i)
            else:
                self._sparkles[i] = b
                val = b * 255.0
                frame[i] += val                  # White = add to all channels

        for i in finished:
            del self._sparkles[i]
