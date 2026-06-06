"""
band_analyzer.py
Converts raw mono audio samples into normalized Low / Mid / High energy values
using a real FFT and a rolling adaptive baseline.
"""
import numpy as np


class BandAnalyzer:
    # Frequency band edges (Hz)
    BANDS = {
        "low": (20, 200),
        "mid": (200, 2000),
        "high": (2000, 8000),
    }

    def __init__(
        self,
        sample_rate: int = 44100,
        history_len: int = 43,
        sensitivity: float = 3.0,
        min_energy: float = 0.02,
        min_rms: float = 0.001,
    ):
        """
        Args:
            sample_rate: Audio sample rate in Hz.
            history_len: Number of frames kept for the adaptive baseline.
            sensitivity: Ratio above the rolling mean that maps to full intensity.
            min_energy: Absolute minimum energy floor used for normalization.
            min_rms: Minimum overall RMS level to consider audio active.
        """
        self.sample_rate = sample_rate
        self.sensitivity = max(1.1, sensitivity)
        self.min_energy = min_energy
        self.min_rms = min_rms
        self._history: dict[str, list[float]] = {b: [] for b in self.BANDS}
        self._history_len = history_len
        self._window = None

    def analyze(self, samples: np.ndarray) -> dict[str, float]:
        """
        Args:
            samples: 1-D float32 mono array (one audio block).
        Returns:
            {"low": float, "mid": float, "high": float}
        """
        n = len(samples)

        if self._window is None or len(self._window) != n:
            self._window = np.hanning(n)

        windowed = samples * self._window
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)

        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        if rms < self.min_rms:
            return {band: 0.0 for band in self.BANDS}

        result: dict[str, float] = {}
        for band, (f_lo, f_hi) in self.BANDS.items():
            mask = (freqs >= f_lo) & (freqs < f_hi)
            energy = float(np.sum(spectrum[mask] ** 2))

            hist = self._history[band]
            hist.append(energy)
            if len(hist) > self._history_len:
                hist.pop(0)

            baseline = float(np.mean(hist)) if hist else 0.0
            floor = max(self.min_energy, baseline)
            ratio = energy / max(floor, 1e-9)
            normalized = np.clip((ratio - 1.0) / (self.sensitivity - 1.0), 0.0, 1.0)

            # Slightly lift medium values so the strip responds smoothly.
            result[band] = float(normalized ** 0.8)

        return result
