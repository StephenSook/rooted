"""Generation step: Genblaze multi-provider, behind a Protocol with a deterministic fake for tests.

The real Genblaze generator is a documented stub until provider keys land and the alpha SDK API can
be verified live (Genblaze is v0.3.x and churny). The pipeline is exercised end to end with the
fake, so the orchestration is proven and the real generator drops in at the seam.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from PIL import Image

logger = logging.getLogger("rooted_worker.generator")


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


def _asset_bytes(url: str) -> bytes:
    """Read a genblaze asset's bytes. OpenAI assets are a local file:// the provider already
    downloaded; GMICloud assets are remote https URLs we fetch."""
    if url.startswith("file://"):
        from urllib.parse import unquote, urlparse

        return Path(unquote(urlparse(url).path)).read_bytes()
    import httpx

    resp = httpx.get(url, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


class GenblazeGenerator:
    """Real Genblaze multi-provider image generation.

    GMICloud is the primary provider (Seedream / FLUX et al.) with within-provider
    ``fallback_models`` for model-level MODEL_ERROR retries; OpenAI (gpt-image / DALL-E) is the
    cross-provider backstop when GMICloud fails entirely. Returns the generated image bytes; the
    Rooted pipeline then watermarks, stores to B2, signs, and indexes the asset for recovery.

    Needs the ``genblaze`` extra (genblaze-core + genblaze-gmicloud + genblaze-openai) and provider
    keys. GMICloudImageProvider reads GMI_API_KEY by default, so the key is passed explicitly to
    avoid an env-name mismatch with Rooted's GMI_CLOUD_API_KEY.
    """

    def __init__(
        self,
        gmi_api_key: str,
        *,
        gmi_model: str,
        gmi_fallback_models: list[str] | None = None,
        openai_api_key: str | None = None,
        openai_model: str = "gpt-image-1",
        timeout: float = 180.0,
    ) -> None:
        self._gmi_api_key = gmi_api_key
        self._gmi_model = gmi_model
        self._gmi_fallback_models = gmi_fallback_models or []
        self._openai_api_key = openai_api_key
        self._openai_model = openai_model
        self._timeout = timeout

    def generate(self, prompt: str) -> GenerationResult:
        try:
            return self._generate_gmi(prompt)
        except Exception as exc:  # noqa: BLE001 - any GMICloud failure falls back across providers
            if not self._openai_api_key:
                raise
            logger.warning("GMICloud generation failed (%s); falling back to OpenAI", exc)
            return self._generate_openai(prompt)

    def _generate_gmi(self, prompt: str) -> GenerationResult:
        from genblaze_core import Modality, Pipeline
        from genblaze_gmicloud import GMICloudImageProvider

        result = (
            Pipeline("rooted-gen")
            .step(
                GMICloudImageProvider(api_key=self._gmi_api_key),
                model=self._gmi_model,
                prompt=prompt,
                modality=Modality.IMAGE,
                fallback_models=self._gmi_fallback_models,
            )
            .run(raise_on_failure=True, timeout=self._timeout)
        )
        return self._to_result(result)

    def _generate_openai(self, prompt: str) -> GenerationResult:
        from genblaze_core import Modality, Pipeline
        from genblaze_openai import DalleProvider

        result = (
            Pipeline("rooted-gen-fallback")
            .step(
                DalleProvider(api_key=self._openai_api_key),
                model=self._openai_model,
                prompt=prompt,
                modality=Modality.IMAGE,
            )
            .run(raise_on_failure=True, timeout=self._timeout)
        )
        return self._to_result(result)

    @staticmethod
    def _to_result(result: object) -> GenerationResult:
        step = result.run.steps[0]  # type: ignore[attr-defined]
        data = _asset_bytes(step.assets[0].url)
        image = Image.open(io.BytesIO(data)).convert("RGB")
        return GenerationResult(
            image=image, image_bytes=data, model=step.model, provider=step.provider
        )
