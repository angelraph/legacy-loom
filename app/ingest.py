"""From a raw voice memo to a filed, searchable memory."""
import logging
import threading
import traceback

from bson import ObjectId

from . import embed, extract, store, transcribe

log = logging.getLogger("legacy-loom.ingest")
# One memo at a time: Whisper and Gemma already use every CPU core.
_lock = threading.Lock()
PENDING = ("queued", "transcribing", "reading", "indexing")


def _set(memo_id: ObjectId, **fields) -> None:
    store.update_memo(memo_id, fields)


def process(memo_id: ObjectId) -> None:
    with _lock:
        _process(memo_id)


def resume_pending() -> None:
    """Finish any memo that was mid-pipeline when the server stopped."""
    for m in list(store.db().memos.find({"status": {"$in": list(PENDING)}}, {"_id": 1})):
        process(m["_id"])


def _process(memo_id: ObjectId) -> None:
    memo = store.db().memos.find_one({"_id": memo_id})
    if not memo:
        return
    try:
        _set(memo_id, status="transcribing", error=None)
        audio = store.read_audio(memo["audio_id"])
        heard = transcribe.transcribe(audio, memo.get("filename") or "memo.webm")
        _set(memo_id, status="reading", transcript=heard["text"], segments=heard["segments"],
             words=heard["words"], language=heard["language"], duration=heard["duration"],
             speech=heard["speech"], transcriber=heard["backend"])
        if not heard["text"]:
            raise RuntimeError("No speech was found in this recording.")

        space = memo.get("space", store.MAIN)
        fam = store.get_family(space)
        info = extract.extract(heard["text"], fam.get("elder_name") or "the speaker", store.pronouns(fam))
        _set(memo_id, status="indexing", **info)

        passages = store.chunk_segments(heard["segments"])
        # One English overview passage per memo, so a question in English can find a memo told in another language.
        overview = " ".join([info["summary"], *info["quotes"]]).strip()
        if overview:
            passages.insert(0, {"start": 0.0, "end": heard["duration"], "text": overview, "overview": True})
        vectors = embed.embed([f"{info['title']}\n{p['text']}" for p in passages])
        store.replace_chunks(memo_id, info["title"], info["kind"], passages, vectors, space)

        when = memo["recorded_at"]
        store.upsert_session_for_memo(memo_id, {
            "when": when, "hour": when.hour, "weekday": when.weekday(),
            "duration_min": round(heard["duration"] / 60, 2),
            "wpm": heard["speech"]["wpm"], "pause_ratio": heard["speech"]["pause_ratio"],
            "asked_by": memo.get("asked_by") or "unknown",
            "topic": (memo.get("prompt_topic") or (info["topics"][0] if info["topics"] else "general")).lower(),
            "rating": memo.get("rating"), "space": space,
        })
        _set(memo_id, status="ready")
    except Exception as e:  # surface every failure on the memo card instead of hiding it
        log.error("ingest failed for %s\n%s", memo_id, traceback.format_exc())
        _set(memo_id, status="error", error=str(e)[:500])
