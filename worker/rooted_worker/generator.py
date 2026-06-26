"""Generation step: Genblaze multi-provider, behind a Protocol with a deterministic fake for tests.

The real Genblaze generator is a documented stub until provider keys land and the alpha SDK API can
be verified live (Genblaze is v0.3.x and churny). The pipeline is exercised end to end with the
fake, so the orchestration is proven and the real generator drops in at the seam.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from PIL import Image


@dataclass
class GenerationResult:
    image: Image.Image
    image_bytes: bytes
    model: str
    provider: str


class Generator(Protocol):
    def generate(self, prompt: str) -> GenerationResult: ...


class FakeGenerator:
    """Deterministic, network-free generator: a prompt-seeded image. Same prompt, same image."""

    def __init__(self, model: str = "fake-image-1", provider: str = "fake") -> None:
        self._model = model
        self._provider = provider

    def generate(self, prompt: str) -> GenerationResult:
        seed = int.from_bytes(hashlib.sha256(prompt.encode()).digest()[:4], "big")
        arr = np.random.default_rng(seed).integers(0, 256, (256, 256, 3), dtype=np.uint8)
        image = Image.fromarray(arr).resize((64, 64)).resize((256, 256))
        buf = io.BytesIO()
        image.save(buf, "PNG")
        return GenerationResult(image, buf.getvalue(), self._model, self._provider)


class GenblazeGenerator:
    """Real Genblaze multi-provider generation with provider fallback. Needs the `genblaze` extra
    plus provider credentials. Intended shape (verify against the live alpha SDK when keys land):

        p = genblaze_core.Pipeline().step(Provider(primary, fallback_models=fallbacks))
        run = pipeline.run(prompt=prompt, sink=ObjectStorageSink(...))
        # -> run.assets[0] bytes + run.manifest (model, provider)
    """

    def __init__(self, primary: str, fallbacks: list[str] | None = None) -> None:
        self._primary = primary
        self._fallbacks = fallbacks or []

    def generate(self, prompt: str) -> GenerationResult:
        raise NotImplementedError(
            "GenblazeGenerator is wired when provider keys land (alpha SDK verified live then)."
        )
