"""Превращение селфи в фури-портрет через OpenAI image edit."""

import base64
import os

from openai import AsyncOpenAI

PROMPT = (
    "Transform the person in this photo into an anthropomorphic furry character. "
    "Pick the animal species that best matches their face, hair and vibe "
    "(fox, wolf, cat, bear, rabbit, etc.). Keep their pose, expression, hairstyle "
    "colours, clothing, accessories and the background recognisable. Soft detailed "
    "fur, expressive eyes, friendly look, high-quality digital illustration, "
    "wholesome and safe for work."
)

MODEL = os.getenv("FURRY_MODEL", "gpt-image-2")
QUALITY = os.getenv("FURRY_QUALITY", "medium")


async def furrify(photo: bytes, client: AsyncOpenAI, species: str | None = None) -> bytes:
    """Принимает байты фото, возвращает PNG фури-версии."""
    prompt = PROMPT
    if species:
        prompt += f" The animal species must be: {species}."
    result = await client.images.edit(
        model=MODEL,
        image=("selfie.jpg", photo, "image/jpeg"),
        prompt=prompt,
        quality=QUALITY,
        size="auto",
    )
    return base64.b64decode(result.data[0].b64_json)
