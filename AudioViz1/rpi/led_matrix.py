"""
led_matrix.py
Minimal Spotify cover renderer for the HUB75 matrix.

This intentionally keeps the image path simple:
download -> Pillow decode -> thumbnail -> SetImage.
"""
import os
import threading
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
        if not spotify:
            return

        url = spotify.get("album_cover_url")
        if not url:
            return

        with self._lock:
            if url == self._current_url:
                return
            self._current_url = url

        thread = threading.Thread(
            target=self._download_and_show,
            args=(url,),
            daemon=True,
            name="MatrixCoverDownload",
        )
        thread.start()

    def clear(self) -> None:
        if self._matrix:
            self._matrix.Clear()

    def _download_and_show(self, url: str) -> None:
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
            self._matrix.SetImage(image.convert("RGB"))
            print(f"[Matrix] Rendered cover {image.size} from {url}")
        except Exception as exc:
            print(f"[Matrix] Render failed: {exc}")
            with self._lock:
                if self._current_url == url:
                    self._current_url = None

    def _startup_test(self, duration_s: float) -> None:
        if not self._matrix:
            return

        import time

        for color in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)]:
            image = Image.new("RGB", (self._cols, self._rows), color)
            self._matrix.SetImage(image)
            time.sleep(duration_s)

        print("[Matrix] Startup test done")
