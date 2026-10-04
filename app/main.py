"""Legacy Loom web server."""
import csv
import io
import json
import logging
import threading
from datetime import datetime

from bson import ObjectId
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, book, config, embed, ingest, llm, planner, store, transcribe, voice

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Legacy Loom", docs_url=None, redoc_url=None, openapi_url="/api/openapi.json")
WEB = config.ROOT / "public"
if (WEB / "static").is_dir():  # on Vercel the CDN serves public/ and it is not in the function
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

# Vercel caps a request body at 4.5 MB. A WhatsApp voice note of that size is over 30 minutes long.
MAX_UPLOAD = (4 if config.SERVERLESS else 60) * 1024 * 1024
AUDIO_SLICE = 2 * 1024 * 1024


def require_key(x_family_key: str | None = Header(default=None)):
    if config.APP_PASSCODE and x_family_key != config.APP_PASSCODE:
        raise HTTPException(401, "This archive is read only without the passcode.")


def jsonable(doc):
    if isinstance(doc, list):
        return [jsonable(d) for d in doc]
    if isinstance(doc, dict):
        return {k: jsonable(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


@app.exception_handler(store.StoreError)
async def store_error(_: Request, exc: store.StoreError):
    return Response(json.dumps({"detail": str(exc)}), status_code=503, media_type="application/json")


PAGES = {"/": "index.html", "/app": "app.html", "/guide": "guide.html", "/docs": "docs.html", "/faq": "faq.html", "/support": "support.html"}


def _page(name: str):
    return lambda: FileResponse(WEB / name)


# Locally FastAPI serves the pages; on Vercel the CDN does (see vercel.json).
for _path, _name in PAGES.items():
    app.add_api_route(_path, _page(_name), methods=["GET"], response_class=HTMLResponse, include_in_schema=False)


@app.get("/api/status")
def status():
    return {"gemma": llm.status(), "atlas": store.status(), "transcriber": transcribe.status(),
            "tabpfn": planner.status(), "elevenlabs": voice.status(),
            "embedding_model": config.EMBED_MODEL, "read_only": bool(config.APP_PASSCODE),
            "max_upload_mb": MAX_UPLOAD // (1024 * 1024), "serverless": config.SERVERLESS}


@app.post("/api/auth/check")
def auth_check(_=Depends(require_key)):
    return {"ok": True}


# Family

@app.get("/api/family")
def get_family():
    return store.get_family()


@app.put("/api/family", dependencies=[Depends(require_key)])
def put_family(data: dict):
    return store.save_family(data)


# Memos

@app.post("/api/memos", dependencies=[Depends(require_key)])
async def upload_memo(background: BackgroundTasks, file: UploadFile = File(...),
                      recorded_at: str = Form(...), asked_by: str = Form(""), prompt_topic: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"Recordings up to {MAX_UPLOAD // (1024 * 1024)} MB please")
    try:
        when = datetime.fromisoformat(recorded_at)
    except ValueError:
        raise HTTPException(400, "recorded_at must be an ISO date and time")
    mime = file.content_type or "application/octet-stream"
    audio_id = store.put_audio(data, file.filename or "memo", mime)
    memo_id = store.create_memo({
        "filename": file.filename, "audio_id": audio_id, "audio_mime": mime, "recorded_at": when.replace(tzinfo=None),
        "asked_by": asked_by.strip()[:40], "prompt_topic": prompt_topic.strip().lower()[:40],
        "status": "queued", "title": file.filename or "New recording", "rating": None,
    })
    if config.SERVERLESS:
        await run_in_threadpool(ingest.process, memo_id)
        m = store.db().memos.find_one({"_id": memo_id}, {"status": 1, "error": 1})
        return {"id": str(memo_id), "status": m["status"], "error": m.get("error")}
    background.add_task(ingest.process, memo_id)
    return {"id": str(memo_id), "status": "queued"}


@app.get("/api/memos")
def list_memos():
    return jsonable(store.list_memos())


@app.get("/api/memos/{memo_id}")
def get_memo(memo_id: str):
    m = store.get_memo(memo_id)
    if not m:
        raise HTTPException(404, "Memo not found")
    return jsonable(m)


class MemoPatch(BaseModel):
    rating: int | None = None
    title: str | None = None
    year: int | None = None
    recipe: dict | None = None


@app.patch("/api/memos/{memo_id}", dependencies=[Depends(require_key)])
def patch_memo(memo_id: str, patch: MemoPatch):
    fields = patch.model_dump(exclude_unset=True)
    if "rating" in fields and fields["rating"] is not None and not 1 <= fields["rating"] <= 5:
        raise HTTPException(400, "rating is 1 to 5")
    if not fields:
        return {"ok": True}
    mid = store.oid(memo_id)
    store.update_memo(mid, fields)
    if "rating" in fields:
        store.rate_session_for_memo(mid, fields["rating"])
    if "title" in fields:
        store.db().chunks.update_many({"memo_id": mid}, {"$set": {"title": fields["title"]}})
    return {"ok": True}


@app.post("/api/memos/{memo_id}/reprocess", dependencies=[Depends(require_key)])
def reprocess(memo_id: str, background: BackgroundTasks):
    mid = store.oid(memo_id)
    store.update_memo(mid, {"status": "queued", "error": None})
    if config.SERVERLESS:
        ingest.process(mid)
    else:
        background.add_task(ingest.process, mid)
    return {"ok": True}


@app.delete("/api/memos/{memo_id}", dependencies=[Depends(require_key)])
def delete_memo(memo_id: str):
    store.delete_memo(memo_id)
    return {"ok": True}


@app.get("/api/audio/{audio_id}")
def audio(audio_id: str, range: str | None = Header(default=None)):
    """Serve audio with byte ranges so the player can jump straight to a cited second."""
    f = store.get_audio_file(audio_id)
    size = f.length
    mime = (f.metadata or {}).get("contentType", "audio/mpeg")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    if range and range.startswith("bytes="):
        start_s, _, end_s = range[6:].partition("-")
        start = int(start_s) if start_s else 0
        # Answer in slices of at most 2 MB; players ask again for the rest.
        end = min(int(end_s) if end_s else size - 1, size - 1, start + AUDIO_SLICE - 1)
        if start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        f.seek(start)
        body = f.read(end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(body, status_code=206, media_type=mime, headers=headers)
    headers["Content-Length"] = str(size)

    def chunks():
        while piece := f.read(256 * 1024):
            yield piece

    return StreamingResponse(chunks(), media_type=mime, headers=headers)


@app.get("/api/memos/{memo_id}/audio")
def memo_audio(memo_id: str, range: str | None = Header(default=None)):
    m = store.db().memos.find_one({"_id": store.oid(memo_id)}, {"audio_id": 1})
    if not m:
        raise HTTPException(404, "Memo not found")
    return audio(str(m["audio_id"]), range)


# Asking

class Ask(BaseModel):
    question: str
    history: list[dict] = []


@app.post("/api/ask")
def ask(body: Ask):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Ask a question")

    def events():
        try:
            for ev in agent.answer(q[:500], body.history):
                yield f"data: {json.dumps(jsonable(ev), ensure_ascii=False)}\n\n"
        except Exception as e:  # report the real failure to the page
            logging.exception("ask failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:400]})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class Speak(BaseModel):
    text: str


@app.post("/api/tts")
def tts(body: Speak):
    try:
        audio_bytes = voice.speak(body.text)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return Response(audio_bytes, media_type="audio/mpeg")


# Questions saved for the next call

@app.get("/api/questions")
def list_questions():
    return jsonable(list(store.db().questions.find().sort("created_at", -1)))


@app.post("/api/questions", dependencies=[Depends(require_key)])
def add_question(data: dict):
    text = str(data.get("text", "")).strip()[:300]
    if not text:
        raise HTTPException(400, "Empty question")
    qid = store.db().questions.insert_one({"text": text, "done": False, "created_at": store.now()}).inserted_id
    return {"id": str(qid)}


@app.patch("/api/questions/{qid}", dependencies=[Depends(require_key)])
def toggle_question(qid: str, data: dict):
    store.db().questions.update_one({"_id": store.oid(qid)}, {"$set": {"done": bool(data.get("done"))}})
    return {"ok": True}


@app.delete("/api/questions/{qid}", dependencies=[Depends(require_key)])
def delete_question(qid: str):
    store.db().questions.delete_one({"_id": store.oid(qid)})
    return {"ok": True}


# Book and timeline

@app.get("/api/recipes")
def recipes():
    return book.recipes()


@app.get("/book", response_class=HTMLResponse)
def recipe_book():
    return book.render()


@app.get("/api/timeline")
def timeline():
    memos = [m for m in store.list_memos() if m.get("status") == "ready"]
    memos.sort(key=lambda m: (m.get("year") is None, m.get("year") or 0, m.get("recorded_at")))
    return jsonable(memos)


# Planner

@app.get("/api/sessions")
def sessions():
    return jsonable(store.list_sessions())


@app.post("/api/sessions/import", dependencies=[Depends(require_key)])
async def import_sessions(file: UploadFile = File(...)):
    """CSV with columns: date, time, duration_min, asked_by, topic, rating (1 to 5)."""
    text = (await file.read()).decode("utf-8-sig")
    rows, problems = [], []
    for i, r in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        try:
            when = datetime.fromisoformat(f"{r['date']}T{r.get('time') or '12:00'}")
            rating = int(r["rating"]) if r.get("rating") else None
            if rating is not None and not 1 <= rating <= 5:
                raise ValueError("rating must be 1 to 5")
            rows.append({"when": when, "hour": when.hour, "weekday": when.weekday(),
                         "duration_min": float(r["duration_min"]) if r.get("duration_min") else None,
                         "asked_by": r.get("asked_by") or "unknown", "topic": (r.get("topic") or "general").lower(),
                         "rating": rating, "wpm": None, "pause_ratio": None, "source": "import"})
        except (KeyError, ValueError) as e:
            problems.append(f"line {i}: {e}")
    added = store.insert_sessions(rows)
    return {"added": added, "problems": problems[:20]}


@app.delete("/api/sessions/{sid}", dependencies=[Depends(require_key)])
def delete_session(sid: str):
    store.db().sessions.delete_one({"_id": store.oid(sid), "source": "import"})
    return {"ok": True}


@app.get("/api/planner")
def plan(asked_by: str | None = None):
    try:
        return jsonable(planner.forecast(asked_by=asked_by))
    except Exception as e:
        logging.exception("planner failed")
        raise HTTPException(502, f"TabPFN call failed: {e}")


@app.on_event("startup")
def warm():
    # Load the embedding model once so the first question is not slow.
    try:
        embed.dim()
    except Exception:
        logging.exception("embedding model failed to load")
    if config.MONGODB_URI and not config.SERVERLESS:
        threading.Thread(target=ingest.resume_pending, daemon=True).start()
