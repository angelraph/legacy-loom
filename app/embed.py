"""Open embedding model via fastembed (ONNX). Small enough for a 512MB Render instance,
so the laptop and the hosted app produce vectors in the same space."""
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(config.EMBED_MODEL)


def dim() -> int:
    return len(embed(["dimension probe"])[0])


def embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    # bge models retrieve better when the query carries this instruction.
    prefix = "Represent this sentence for searching relevant passages: " if "bge" in config.EMBED_MODEL.lower() else ""
    return embed([prefix + text])[0]
