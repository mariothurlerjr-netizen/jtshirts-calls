"""OpenAI Whisper transcription."""
import logging
from pathlib import Path

import requests

from . import config

log = logging.getLogger("whisper")


def transcribe(mp3_path: Path) -> str:
    """Transcribe an MP3 file via OpenAI Whisper. Returns plain text."""
    with open(mp3_path, "rb") as f:
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            files={"file": f},
            data={"model": "whisper-1"},
            timeout=180,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Whisper failed [{r.status_code}]: {r.text[:300]}")
    return r.json().get("text", "")
