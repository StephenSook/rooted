"""GenblazeGenerator: the network-free logic (cross-provider fallback + asset-byte reading).

The real provider calls (GMICloud, OpenAI) are exercised by an opt-in live smoke that costs money
and needs keys, not here. genblaze is imported lazily inside the provider methods, so these tests
run without the genblaze extra installed.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest
from PIL import Image

from rooted_worker.generator import GenblazeGenerator, GenerationResult, _asset_bytes


def _png_bytes(seed: int) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 256, (32, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def _result(model: str, provider: str) -> GenerationResult:
    return GenerationResult(
        image=Image.new("RGB", (8, 8)), image_bytes=b"x", model=model, provider=provider
    )


def test_asset_bytes_reads_file_url_in_tempdir() -> None:
    data = _png_bytes(1)
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    p = Path(name)
    p.write_bytes(data)
    try:
        assert _asset_bytes(f"file://{quote(str(p))}") == data
    finally:
        p.unlink(missing_ok=True)


def test_asset_bytes_rejects_file_outside_tempdir() -> None:
    with pytest.raises(ValueError, match="outside the temp dir"):
        _asset_bytes("file:///etc/hosts")


def test_asset_bytes_rejects_non_https_scheme() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _asset_bytes("ftp://example.com/x.png")


def test_asset_bytes_rejects_internal_host() -> None:
    with pytest.raises(ValueError, match="internal address|cannot resolve"):
        _asset_bytes("https://127.0.0.1/x.png")


def test_generate_uses_gmi_when_it_succeeds() -> None:
    gen = GenblazeGenerator("gmi-key", gmi_model="seedream", openai_api_key="oai-key")
    sentinel = _result("seedream", "gmicloud-image")
    gen._generate_gmi = lambda prompt: sentinel  # type: ignore[method-assign]

    def _should_not_call(prompt: str) -> GenerationResult:
        raise AssertionError("OpenAI must not be called when GMICloud succeeds")

    gen._generate_openai = _should_not_call  # type: ignore[method-assign]
    assert gen.generate("a fox") is sentinel


def test_generate_falls_back_to_openai_when_gmi_fails() -> None:
    gen = GenblazeGenerator("gmi-key", gmi_model="seedream", openai_api_key="oai-key")
    sentinel = _result("gpt-image-1", "openai-dalle")

    def _boom(prompt: str) -> GenerationResult:
        raise RuntimeError("GMICloud down")

    gen._generate_gmi = _boom  # type: ignore[method-assign]
    gen._generate_openai = lambda prompt: sentinel  # type: ignore[method-assign]
    assert gen.generate("a fox") is sentinel


def test_generate_reraises_when_no_openai_key() -> None:
    gen = GenblazeGenerator("gmi-key", gmi_model="seedream", openai_api_key=None)

    def _boom(prompt: str) -> GenerationResult:
        raise RuntimeError("GMICloud down")

    gen._generate_gmi = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="GMICloud down"):
        gen.generate("a fox")
