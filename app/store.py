"""MongoDB Atlas: memos, searchable chunks, recording sessions and the audio itself (GridFS)."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import gridfs
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from . import config

RRF_K = 60

# Two spaces share one database. "main" is the friend's archive, protected by the passcode.
# "try" is an open sandbox for visitors and judges; what they add there is cleared after a day.
MAIN, TRY = "main", "try"
SPACES = (MAIN, TRY)
TRY_TTL = timedelta(hours=24)


def clean_space(value: str | None) -> str:
    return value if value in SPACES else MAIN


class StoreError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def db():
    if not config.MONGODB_URI:
        raise StoreError("MONGODB_URI is not set")
    client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=8000, appname="legacy-loom")
    return client[config.MONGODB_DB]


def audio_bucket() -> gridfs.GridFSBucket:
    return gridfs.GridFSBucket(db(), bucket_name="audio")


def tts_bucket() -> gridfs.GridFSBucket:
    return gridfs.GridFSBucket(db(), bucket_name="tts")


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as e:
        raise StoreError(f"Bad id: {value}") from e


# Setup

def search_index_status() -> dict:
    try:
        found = {i["name"]: i.get("status", "UNKNOWN") for i in db().chunks.list_search_indexes()}
    except PyMongoError as e:
        return {"error": str(e)}
    return {config.VECTOR_INDEX: found.get(config.VECTOR_INDEX, "MISSING"),
            config.TEXT_INDEX: found.get(config.TEXT_INDEX, "MISSING")}


def status() -> dict:
    try:
        db().command("ping")
    except (PyMongoError, StoreError) as e:
        return {"ok": False, "detail": str(e)}
    idx = search_index_status()
    ready = all(v == "READY" for v in idx.values()) if "error" not in idx else False
    return {"ok": True, "detail": "connected", "search_indexes": idx, "search_ready": ready}


def ensure_indexes(dimensions: int) -> list[str]:
    d = db()
    d.memos.create_index([("created_at", DESCENDING)])
    d.chunks.create_index([("memo_id", ASCENDING), ("idx", ASCENDING)])
    d.sessions.create_index([("space", ASCENDING), ("when", ASCENDING)])
    d.memos.create_index([("space", ASCENDING), ("recorded_at", DESCENDING)])
    if "chunks" not in d.list_collection_names():
        d.create_collection("chunks")
    existing = {i["name"] for i in d.chunks.list_search_indexes()}
    made = []
    if config.VECTOR_INDEX not in existing:
        d.chunks.create_search_index(SearchIndexModel(
            name=config.VECTOR_INDEX, type="vectorSearch",
            definition={"fields": [
                {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
                {"type": "filter", "path": "kind"},
                {"type": "filter", "path": "space"},
            ]}))
        made.append(config.VECTOR_INDEX)
    if config.TEXT_INDEX not in existing:
        d.chunks.create_search_index(SearchIndexModel(
            name=config.TEXT_INDEX, type="search",
            definition={"mappings": {"dynamic": False, "fields": {
                "text": {"type": "string", "analyzer": "lucene.standard"},
                "title": {"type": "string", "analyzer": "lucene.standard"},
                "kind": {"type": "token"},
                "space": {"type": "token"},
            }}}))
        made.append(config.TEXT_INDEX)
    return made


def add_space_to_indexes(dimensions: int) -> None:
    """Update search indexes made before spaces existed so they can filter on space."""
    d = db()
    d.chunks.update_search_index(config.VECTOR_INDEX, {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
        {"type": "filter", "path": "kind"},
        {"type": "filter", "path": "space"},
    ]})
    d.chunks.update_search_index(config.TEXT_INDEX, {"mappings": {"dynamic": False, "fields": {
        "text": {"type": "string", "analyzer": "lucene.standard"},
        "title": {"type": "string", "analyzer": "lucene.standard"},
        "kind": {"type": "token"},
        "space": {"type": "token"},
    }}})


# Family settings

def _settings_id(space: str) -> str:
    return "family" if space == MAIN else f"family:{space}"


def get_family(space: str = MAIN) -> dict:
    doc = db().settings.find_one({"_id": _settings_id(space)}) or {}
    return {"elder_name": doc.get("elder_name", ""), "family_name": doc.get("family_name", ""),
            "askers": doc.get("askers", []), "pronoun": doc.get("pronoun", "she")}


PRONOUNS = {"she": ("she", "her", "her"), "he": ("he", "him", "his"), "they": ("they", "them", "their")}


def pronouns(fam: dict) -> dict:
    sub, obj, pos = PRONOUNS.get(fam.get("pronoun", "she"), PRONOUNS["she"])
    return {"sub": sub, "obj": obj, "pos": pos}


def save_family(data: dict, space: str = MAIN) -> dict:
    clean = {"elder_name": str(data.get("elder_name", "")).strip()[:60],
             "family_name": str(data.get("family_name", "")).strip()[:60],
             "askers": [str(a).strip()[:40] for a in data.get("askers", []) if str(a).strip()][:12],
             "pronoun": data.get("pronoun") if data.get("pronoun") in PRONOUNS else "she"}
    db().settings.update_one({"_id": _settings_id(space)}, {"$set": clean}, upsert=True)
    return clean


# Memos

def put_audio(data: bytes, filename: str, mime: str) -> ObjectId:
    return audio_bucket().upload_from_stream(filename, data, metadata={"contentType": mime})


def get_audio_file(audio_id: str):
    try:
        return audio_bucket().open_download_stream(oid(audio_id))
    except gridfs.errors.NoFile as e:
        raise StoreError("Audio not found") from e


def read_audio(audio_id) -> bytes:
    return audio_bucket().open_download_stream(audio_id).read()


def create_memo(doc: dict) -> ObjectId:
    doc.setdefault("created_at", now())
    return db().memos.insert_one(doc).inserted_id


def update_memo(memo_id: ObjectId, fields: dict) -> None:
    db().memos.update_one({"_id": memo_id}, {"$set": fields})


LIST_FIELDS = {"segments": 0, "words": 0, "transcript": 0}


def list_memos(space: str = MAIN) -> list[dict]:
    return list(db().memos.find({"space": space}, LIST_FIELDS).sort("recorded_at", DESCENDING))


def purge_expired_try() -> int:
    """Clear sandbox memos older than a day, with their audio, passages and sessions."""
    cutoff = now() - TRY_TTL
    old = list(db().memos.find({"space": TRY, "created_at": {"$lt": cutoff}}, {"_id": 1}))
    for m in old:
        delete_memo(str(m["_id"]))
    db().sessions.delete_many({"space": TRY, "source": "import", "imported_at": {"$lt": cutoff}})
    db().questions.delete_many({"space": TRY, "created_at": {"$lt": cutoff}})
    return len(old)


def memo_space(memo_id: str) -> str | None:
    m = db().memos.find_one({"_id": oid(memo_id)}, {"space": 1})
    return m.get("space", MAIN) if m else None


def get_memo(memo_id: str) -> dict | None:
    return db().memos.find_one({"_id": oid(memo_id)}, {"words": 0})


def delete_memo(memo_id: str) -> None:
    m = db().memos.find_one({"_id": oid(memo_id)})
    if not m:
        raise StoreError("Memo not found")
    db().chunks.delete_many({"memo_id": m["_id"]})
    db().sessions.delete_many({"memo_id": m["_id"]})
    if m.get("audio_id"):
        try:
            audio_bucket().delete(m["audio_id"])
        except gridfs.errors.NoFile:
            pass
    db().memos.delete_one({"_id": m["_id"]})


def chunk_segments(segments: list[dict], target: float = 45.0) -> list[dict]:
    """Group transcript segments into about 45 second passages that keep their timestamps."""
    chunks, cur = [], []
    for s in segments:
        cur.append(s)
        if cur[-1]["end"] - cur[0]["start"] >= target:
            chunks.append(cur)
            cur = []
    if cur:
        if chunks and cur[-1]["end"] - cur[0]["start"] < target / 3:
            chunks[-1].extend(cur)
        else:
            chunks.append(cur)
    return [{"start": c[0]["start"], "end": c[-1]["end"], "text": " ".join(s["text"] for s in c).strip()}
            for c in chunks if c]


def replace_chunks(memo_id: ObjectId, title: str, kind: str, chunks: list[dict], vectors: list[list[float]],
                   space: str = MAIN) -> None:
    db().chunks.delete_many({"memo_id": memo_id})
    if chunks:
        db().chunks.insert_many([{"memo_id": memo_id, "space": space, "idx": i, "title": title, "kind": kind,
                                  "start": c["start"], "end": c["end"], "text": c["text"],
                                  "overview": bool(c.get("overview")), "embedding": v}
                                 for i, (c, v) in enumerate(zip(chunks, vectors))])


# Search

def _vector(query_vec: list[float], k: int, kind: str | None, space: str) -> list[dict]:
    stage = {"index": config.VECTOR_INDEX, "path": "embedding", "queryVector": query_vec,
             "numCandidates": max(100, k * 15), "limit": k,
             "filter": {"$and": [{"space": space}, {"kind": kind}]} if kind else {"space": space}}
    return list(db().chunks.aggregate([{"$vectorSearch": stage}, {"$project": {"embedding": 0}}]))


def _text(query: str, k: int, kind: str | None, space: str) -> list[dict]:
    clause = {"text": {"query": query, "path": ["text", "title"], "fuzzy": {"maxEdits": 1}}}
    filters = [{"equals": {"path": "space", "value": space}}]
    if kind:
        filters.append({"equals": {"path": "kind", "value": kind}})
    search = {"index": config.TEXT_INDEX, "compound": {"must": [clause], "filter": filters}}
    return list(db().chunks.aggregate([{"$search": search}, {"$limit": k}, {"$project": {"embedding": 0}}]))


def rrf(result_lists: dict[str, list[dict]], k: int) -> list[dict]:
    """Reciprocal rank fusion: reward chunks that rank well in either list, more if in both."""
    scored: dict = {}
    for source, results in result_lists.items():
        for rank, doc in enumerate(results):
            entry = scored.setdefault(doc["_id"], {"doc": doc, "score": 0.0, "via": []})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
            entry["via"].append(source)
    ranked = sorted(scored.values(), key=lambda e: e["score"], reverse=True)[:k]
    return [{**e["doc"], "score": round(e["score"], 5), "via": e["via"]} for e in ranked]


def hybrid_search(query: str, query_vec: list[float], k: int = 6, kind: str | None = None, space: str = MAIN) -> dict:
    lists, errors = {}, []
    try:
        lists["vector"] = _vector(query_vec, k * 2, kind, space)
    except OperationFailure as e:
        errors.append(f"vector: {e.details.get('errmsg', str(e)) if e.details else e}")
    try:
        lists["text"] = _text(query, k * 2, kind, space)
    except OperationFailure as e:
        errors.append(f"text: {e.details.get('errmsg', str(e)) if e.details else e}")
    if not lists:
        raise StoreError("Atlas Search is not available: " + "; ".join(errors))
    hits = rrf(lists, k)
    for h in hits:
        h["memo_id"] = str(h["memo_id"])
        h["_id"] = str(h["_id"])
    return {"hits": hits, "modes": list(lists), "errors": errors}


# Sessions (one row per recording session, used by the planner)

def upsert_session_for_memo(memo_id: ObjectId, row: dict) -> None:
    db().sessions.update_one({"memo_id": memo_id}, {"$set": {**row, "memo_id": memo_id, "source": "memo"}}, upsert=True)


def insert_sessions(rows: list[dict]) -> int:
    if not rows:
        return 0
    return len(db().sessions.insert_many(rows).inserted_ids)


def list_sessions(space: str = MAIN) -> list[dict]:
    return list(db().sessions.find({"space": space}).sort("when", ASCENDING))


def rate_session_for_memo(memo_id: ObjectId, rating: int | None) -> None:
    db().sessions.update_one({"memo_id": memo_id}, {"$set": {"rating": rating}})
