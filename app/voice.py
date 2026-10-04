"""Read answers aloud with ElevenLabs. Every clip is cached in GridFS so a replay costs nothing."""
import hashlib

import gridfs
import httpx

from . import config, store


def status() -> dict:
    if not config.ELEVENLABS_API_KEY:
        return {"ok": False, "detail": "ELEVENLABS_API_KEY not set"}
    return {"ok": True, "detail": f"voice {config.ELEVENLABS_VOICE_ID}"}


def speak(text: str) -> bytes:
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    text = text.strip()[:2500]
    key = hashlib.sha1(f"{config.ELEVENLABS_VOICE_ID}|{config.ELEVENLABS_TTS_MODEL}|{text}".encode()).hexdigest()
    bucket = store.tts_bucket()
    try:
        return bucket.open_download_stream_by_name(key).read()
    except gridfs.errors.NoFile:
        pass
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
                   params={"output_format": "mp3_44100_128"},
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
                   json={"text": text, "model_id": config.ELEVENLABS_TTS_MODEL,
                         "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}},
                   timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
    bucket.upload_from_stream(key, r.content, metadata={"contentType": "audio/mpeg"})
    return r.content
