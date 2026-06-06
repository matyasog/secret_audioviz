"""
led_matrix.py
Minimal Spotify cover renderer for the HUB75 matrix.

This intentionally keeps the image path simple:
download -> Pillow decode -> thumbnail -> SetImage.
"""
import os
import threading
import time
from io import BytesIO

import requests
from PIL import Image, ImageFile

import PIL.JpegImagePlugin  # ensure JPEG support is registered

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.init()

print(
    f"[Matrix] PIL {Image.__version__} | "
    f"JPEG registered: {'JPEG' in Image.registered_extensions().values()}"
)

os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    _HW_AVAILABLE = True
except ImportError:
    _HW_AVAILABLE = False
    print("[Matrix] rgbmatrix not found; running in mock mode")


_HTTP = requests.Session()
_HTTP.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/jpeg,image/png,image/*,*/*;q=0.8",
    "Accept-Encoding": "identity",
})


def _decode_cover(raw_bytes: bytes, width: int, height: int) -> Image.Image:
    img = Image.open(BytesIO(raw_bytes))
    img.load()
    img = img.convert("RGB")
    img.thumbnail((width, height), Image.Resampling.LANCZOS)

    if img.size == (width, height):
        return img

    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    x = (width - img.width) // 2
    y = (height - img.height) // 2
    canvas.paste(img, (x, y))
    return canvas


