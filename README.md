# Legacy Loom

**Live:** https://legacy-loom-ashen.vercel.app  
**App:** https://legacy-loom-ashen.vercel.app/app  
**Docs:** https://legacy-loom-ashen.vercel.app/docs

Legacy Loom keeps one person's voice notes and makes them useful. You drop in the voice notes a friend sends you (the recipes, the stories, the "this is how my mum did it"), and it turns them into a private archive you can ask questions of. Every answer links to the second they said it. Recipes become a printable cookbook, and each page has a QR code that plays the cook explaining the dish.

Open models do the core work: Gemma reads, files and answers, Whisper listens, and an open embedding model searches. Gemma and Whisper can run on a laptop with no GPU, so the voice notes don't have to leave the machine.

## What it does

**Ask.** Gemma picks a tool (search the memories, look up a recipe, read the timeline, or ask the call planner), runs it, and answers only from what came back. Citations are numbered. Click one and the player jumps to that moment in the recording. If nothing in the archive answers the question, it says so and suggests what to ask next time, and you can save that question.

**Archive.** Each note gets a transcript synced to the audio, a title, the people and places mentioned, a year if one was said, and the lines worth keeping word for word.

**Recipes.** Any note where someone explains a dish becomes a recipe card. `/book` renders the whole collection as a print-ready cookbook.

**Timeline.** Memories ordered by when they happened, not when they were recorded.

**Next call.** Some sessions are gold and some are flat. You rate each recording, and TabPFN learns from that small table (hour, weekday, who asked, topic) which slots and topics tend to produce the good ones. It also estimates how long they will talk. A friendship has a few dozen sessions, not a few thousand, and that is the size of data TabPFN was built for.

**Read aloud.** ElevenLabs reads answers out loud. Clips are cached in GridFS.

## How it is built

| Piece | What does it |
| :-- | :-- |
| Language model | Gemma 3 through Ollama on the laptop, or Gemma 3 27B on Google AI Studio for the hosted demo. One env var switches between them. |
| Transcription | faster-whisper on CPU, or ElevenLabs Scribe when local Whisper is not available. Both give word timings. |
| Embeddings | `BAAI/bge-small-en-v1.5` via fastembed (ONNX). Small enough for a 512 MB server, so laptop and server write vectors in the same space. |
| Storage and search | MongoDB Atlas: memos, chunks, sessions, and the audio itself in GridFS. Atlas Vector Search and Atlas Search run side by side, merged with reciprocal rank fusion. |
| Forecasting | TabPFN through the Prior Labs API, with the open `tabpfn` package as an alternative. |
| Server and UI | FastAPI and plain HTML, CSS and JS. No build step. |
| Hosting | Render, from `render.yaml`. |

```
app/
  main.py        routes, audio streaming with byte ranges
  ingest.py      audio to transcript to Gemma filing to chunks to vectors
  transcribe.py  faster-whisper or ElevenLabs Scribe
  extract.py     Gemma turns a transcript into structured JSON
  agent.py       tool routing, grounded answers, citations
  store.py       Atlas, GridFS, hybrid search
  planner.py     TabPFN session planner
  voice.py       ElevenLabs speech
  book.py        printable cookbook with QR codes
public/          landing, app, docs, FAQ and support pages
tabpfn_api/      the planner as its own Vercel function
scripts/setup_atlas.py
```

## Run it on your laptop

You need Python 3.11 or newer, Ollama and a free MongoDB Atlas cluster.

```bash
ollama pull gemma3:4b
python -m venv .venv
.venv\Scripts\activate          # macOS or Linux: source .venv/bin/activate
pip install -r requirements-local.txt
copy .env.example .env          # then fill in MONGODB_URI and any keys you have
python -m scripts.setup_atlas   # creates the vector and text search indexes
uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000/app, go to Settings and enter whose voice it is, then add a recording.

Everything optional stays optional. Without an ElevenLabs key there is no read aloud button. Without a TabPFN token the planner says it is not configured. The app never fakes an answer.

## Deploy on Render

Push the repo to GitHub, then in Render choose New, Blueprint, and point it at the repo. Fill in the secret env vars it asks for (`MONGODB_URI`, `GOOGLE_API_KEY`, `ELEVENLABS_API_KEY`, `TABPFN_TOKEN`, `APP_PASSCODE`, `PUBLIC_BASE_URL`). The hosted version uses Gemma on Google AI Studio and ElevenLabs Scribe, because a free instance cannot hold Whisper or a 4B model in 512 MB. It reads the same Atlas database as your laptop, so notes processed locally show up online too.

Set `APP_PASSCODE` before sharing the link. Visitors can listen and ask, but only someone with the passcode can add, rate or delete.

## Session log CSV

If you already keep track of calls, import them on the Next call page:

```
date,time,duration_min,asked_by,topic,rating
2026-09-12,19:30,14,Ada,jollof,5
```

## Tests

```bash
pytest -q
```

## License

MIT
