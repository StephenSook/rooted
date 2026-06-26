"""Generation step: Genblaze multi-provider, behind a Protocol with a deterministic fake for tests.

FakeGenerator is the network-free stand-in the pipeline tests use. GenblazeGenerator is the real
multi-provider generator (GMICloud primary, OpenAI cross-provider fallback) used when the genblaze
extra and provider keys are present. Asset bytes are read defensively: a file:// asset is bounded to
the temp dir the provider writes to, and an https asset is fetched over https only, refusing
internal addresses and redirects, with a size cap.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import logging
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

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


_MAX_ASSET_BYTES = 50 * 1024 * 1024  # cap on a fetched/read genblaze asset


def _reject_internal_host(host: str | None) -> None:
    """SSRF guard: refuse a host that resolves to a private, loopback, or reserved address."""
    if not host:
        raise ValueError("genblaze asset URL has no host")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ValueError(f"cannot resolve genblaze asset host {host!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError(f"refusing to fetch a genblaze asset from internal address {ip}")


def _asset_bytes(url: str) -> bytes:
    """Read a genblaze asset's bytes, failing closed on an untrusted URL.

    OpenAI assets are a local file:// the provider already downloaded (bounded to the temp dir it
    writes to); GMICloud assets are remote URLs fetched over https only, refusing internal
    addresses (SSRF) and redirects, with a size cap. The asset URL comes from the provider
    response, so this is defense in depth rather than a user-facing input path.
    """
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(unquote(parsed.path)).resolve()
        tmp_root = Path(tempfile.gettempdir()).resolve()
        if not path.is_relative_to(tmp_root):
            raise ValueError(f"refusing to read a genblaze asset outside the temp dir: {path}")
        data = path.read_bytes()
        if len(data) > _MAX_ASSET_BYTES:
            raise ValueError("genblaze asset exceeds the size cap")
        return data
    if parsed.scheme != "https":
        raise ValueError(f"unsupported genblaze asset URL scheme: {parsed.scheme!r}")
    _reject_internal_host(parsed.hostname)
    import httpx

    chunks: list[bytes] = []
    total = 0
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=False) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > _MAX_ASSET_BYTES:
                raise ValueError("genblaze asset exceeds the size cap")
            chunks.append(chunk)
    return b"".join(chunks)


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
