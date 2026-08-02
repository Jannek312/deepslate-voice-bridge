"""Streaming 16 kHz → 24 kHz mono PCM16 upsampler.

The Voice PE mic streams 16 kHz PCM16; the Deepslate session runs at 24 kHz
both directions (the SDK configures one audio line for input and output, and
the firmware's playback path is hardcoded to 24 kHz). Linear interpolation at
an exact 2:3 ratio is plenty for speech that originated at 16 kHz — upsampling
adds no information, it only re-times the samples.

Streaming: carries the previous input sample and the fractional phase across
chunk boundaries, so arbitrary chunking produces the identical sample stream.
"""

from __future__ import annotations

import array


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale mono PCM16 by `gain`, clamping to int16 range."""
    if gain == 1.0 or not pcm:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm)
    out = array.array(
        "h", (max(-32768, min(32767, int(s * gain))) for s in samples)
    )
    return out.tobytes()


def levels(pcm: bytes) -> tuple[float, float]:
    """(rms, peak) of mono PCM16 as fractions of full scale."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0, 0.0
    peak = max(abs(s) for s in samples) / 32768
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768
    return rms, peak


class Upsampler16to24:
    """Phase-accumulator linear interpolator, 2 input samples → 3 output samples."""

    def __init__(self) -> None:
        self._prev: int | None = None
        self._phase = 0  # next output position relative to prev, in thirds (0..2)

    def reset(self) -> None:
        self._prev = None
        self._phase = 0

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        samples = array.array("h")
        samples.frombytes(pcm)

        out = array.array("h")
        prev = self._prev
        phase = self._phase
        for s in samples:
            if prev is None:
                prev = s
                continue
            while phase < 3:
                out.append(prev + ((s - prev) * phase) // 3)
                phase += 2
            phase -= 3
            prev = s
        self._prev = prev
        self._phase = phase
        return out.tobytes()
