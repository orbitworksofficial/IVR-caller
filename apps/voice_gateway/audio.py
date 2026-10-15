"""
G.711 µ-law <-> 16-bit linear PCM conversion.

Twilio Media Streams carry 8kHz mono µ-law in both directions, but:
  * ElevenLabsTTS returns 8kHz 16-bit little-endian PCM (output_format=pcm_8000)
  * TurnManager measures barge-in energy assuming 16-bit PCM
so the gateway converts at the boundary. Deepgram is configured for µ-law and
receives Twilio's bytes untouched.

Implemented with numpy because the stdlib `audioop` module was removed in
Python 3.13.
"""

import numpy as np

_BIAS = 0x84
_CLIP = 32635


def _build_decode_table() -> np.ndarray:
    u = ~np.arange(256, dtype=np.int32) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return np.where(sign != 0, -sample, sample).astype("<i2")


_DECODE_TABLE = _build_decode_table()


def ulaw_to_pcm16(data: bytes) -> bytes:
    """Decode µ-law bytes to 16-bit little-endian PCM (2 bytes per sample)."""
    if not data:
        return b""
    return _DECODE_TABLE[np.frombuffer(data, dtype=np.uint8)].tobytes()


def pcm16_to_ulaw(data: bytes) -> bytes:
    """Encode 16-bit little-endian PCM to µ-law. `data` must be an even length."""
    if not data:
        return b""
    s = np.frombuffer(data, dtype="<i2").astype(np.int32)
    sign = (s < 0).astype(np.int32) << 7
    s = np.minimum(np.abs(s), _CLIP) + _BIAS
    exponent = np.floor(np.log2(s)).astype(np.int32) - 7
    mantissa = (s >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


class PCMToUlawStream:
    """
    Stateful encoder for a PCM byte stream whose chunks may split a sample.

    ElevenLabs streams with iter_chunked(1024), and HTTP chunk boundaries are
    not guaranteed to land on an even byte, so an odd trailing byte is carried
    into the next chunk instead of corrupting every following sample.
    """

    def __init__(self):
        self._carry = b""

    def encode(self, chunk: bytes) -> bytes:
        data = self._carry + chunk
        cut = len(data) - (len(data) % 2)
        self._carry = data[cut:]
        return pcm16_to_ulaw(data[:cut])

    def reset(self) -> None:
        self._carry = b""
