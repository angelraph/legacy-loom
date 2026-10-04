"""Speech to text with word timings.

Local path: faster-whisper (open Whisper weights, runs on CPU, audio never leaves the machine).
Hosted path: ElevenLabs Scribe, which also covers languages Whisper handles poorly.
Both return the same shape so the rest of the app does not care which ran.
"""
import os
import tempfile
from functools import lru_cache

import httpx

from . import config

SENTENCE_END = (".", "?", "!")


def _local_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def backend() -> str | None:
    if config.TRANSCRIBER == "local":
        return "local" if _local_available() else None
    if config.TRANSCRIBER == "elevenlabs":
        return "elevenlabs" if config.ELEVENLABS_API_KEY else None
    if _local_available():
        return "local"
    return "elevenlabs" if config.ELEVENLABS_API_KEY else None


def status() -> dict:
    b = backend()
    if b == "local":
        return {"ok": True, "backend": "faster-whisper", "detail": f"model {config.WHISPER_MODEL}, on this machine"}
    if b == "elevenlabs":
        return {"ok": True, "backend": "elevenlabs-scribe", "detail": config.ELEVENLABS_STT_MODEL}
    return {"ok": False, "backend": None, "detail": "install faster-whisper or set ELEVENLABS_API_KEY"}


@lru_cache(maxsize=1)
def _whisper():
    from faster_whisper import WhisperModel
    return WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")


def _local(path: str) -> dict:
    segments, info = _whisper().transcribe(path, language=config.WHISPER_LANGUAGE, word_timestamps=True,
                                           vad_filter=True, beam_size=5)
    segs, words = [], []
    for s in segments:
        segs.append({"start": round(float(s.start), 2), "end": round(float(s.end), 2), "text": s.text.strip()})
        for w in s.words or []:
            words.append({"start": float(w.start), "end": float(w.end), "text": w.word.strip()})
    return {"language": info.language, "duration": round(float(info.duration), 2), "segments": segs, "words": words}


def _group_words(words: list[dict]) -> list[dict]:
    """Turn a flat word list into readable caption-sized segments."""
    segs, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        span = cur[-1]["end"] - cur[0]["start"]
        gap = (nxt["start"] - w["end"]) if nxt else 0
        if nxt is None or gap > 1.0 or span > 18 or (w["text"].endswith(SENTENCE_END) and span > 4):
            segs.append({"start": round(cur[0]["start"], 2), "end": round(cur[-1]["end"], 2),
                         "text": " ".join(x["text"] for x in cur).strip()})
            cur = []
    return segs


def _elevenlabs(path: str, filename: str) -> dict:
    with open(path, "rb") as f:
        r = httpx.post("https://api.elevenlabs.io/v1/speech-to-text",
                       headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                       data={"model_id": config.ELEVENLABS_STT_MODEL, "timestamps_granularity": "word",
                             "tag_audio_events": "false"},
                       files={"file": (filename, f)}, timeout=600)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs Scribe error {r.status_code}: {r.text[:300]}")
    data = r.json()
    words = [{"start": w["start"], "end": w["end"], "text": w["text"].strip()}
             for w in data.get("words", []) if w.get("type") == "word" and w.get("text", "").strip()]
    duration = words[-1]["end"] if words else 0
    return {"language": data.get("language_code"), "duration": round(duration, 2),
            "segments": _group_words(words), "words": words}


def speech_features(result: dict) -> dict:
    """How she spoke: pace and how much of the recording was silence."""
    words, duration = result["words"], result["duration"] or 0
    if not words or duration <= 0:
        return {"wpm": None, "pause_ratio": None}
    spoken = sum(max(0.0, w["end"] - w["start"]) for w in words)
    return {"wpm": round(len(words) / (duration / 60), 1),
            "pause_ratio": round(max(0.0, 1 - spoken / duration), 3)}


def transcribe(audio: bytes, filename: str) -> dict:
    b = backend()
    if b is None:
        raise RuntimeError("No transcriber configured. Install faster-whisper or set ELEVENLABS_API_KEY.")
    suffix = os.path.splitext(filename)[1] or ".audio"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        result = _local(path) if b == "local" else _elevenlabs(path, filename)
    finally:
        os.remove(path)
    result["backend"] = b
    result["text"] = " ".join(s["text"] for s in result["segments"]).strip()
    result["speech"] = speech_features(result)
    return result
