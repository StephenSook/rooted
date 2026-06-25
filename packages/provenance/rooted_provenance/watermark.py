"""TrustMark variant P watermark, behind a Protocol.

The real implementation pulls torch (heavy), so it lives behind the optional `watermark` extra and
is imported lazily. Unit tests use the in-process fake. The real watermark round-trip is validated
separately by the Gate 1 kill experiment harness.
"""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class Watermarker(Protocol):
    def encode(self, image: Image.Image, secret: str) -> Image.Image: ...
    def decode(self, image: Image.Image) -> tuple[str | None, float]: ...


class FakeWatermarker:
    """Deterministic stand-in for tests. decode() returns whatever id it was constructed with."""

    def __init__(self, decoded_id: str | None = None, confidence: float = 1.0) -> None:
        self._decoded_id = decoded_id
        self._confidence = confidence

    def encode(self, image: Image.Image, secret: str) -> Image.Image:
        return image

    def decode(self, image: Image.Image) -> tuple[str | None, float]:
        if self._decoded_id is None:
            return None, 0.0
        return self._decoded_id, self._confidence


class TrustMarkWatermarker:
    """Real TrustMark variant P (BCH_SUPER). Requires the `watermark` extra (trustmark + torch)."""

    def __init__(self) -> None:
        from trustmark import TrustMark

        self._tm = TrustMark(
            verbose=False, model_type="P", encoding_type=TrustMark.Encoding.BCH_SUPER
        )

    def encode(self, image: Image.Image, secret: str) -> Image.Image:
        return self._tm.encode(image.convert("RGB"), secret, MODE="text")

    def decode(self, image: Image.Image) -> tuple[str | None, float]:
        secret, present, _schema = self._tm.decode(image.convert("RGB"), MODE="text")
        return (secret if present else None), (1.0 if present else 0.0)
