import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from furry import furrify  # noqa: E402


class FakeImages:
    def __init__(self):
        self.kwargs = None

    async def edit(self, **kwargs):
        self.kwargs = kwargs
        png = base64.b64encode(b"PNGDATA").decode()
        return SimpleNamespace(data=[SimpleNamespace(b64_json=png)])


def test_furrify_sends_photo_and_decodes_result():
    images = FakeImages()
    client = SimpleNamespace(images=images)
    out = asyncio.run(furrify(b"selfie", client, species="волк"))
    assert out == b"PNGDATA"
    assert images.kwargs["image"][1] == b"selfie"
    assert "волк" in images.kwargs["prompt"]


def test_furrify_without_species_lets_model_choose():
    images = FakeImages()
    asyncio.run(furrify(b"x", SimpleNamespace(images=images)))
    assert "must be" not in images.kwargs["prompt"]
