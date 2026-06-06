"""
led_strip.py  —  RPi Node
Controls the WS2812B LED strip via rpi_ws281x using a layered visual engine.
"""
import math
import random
import threading
import time

import numpy as np

try:
    from rpi_ws281x import Color, PixelStrip

    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    PixelStrip = None
    Color = None
    print("[Strip] rpi_ws281x not found — running in mock mode")


class LEDStrip:
    def __init__(self, cfg: dict) -> None:
        self._lock = threading.Lock()
        self._last_render = time.perf_counter()
        self._band_targets = {"low": 0.0, "mid": 0.0, "high": 0.0}
        self._band_levels = {"low": 0.0, "mid": 0.0, "high": 0.0}
        self._master_level = 0.0
        self._low_memory = 0.0
        self._mid_phase = 0.0
        self._bed_phase = 0.0
        self._sparkles: list[dict[str, float]] = []
        self._strip = None
        self._startup_test_enabled = False
        self._startup_test_seconds = 0.6

        self.reload_config(cfg, first_load=True)
        self._init_hardware()

    def reload_config(self, cfg: dict, first_load: bool = False) -> None:
        with self._lock:
            self._N = max(1, int(cfg.get("num_leds", 30)))
            self._enabled = bool(cfg.get("ws281_enabled", True))
            self._gpio = int(cfg.get("ws281_gpio", 19))
            self._freq_hz = int(cfg.get("ws281_freq_hz", 800000))
            self._dma = int(cfg.get("ws281_dma", 10))
            self._invert = bool(cfg.get("ws281_invert", False))
            self._hw_brightness = int(cfg.get("ws281_brightness", 255))
            self._channel = int(cfg.get("ws281_channel", 1))

            self._startup_test_enabled = bool(cfg.get("strip_startup_test", False))
            self._startup_test_seconds = max(0.1, float(cfg.get("strip_startup_test_seconds", 0.6)))

            self._gamma = max(1.0, float(cfg.get("strip_gamma", 2.0)))
            self._max_brightness = float(np.clip(cfg.get("strip_max_brightness", 0.42), 0.0, 1.0))

            self._attack_s = max(0.001, float(cfg.get("band_attack_ms", 45)) / 1000.0)
            self._release_s = max(0.001, float(cfg.get("band_release_ms", 220)) / 1000.0)
            self._low_decay_s = max(0.001, float(cfg.get("low_decay_ms", 320)) / 1000.0)

            self._low_gain = float(cfg.get("low_gain", 1.10))
            self._mid_gain = float(cfg.get("mid_gain", 1.05))
            self._high_gain = float(cfg.get("high_gain", 0.85))
            self._mid_speed = float(cfg.get("mid_speed", 0.70))
            self._high_spawn_rate = float(cfg.get("high_spawn_rate", 9.5))

            self._low_color_inner = np.array(cfg.get("low_color_inner", [1.00, 0.34, 0.08]), dtype=np.float32)
            self._low_color_outer = np.array(cfg.get("low_color_outer", [0.85, 0.08, 0.02]), dtype=np.float32)
            self._mid_color_a = np.array(cfg.get("mid_color_a", [0.08, 0.78, 0.34]), dtype=np.float32)
            self._mid_color_b = np.array(cfg.get("mid_color_b", [0.04, 0.58, 0.90]), dtype=np.float32)
            self._high_color = np.array(cfg.get("high_color", [0.78, 0.90, 1.00]), dtype=np.float32)
            self._bed_color_a = np.array(cfg.get("bed_color_a", [0.03, 0.02, 0.01]), dtype=np.float32)
            self._bed_color_b = np.array(cfg.get("bed_color_b", [0.01, 0.04, 0.05]), dtype=np.float32)

            if len(self._sparkles) > self._N * 3:
                self._sparkles = self._sparkles[-self._N * 3 :]

        if not first_load:
            print(
                "[Strip] Reloaded config: "
                f"N={self._N}, gamma={self._gamma:.2f}, max_brightness={self._max_brightness:.2f}, "
                f"attack={self._attack_s * 1000:.0f}ms, release={self._release_s * 1000:.0f}ms"
            )

    def _init_hardware(self) -> None:
        print(
            "[Strip] Startup config: "
            f"enabled={self._enabled}, leds={self._N}, gpio={self._gpio}, channel={self._channel}, "
            f"dma={self._dma}, freq={self._freq_hz}, brightness={self._hw_brightness}"
        )

        if not self._enabled:
            print("[Strip] WS281 output disabled in config")
            self._strip = None
            return

        if not _HW_AVAILABLE:
            print("[Strip] Hardware library unavailable; strip will stay in mock mode")
            self._strip = None
            return

        try:
            self._strip = PixelStrip(
                self._N,
                self._gpio,
                self._freq_hz,
                self._dma,
                self._invert,
                self._hw_brightness,
                self._channel,
            )
            self._strip.begin()
        except Exception as exc:
            raise RuntimeError(f"WS281 init failed on GPIO {self._gpio} / channel {self._channel}: {exc}") from exc

        print("[Strip] Hardware mode active")
        if self._startup_test_enabled:
            self._run_startup_test()

    def update_bands(self, low: float | bool, mid: float | bool, high: float | bool) -> None:
        with self._lock:
            self._band_targets["low"] = self._coerce_band_value(low)
            self._band_targets["mid"] = self._coerce_band_value(mid)
            self._band_targets["high"] = self._coerce_band_value(high)

    def render(self) -> None:
        now = time.perf_counter()
        dt = max(1e-3, now - self._last_render)
        self._last_render = now

        with self._lock:
            targets = dict(self._band_targets)
            levels_before = dict(self._band_levels)
            low_memory = self._low_memory
            mid_phase = self._mid_phase
            bed_phase = self._bed_phase
            sparkles = [dict(s) for s in self._sparkles]

        levels, master_level, low_memory = self._advance_envelopes(targets, levels_before, low_memory, dt)
        bed_phase += dt * (0.10 + master_level * 0.18)
        mid_phase += dt * (0.55 + levels["mid"] * self._mid_speed * 2.8)
        sparkles = self._advance_sparkles(sparkles, levels["high"], dt)

        indices = np.arange(self._N, dtype=np.float32)
        norm = indices / max(1.0, self._N - 1.0)

        frame = np.zeros((self._N, 3), dtype=np.float32)
        frame += self._render_bed_layer(norm, bed_phase, master_level)
        frame += self._render_low_layer(norm, levels["low"], low_memory)
        frame += self._render_mid_layer(norm, mid_phase, levels["mid"])
        frame += self._render_high_layer(sparkles)

        frame = self._tone_map(frame, master_level)
        self._write_frame(frame)

        with self._lock:
            self._band_levels = levels
            self._master_level = master_level
            self._low_memory = low_memory
            self._mid_phase = mid_phase
            self._bed_phase = bed_phase
            self._sparkles = sparkles

    def clear(self) -> None:
        self._write_frame(np.zeros((self._N, 3), dtype=np.float32))

    def _coerce_band_value(self, value: float | bool) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        try:
            return float(np.clip(float(value), 0.0, 1.0))
        except (TypeError, ValueError):
            return 0.0

    def _advance_envelopes(
        self,
        targets: dict[str, float],
        levels: dict[str, float],
        low_memory: float,
        dt: float,
    ) -> tuple[dict[str, float], float, float]:
        updated: dict[str, float] = {}
        for band in ("low", "mid", "high"):
            current = levels[band]
            target = targets[band]
            tau = self._attack_s if target >= current else self._release_s
            alpha = 1.0 - math.exp(-dt / tau)
            updated[band] = current + (target - current) * alpha

        low_decay = math.exp(-dt / self._low_decay_s)
        low_memory = max(updated["low"], low_memory * low_decay)

        master_level = float(
            np.clip(
                0.55 * low_memory + 0.30 * updated["mid"] + 0.25 * updated["high"],
                0.0,
                1.0,
            )
        )
        return updated, master_level, low_memory

    def _render_bed_layer(self, norm: np.ndarray, phase: float, master_level: float) -> np.ndarray:
        mix = 0.5 + 0.5 * np.sin((norm * 2.0 * math.pi) + phase * 2.0 * math.pi)
        gradient = ((1.0 - mix)[:, None] * self._bed_color_a) + (mix[:, None] * self._bed_color_b)
        pulse = 0.55 + 0.45 * np.sin((norm * 4.0 * math.pi) - phase * 1.7 * math.pi)
        strength = 0.030 + 0.13 * (master_level ** 1.1)
        return gradient * pulse[:, None] * strength

    def _render_low_layer(self, norm: np.ndarray, low_level: float, low_memory: float) -> np.ndarray:
        if low_memory <= 0.001:
            return np.zeros((self._N, 3), dtype=np.float32)

        center = 0.5
        dist = np.abs(norm - center) / 0.5
        radius = 0.34 + 0.26 * low_memory
        core = np.exp(-((dist / max(radius, 1e-3)) ** 1.9))
        halo = np.exp(-((dist / max(radius * 2.4, 1e-3)) ** 1.15))
        color_mix = np.clip(dist, 0.0, 1.0)
        colors = ((1.0 - color_mix)[:, None] * self._low_color_inner) + (color_mix[:, None] * self._low_color_outer)
        intensity = np.clip(0.24 + 0.82 * low_memory + 0.34 * low_level, 0.0, 1.35) * self._low_gain
        full_strip_wash = (0.06 + 0.24 * low_memory) * self._low_color_outer
        return colors * (0.58 * core[:, None] + 0.42 * halo[:, None]) * intensity + full_strip_wash

    def _render_mid_layer(self, norm: np.ndarray, phase: float, mid_level: float) -> np.ndarray:
        if mid_level <= 0.001:
            return np.zeros((self._N, 3), dtype=np.float32)

        crest = 0.5 + 0.5 * np.sin((norm * 2.3 * math.pi) - phase * 2.0 * math.pi)
        ribbon = crest ** (1.8 - 0.9 * mid_level)
        ribbon_fill = 0.30 + 0.70 * crest
        shimmer = 0.5 + 0.5 * np.sin((norm * 8.5 * math.pi) + phase * 5.0 * math.pi)
        color_mix = 0.35 + 0.65 * crest
        colors = ((1.0 - color_mix)[:, None] * self._mid_color_a) + (color_mix[:, None] * self._mid_color_b)
        intensity = (0.18 + 0.72 * mid_level) * self._mid_gain
        return colors * (0.62 * ribbon[:, None] + 0.24 * ribbon_fill[:, None] + 0.14 * shimmer[:, None]) * intensity

    def _advance_sparkles(self, sparkles: list[dict[str, float]], high_level: float, dt: float) -> list[dict[str, float]]:
        updated: list[dict[str, float]] = []
        for sparkle in sparkles:
            sparkle["age"] += dt
            if sparkle["age"] < sparkle["ttl"]:
                updated.append(sparkle)

        if high_level <= 0.01:
            return updated

        expected = self._high_spawn_rate * high_level * dt
        spawn_count = int(expected)
        if random.random() < (expected - spawn_count):
            spawn_count += 1

        for _ in range(spawn_count):
            updated.append(
                {
                    "pos": random.uniform(0.0, self._N - 1.0),
                    "ttl": random.uniform(0.12, 0.30),
                    "age": 0.0,
                    "amp": random.uniform(0.45, 1.0) * high_level,
                    "width": random.uniform(0.75, 1.60),
                    "twinkle": random.uniform(4.0, 9.0),
                }
            )

        max_sparkles = max(6, self._N // 2)
        if len(updated) > max_sparkles:
            updated = updated[-max_sparkles:]
        return updated

    def _render_high_layer(self, sparkles: list[dict[str, float]]) -> np.ndarray:
        if not sparkles:
            return np.zeros((self._N, 3), dtype=np.float32)

        layer = np.zeros((self._N, 3), dtype=np.float32)
        indices = np.arange(self._N, dtype=np.float32)
        for sparkle in sparkles:
            life = sparkle["age"] / sparkle["ttl"]
            fade = max(0.0, 1.0 - life)
            twinkle = 0.7 + 0.3 * math.sin((life * sparkle["twinkle"] + sparkle["pos"] * 0.13) * math.pi)
            envelope = np.exp(-((indices - sparkle["pos"]) / sparkle["width"]) ** 2)
            strength = sparkle["amp"] * fade * twinkle * self._high_gain * 0.55
            layer += envelope[:, None] * self._high_color * strength
        return np.clip(layer, 0.0, 0.68)

    def _tone_map(self, frame: np.ndarray, master_level: float) -> np.ndarray:
        compressed = 1.0 - np.exp(-np.clip(frame, 0.0, None))
        brightness = self._max_brightness * (0.44 + 0.56 * max(master_level, 0.12))
        corrected = np.power(np.clip(compressed * brightness, 0.0, 1.0), self._gamma)
        return np.clip(corrected, 0.0, 1.0)

    def _write_frame(self, frame: np.ndarray) -> None:
        pixels = np.clip(frame * 255.0, 0.0, 255.0).astype(np.uint8)
        if self._strip:
            for i in range(self._N):
                self._strip.setPixelColor(i, Color(int(pixels[i, 0]), int(pixels[i, 1]), int(pixels[i, 2])))
            self._strip.show()

    def _run_startup_test(self) -> None:
        if not self._strip:
            return

        print(f"[Strip] Running startup test for {self._startup_test_seconds:.2f}s")
        hold = self._startup_test_seconds / 4.0
        solid_colors = (
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 0.0, 1.0], dtype=np.float32),
        )
        for color in solid_colors:
            frame = np.tile(color, (self._N, 1))
            self._write_frame(frame * self._max_brightness)
            time.sleep(hold)

        step_delay = self._startup_test_seconds / max(1, self._N)
        for idx in range(self._N):
            frame = np.zeros((self._N, 3), dtype=np.float32)
            frame[idx] = np.array([1.0, 1.0, 1.0], dtype=np.float32) * self._max_brightness
            self._write_frame(frame)
            time.sleep(step_delay)

        self.clear()
        print("[Strip] Startup test complete")
