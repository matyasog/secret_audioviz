"""
spotify_poller.py
Background thread that polls the Spotify Web API every N seconds
and exposes the current track info via get_current().

Uses `spotipy` for OAuth2 handling (token refresh is automatic).
On first run it will open your browser for a one-time authorisation.
The token is cached in .spotify_cache so subsequent runs are silent.
"""
import os
import time
import threading
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

load_dotenv()


class SpotifyPoller:
    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self._current: dict = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

        self._sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/callback"),
                scope="user-read-currently-playing",
                cache_path=".spotify_cache",
                open_browser=True,
            )
        )

    # ── public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        """Kick off the background polling thread."""
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="SpotifyPoller")
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def get_current(self) -> dict:
        """Thread-safe snapshot of the latest Spotify data (empty dict if nothing playing)."""
        with self._lock:
            return dict(self._current)

    # ── internals ───────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                result = self._sp.currently_playing()
                if result and result.get("item"):
                    item = result["item"]
                    images = item["album"]["images"]
                    # Spotify returns images sorted largest→smallest; index 0 is highest res
                    cover_url = images[0]["url"] if images else None
                    data = {
                        "track_id":       item["id"],
                        "track_name":     item["name"],
                        "artist_name":    ", ".join(a["name"] for a in item["artists"]),
                        "album_cover_url": cover_url,
                        "progress_ms":    result.get("progress_ms", 0),
                        "duration_ms":    item["duration_ms"],
                        "is_playing":     result.get("is_playing", False),
                    }
                    with self._lock:
                        self._current = data
                else:
                    # Nothing playing — clear so the matrix doesn't keep old art
                    with self._lock:
                        self._current = {}
            except Exception as exc:
                print(f"[Spotify] Poll error: {exc}")

            time.sleep(self.poll_interval)
