from __future__ import annotations

import io

from openai import OpenAI

from app.config import settings


class PermanentJobError(RuntimeError):
    """Non-retryable job failure (e.g. missing STT API key)."""


def transcribe_bytes(data: bytes, content_type: str | None, filename: str) -> tuple[str, str]:
    if content_type and content_type.startswith("text/"):
        return data.decode("utf-8"), "passthrough"

    provider = settings.stt_provider
    if provider != "heuristic":
        if provider in ("auto", "groq") and settings.groq_api_key:
            text = _whisper(
                OpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1"),
                "whisper-large-v3",
                data,
                filename,
            )
            return text, "groq/whisper-large-v3"
        if provider in ("auto", "openai") and settings.openai_api_key:
            text = _whisper(OpenAI(api_key=settings.openai_api_key), "whisper-1", data, filename)
            return text, "openai/whisper-1"

    raise PermanentJobError(
        "No STT provider configured for raw audio. Add GROQ_API_KEY or OPENAI_API_KEY in .env, or use a fixture scenario."
    )


def _whisper(client: OpenAI, model: str, data: bytes, filename: str) -> str:
    buf = io.BytesIO(data)
    buf.name = filename
    resp = client.audio.transcriptions.create(model=model, file=buf)
    return resp.text
