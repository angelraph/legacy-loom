"""Settings loaded from the environment (and a local .env file when present)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Gemma. "ollama" runs it on this machine, "google" uses Google AI Studio.
LLM_PROVIDER = _get("LLM_PROVIDER", "ollama").lower()
OLLAMA_URL = _get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = _get("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_NUM_CTX = int(_get("OLLAMA_NUM_CTX", "8192"))
GOOGLE_API_KEY = _get("GOOGLE_API_KEY")
GOOGLE_MODEL = _get("GOOGLE_MODEL", "gemma-4-26b-a4b-it")

# Storage
MONGODB_URI = _get("MONGODB_URI")
MONGODB_DB = _get("MONGODB_DB", "legacyloom")

# Speech
TRANSCRIBER = _get("TRANSCRIBER", "auto").lower()  # auto | local | elevenlabs
WHISPER_MODEL = _get("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = _get("WHISPER_LANGUAGE") or None
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
ELEVENLABS_TTS_MODEL = _get("ELEVENLABS_TTS_MODEL", "eleven_multilingual_v2")
ELEVENLABS_STT_MODEL = _get("ELEVENLABS_STT_MODEL", "scribe_v1")

# Retrieval
EMBED_MODEL = _get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
VECTOR_INDEX = "chunks_vector"
TEXT_INDEX = "chunks_text"

# TabPFN
TABPFN_TOKEN = _get("TABPFN_TOKEN")
# On Vercel the planner runs as its own function (see tabpfn_api/).
PLANNER_URL = _get("PLANNER_URL")
PLANNER_MIN_ROWS = int(_get("PLANNER_MIN_ROWS", "8"))

# Read aloud budget on the ElevenLabs free plan: new clips per visitor per day, and for everyone per day.
TTS_PER_VISITOR = int(_get("TTS_PER_VISITOR", "3"))
TTS_PER_DAY = int(_get("TTS_PER_DAY", "40"))

# Serverless hosts (Vercel) freeze a function once it has replied, so work happens inside the request.
SERVERLESS = bool(os.getenv("VERCEL"))
if SERVERLESS:
    # Only /tmp is writable there; model files are cached in it between warm invocations.
    os.environ.setdefault("HF_HOME", "/tmp/hf")
    os.environ.setdefault("FASTEMBED_CACHE_PATH", "/tmp/fastembed")

# App
APP_PASSCODE = _get("APP_PASSCODE")  # when set, anything that changes data needs it
PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