class LEDMatrix:
    def __init__(self, cfg: dict) -> None:
        self._rows = cfg.get("matrix_rows", 64)
        self._cols = cfg.get("matrix_cols", 64)
        self._current_url: str | None = None
        self._current_track_id: str | None = None
        self._current_image: Image.Image | None = None
        self._ending_track_id: str | None = None
        self._stop_fade_in_progress = False
        self._fade_steps = max(1, int(cfg.get("matrix_fade_steps", 20)))
        self._fade_delay = max(0.0, float(cfg.get("matrix_fade_delay", 0.03)))
        self._end_fade_remaining_ms = max(0, int(cfg.get("matrix_end_fade_remaining_ms", 1500)))
        self._transition_token = 0
        self._lock = threading.Lock()
        self._matrix = None

        if _HW_AVAILABLE:
            opt = RGBMatrixOptions()
            opt.rows = self._rows
            opt.cols = self._cols
            opt.brightness = cfg.get("matrix_brightness", 100)
            opt.hardware_mapping = cfg.get("matrix_hardware_mapping", "adafruit-hat-pwm")
            opt.chain_length = cfg.get("matrix_chain_length", 1)
            opt.parallel = cfg.get("matrix_parallel", 1)
            opt.row_address_type = cfg.get("matrix_row_address_type", 0)
            opt.multiplexing = cfg.get("matrix_multiplexing", 0)
            opt.gpio_slowdown = cfg.get("matrix_gpio_slowdown", 4)
            opt.pwm_bits = cfg.get("matrix_pwm_bits", 11)
            opt.led_rgb_sequence = cfg.get("matrix_rgb_sequence", "RGB")
            opt.disable_hardware_pulsing = cfg.get("matrix_disable_hardware_pulsing", False)
            self._matrix = RGBMatrix(options=opt)
            print("[Matrix] Hardware initialised")

            if cfg.get("matrix_startup_test", False):
                self._startup_test(cfg.get("matrix_startup_test_seconds", 0.5))

    def update(self, spotify: dict) -> None:
        with self._lock:
            if not spotify or not spotify.get("is_playing", False):
                if self._stop_fade_in_progress:
                    return
                image = self._current_image.copy() if self._current_image else None
                token = self._begin_transition_locked()
                self._current_url = None
                self._current_track_id = None
                self._current_image = None
                self._ending_track_id = None
                self._stop_fade_in_progress = True
                if image is None:
                    if self._matrix:
                        self._matrix.Clear()
                    return
                self._start_worker(self._fade_out_image, image, token)
                return

            url = spotify.get("album_cover_url")
            track_id = spotify.get("track_id") or url
            progress_ms = int(spotify.get("progress_ms") or 0)
            duration_ms = int(spotify.get("duration_ms") or 0)

            if not url:
                return

            if track_id == self._ending_track_id:
                return

            remaining_ms = max(0, duration_ms - progress_ms) if duration_ms > 0 else None
            if (
                track_id == self._current_track_id
                and self._current_image is not None
                and remaining_ms is not None
                and remaining_ms <= self._end_fade_remaining_ms
                and self._ending_track_id != track_id
            ):
                image = self._current_image.copy()
                token = self._begin_transition_locked()
                self._current_image = None
                self._current_url = None
                self._current_track_id = None
                self._ending_track_id = track_id
                self._start_worker(self._fade_out_image, image, token)
                return

            if track_id == self._current_track_id and url == self._current_url:
                return

            self._current_url = url
            self._current_track_id = track_id
            self._current_image = None
            self._ending_track_id = None
            self._stop_fade_in_progress = False
            token = self._begin_transition_locked()

        self._start_worker(self._download_and_fade_in, url, track_id, token)

    def clear(self) -> None:
        with self._lock:
            self._begin_transition_locked()
            self._current_url = None
            self._current_track_id = None
            self._current_image = None
            self._ending_track_id = None
            self._stop_fade_in_progress = False
        if self._matrix:
            self._matrix.Clear()

    def _download_and_fade_in(self, url: str, track_id: str, token: int) -> None:
        if not self._matrix:
            return

        try:
            response = _HTTP.get(url, timeout=10)
            response.raise_for_status()
            if not response.content:
                raise ValueError("empty image response")

            print(
                f"[Matrix] Downloaded cover: {len(response.content)} bytes "
                f"({response.headers.get('Content-Type', 'unknown')})"
            )

            image = _decode_cover(response.content, self._cols, self._rows)
            with self._lock:
                if token != self._transition_token or self._current_track_id != track_id:
                    return
                self._current_image = image.copy()

            self._fade_in_image(image, token)
            print(f"[Matrix] Rendered cover {image.size} from {url}")
        except Exception as exc:
            print(f"[Matrix] Render failed: {exc}")
            with self._lock:
                if self._current_url == url and self._current_track_id == track_id:
                    self._current_url = None
                    self._current_track_id = None
                    self._current_image = None

    def _fade_in_image(self, image: Image.Image, token: int) -> None:
        for step in range(1, self._fade_steps + 1):
            if not self._is_token_active(token):
                return
            alpha = step / self._fade_steps
            frame = Image.blend(self._blank_image(), image, alpha)
            self._matrix.SetImage(frame.convert("RGB"))
            time.sleep(self._fade_delay)

    def _fade_out_image(self, image: Image.Image, token: int) -> None:
        for step in range(self._fade_steps - 1, -1, -1):
            if not self._is_token_active(token):
                return
            alpha = step / self._fade_steps
            frame = Image.blend(self._blank_image(), image, alpha)
            self._matrix.SetImage(frame.convert("RGB"))
            time.sleep(self._fade_delay)

        if self._is_token_active(token):
            self._matrix.Clear()

    def _blank_image(self) -> Image.Image:
        return Image.new("RGB", (self._cols, self._rows), (0, 0, 0))

    def _is_token_active(self, token: int) -> bool:
        with self._lock:
            return token == self._transition_token

    def _begin_transition_locked(self) -> int:
        self._transition_token += 1
        return self._transition_token

    def _start_worker(self, target, *args) -> None:
        thread = threading.Thread(
            target=target,
            args=args,
            daemon=True,
            name=f"Matrix-{target.__name__}",
        )
        thread.start()

    def _startup_test(self, duration_s: float) -> None:
        if not self._matrix:
            return

        import time

        for color in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)]:
            image = Image.new("RGB", (self._cols, self._rows), color)
            self._matrix.SetImage(image)
            time.sleep(duration_s)

        print("[Matrix] Startup test done")
