"""Gemma reads a transcript and files it: what kind of memory it is, who is in it, when it happened."""
from . import llm

KINDS = ("story", "recipe", "advice", "song", "other")

PROMPT = """You are helping the people close to {elder} keep {elder}'s voice notes. Read the transcript and file it.

Return only a JSON object with these keys:
"title": a short warm title in English, like a chapter name (max 8 words)
"kind": one of "story", "recipe", "advice", "song", "other"
"summary": 2 or 3 plain English sentences about what {elder} says (refer to {elder} as {sub}/{obj})
"people": names or relations mentioned (e.g. "her mother", "Uncle Emeka")
"places": places mentioned
"year": the year the events happened as an integer, or null if not said or clearly implied
"era": a decade or life stage like "1960s" or "her childhood", or null
"topics": 1 to 4 lowercase topic words (e.g. "farming", "wedding", "cooking", "school")
"quotes": up to 3 short lines {elder} actually said that the people who love them would want to keep, copied exactly from the transcript
"recipe": null unless {elder} explains how to cook something. If so: {{"name": str, "serves": str or null, "ingredients": [str], "steps": [str], "tips": [str]}}

Use only what is in the transcript. Do not invent names, years or ingredients. If the transcript is not in English, still write title, summary, topics and recipe in English, but copy quotes in the original language.

Transcript:
\"\"\"{transcript}\"\"\""""

RECIPE_PROMPT = """{elder} explains how to cook something in this transcript. Write it down as a recipe card.
Return only JSON: {{"name": str, "serves": str or null, "ingredients": [str], "steps": [str], "tips": [str]}}
ingredients: every ingredient {elder} names, with amounts if said. steps: what to do, in order, in plain English.
tips: little warnings or tricks {elder} gives. Use only what is in the transcript.

Transcript:
\"\"\"{transcript}\"\"\""""

MAX_CHARS = 14000


def _str_list(v, limit=12) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if str(x).strip()][:limit]


def normalize(raw: dict) -> dict:
    kind = str(raw.get("kind", "other")).lower().strip()
    year = raw.get("year")
    try:
        year = int(year) if year not in (None, "", "null") else None
        if year is not None and not (1850 <= year <= 2100):
            year = None
    except (TypeError, ValueError):
        year = None
    recipe = raw.get("recipe")
    if isinstance(recipe, dict) and (_str_list(recipe.get("ingredients")) or _str_list(recipe.get("steps"))):
        recipe = {"name": str(recipe.get("name") or raw.get("title") or "Recipe").strip(),
                  "serves": (str(recipe["serves"]).strip() if recipe.get("serves") else None),
                  "ingredients": _str_list(recipe.get("ingredients"), 40),
                  "steps": _str_list(recipe.get("steps"), 40),
                  "tips": _str_list(recipe.get("tips"), 10)}
    else:
        recipe = None
    if recipe and kind != "recipe":
        kind = "recipe"
    return {
        "title": str(raw.get("title") or "Untitled memory").strip()[:90],
        "kind": kind if kind in KINDS else "other",
        "summary": str(raw.get("summary") or "").strip(),
        "people": _str_list(raw.get("people")),
        "places": _str_list(raw.get("places")),
        "year": year,
        "era": (str(raw["era"]).strip() if raw.get("era") else None),
        "topics": [t.lower() for t in _str_list(raw.get("topics"), 4)],
        "quotes": _str_list(raw.get("quotes"), 3),
        "recipe": recipe,
    }


def extract(transcript: str, elder: str, pr: dict | None = None) -> dict:
    pr = pr or {"sub": "they", "obj": "them"}
    text = transcript if len(transcript) <= MAX_CHARS else transcript[:MAX_CHARS] + " ..."
    raw = llm.chat_json([{"role": "user", "content": PROMPT.format(elder=elder, transcript=text, sub=pr["sub"], obj=pr["obj"])}], max_tokens=1500)
    info = normalize(raw)
    if info["recipe"] is None and (raw.get("kind") == "recipe" or "cooking" in info["topics"]):
        # Small models sometimes mark a recipe but leave the card empty. Ask for just the card.
        card = llm.chat_json([{"role": "user", "content": RECIPE_PROMPT.format(elder=elder, transcript=text)}], max_tokens=900)
        info = normalize({**raw, "recipe": card if "ingredients" in card else card.get("recipe")})
    return info
