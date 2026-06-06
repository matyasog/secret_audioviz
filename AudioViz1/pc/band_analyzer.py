"""
band_analyzer.py
Converts raw mono audio samples into Low / Mid / High boolean triggers
using a real FFT and a rolling adaptive threshold.
"""
import numpy as np


class BandAnalyzer:
    # Frequency band edges (Hz)
    BANDS = {
        "low":  (20,   200),
        "mid":  (200,  2000),
        "high": (2000, 8000),
    }

    def __init__(self, sample_rate: int = 44100, history_len: int = 43, sensitivity: float = 3.0, min_energy: float = 0.02, min_rms: float = 0.001):
        """
        Args:
            sample_rate:  Audio sample rate in Hz.
            history_len:  Number of frames kept for the adaptive baseline.
                          ~43 frames @ 21 fps ≈ 2 seconds of history.
            sensitivity:  Multiplier above the rolling mean required to fire.
                          3.0 = must be 200% louder than average.
            min_energy:   Absolute minimum energy required to trigger (ignores dither/noise).
            min_rms:     Minimum overall RMS level to consider audio active.
        """
        self.sample_rate = sample_rate
        self.sensitivity = sensitivity
        self.min_energy = min_energy
        self.min_rms = min_rms
        self._history: dict[str, list[float]] = {b: [] for b in self.BANDS}
        self._history_len = history_len
        self._window = None  # built lazily so we know the block size

    def analyze(self, samples: np.ndarray) -> dict[str, bool]:
        """
        Args:
            samples: 1-D float32 mono array (one audio block).
        Returns:
            {"low": bool, "mid": bool, "high": bool}
        """
        n = len(samples)

        # Build/cache the Hann window for this block size
        if self._window is None or len(self._window) != n:
            self._window = np.hanning(n)

        windowed = samples * self._window
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(n, 1.0 / self.sample_rate)

        # Compute overall RMS to filter out silence/background noise
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        if rms < self.min_rms:
            # Silence or very low level – treat as no activity
            return {band: False for band in self.BANDS}

        result: dict[str, bool] = {}
        for band, (f_lo, f_hi) in self.BANDS.items():
            mask = (freqs >= f_lo) & (freqs < f_hi)
            energy = float(np.sum(spectrum[mask] ** 2))

            hist = self._history[band]
            hist.append(energy)
            if len(hist) > self._history_len:
                hist.pop(0)

            # Need at least 5 frames before triggering to avoid false starts
            if len(hist) >= 5:
                baseline = np.mean(hist) * self.sensitivity
                threshold = max(self.min_energy, baseline)
                result[band] = bool(energy > threshold)
            else:
                result[band] = False

        return result
