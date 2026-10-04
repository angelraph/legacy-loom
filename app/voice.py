"""Read answers aloud with ElevenLabs. Every clip is cached in GridFS so a replay costs nothing.

The free plan has a monthly character budget, so new clips are rationed: a few per visitor per day
and a daily ceiling for everyone together. Cached clips and the cook's own recordings are never limited.
"""
import hashlib
from datetime import datetime, timezone

import gridfs
import httpx

from . import config, store


class QuotaReached(RuntimeError):
    pass


def status() -> dict:
    if not config.ELEVENLABS_API_KEY:
        return {"ok": False, "detail": "ELEVENLABS_API_KEY not set"}
    return {"ok": True, "detail": f"voice {config.ELEVENLABS_VOICE_ID}"}


def _key(text: str) -> str:
    return hashlib.sha1(f"{config.ELEVENLABS_VOICE_ID}|{config.ELEVENLABS_TTS_MODEL}|{text}".encode()).hexdigest()


def _check_quota(visitor: str) -> tuple[str, str]:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mine, everyone = f"{day}:{visitor}", f"{day}:all"
    usage = store.db().tts_usage
    used_mine = (usage.find_one({"_id": mine}) or {}).get("n", 0)
    used_all = (usage.find_one({"_id": everyone}) or {}).get("n", 0)
    if used_all >= config.TTS_PER_DAY:
        raise QuotaReached("Read aloud has reached today's limit for everyone. Tap any number in the answer to hear the real voice instead.")
    if used_mine >= config.TTS_PER_VISITOR:
        raise QuotaReached(f"You've used today's {config.TTS_PER_VISITOR} read alouds. Tap any number in the answer to hear the real voice instead.")
    return mine, everyone


def speak(text: str, visitor: str = "anonymous", unlimited: bool = False) -> bytes:
    if not config.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    text = text.strip()[:2500]
    key = _key(text)
    bucket = store.tts_bucket()
    try:
        return bucket.open_download_stream_by_name(key).read()
    except gridfs.errors.NoFile:
        pass
    counters = None if unlimited else _check_quota(visitor)
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}",
                   params={"output_format": "mp3_44100_128"},
                   headers={"xi-api-key": config.ELEVENLABS_API_KEY, "accept": "audio/mpeg"},
                   json={"text": text, "model_id": config.ELEVENLABS_TTS_MODEL,
                         "voice_settings": {"stability": 0.55, "similarity_boost": 0.75}},
                   timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text[:300]}")
    bucket.upload_from_stream(key, r.content, metadata={"contentType": "audio/mpeg"})
    if counters:
        for c in counters:
            store.db().tts_usage.update_one({"_id": c}, {"$inc": {"n": 1}}, upsert=True)
    return r.content
