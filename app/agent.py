"""The memory keeper. Gemma picks a tool, the tool runs against Atlas or TabPFN,
then Gemma answers only from what came back and cites the exact second of the recording."""
import json
import re
from difflib import SequenceMatcher
from typing import Iterator

from . import embed, llm, planner, store

TOOLS = {
    "search_memories": "find passages where {sub} talks about something (people, places, events, feelings, advice)",
    "find_recipe": "look up a dish {sub} explained how to cook",
    "timeline": "list all {pos} memories in the order the events happened",
    "plan_calls": "the best times this week to call {obj} and which topics tend to go well (TabPFN forecast)",
}

ROUTER = """You route questions for a private archive of {elder}'s voice memos.
Tools:
{tools}

Pick the tools needed to answer the question. Most questions need search_memories.
Return only JSON: {{"calls": [{{"tool": "<name>", "query": "<short search text in English>"}}]}} with 1 or 2 calls.

Recent conversation:
{history}
Question: {question}"""

ANSWER = """You are Legacy Loom, the keeper of {elder}'s recorded memories, talking with {family}.
Answer the question using only the material below. Every fact from a passage gets its number in square brackets, like [2].
Speak warmly and plainly, the way a close friend who listened to every voice note would. Keep it under 180 words.
When {sub} said something memorable, quote {pos} words exactly.
For a recipe, give the ingredients and the steps as short plain lists.
Never invent names, dates, ingredients, events or feelings. Do not say how {sub} felt about something unless {sub} said it. Never output JSON.
Only if the material does not answer the question: say {sub} has not talked about that in any recording yet, then suggest one gentle question they could ask {obj} next time, on its own line starting with "Ask {obj}:". If the material answers the question, do not add an "Ask {obj}:" line.

Material:
{material}"""


def _history_text(history: list[dict]) -> str:
    lines = [f"{h['role']}: {h['content'][:300]}" for h in history[-4:]]
    return "\n".join(lines) or "(none)"


def route(question: str, history: list[dict], elder: str, pr: dict) -> list[dict]:
    tools = "\n".join(f"- {k}: {v.format(**pr)}" for k, v in TOOLS.items())
    try:
        out = llm.chat_json([{"role": "user", "content": ROUTER.format(
            elder=elder, tools=tools, history=_history_text(history), question=question)}], max_tokens=150)
        calls = [c for c in out.get("calls", []) if isinstance(c, dict) and c.get("tool") in TOOLS][:2]
    except (llm.LLMError, json.JSONDecodeError, AttributeError):
        calls = []
    return calls or [{"tool": "search_memories", "query": question}]


def _fmt_time(sec: float) -> str:
    sec = int(sec or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def run_tool(call: dict, question: str) -> dict:
    tool, query = call["tool"], (call.get("query") or question).strip()
    if tool in ("search_memories", "find_recipe"):
        kind = "recipe" if tool == "find_recipe" else None
        res = store.hybrid_search(query, embed.embed_query(query), k=6, kind=kind)
        if kind and not res["hits"]:
            res = store.hybrid_search(query, embed.embed_query(query), k=6)
        sources = [{"memo_id": h["memo_id"], "title": h["title"], "start": h["start"], "end": h["end"],
                    "text": h["text"], "overview": bool(h.get("overview")), "via": h["via"]} for h in res["hits"]]
        recipes = []
        if tool == "find_recipe":
            ids = {s["memo_id"] for s in sources}
            for mid in ids:
                m = store.get_memo(mid)
                if m and m.get("recipe"):
                    recipes.append({"memo_id": mid, "title": m["title"], "recipe": m["recipe"]})
        return {"tool": tool, "query": query, "sources": sources, "recipes": recipes, "modes": res["modes"]}
    if tool == "timeline":
        memos = [m for m in store.list_memos() if m.get("status") == "ready"]
        memos.sort(key=lambda m: (m.get("year") is None, m.get("year") or 0))
        return {"tool": tool, "query": query, "timeline": [
            {"memo_id": str(m["_id"]), "title": m["title"], "year": m.get("year"), "era": m.get("era"),
             "summary": m.get("summary", "")} for m in memos]}
    if tool == "plan_calls":
        return {"tool": tool, "query": query, "plan": planner.plan(store.list_sessions())}
    return {"tool": tool, "query": query}


def build_material(results: list[dict]) -> tuple[str, list[dict]]:
    """Number every passage so the answer can cite it, and keep the list for the UI."""
    parts, sources, seen = [], [], set()
    for r in results:
        for s in r.get("sources", []):
            key = (s["memo_id"], s["start"], s["overview"])
            if key in seen:
                continue
            seen.add(key)
            sources.append(s)
            where = "overview" if s["overview"] else f"at {_fmt_time(s['start'])}"
            parts.append(f"[{len(sources)}] \"{s['title']}\" ({where}): {s['text']}")
        for rec in r.get("recipes", []):
            rc = rec["recipe"]
            card = [f"Recipe card \"{rc['name']}\" (from the recording \"{rec['title']}\")"]
            if rc.get("serves"):
                card.append(f"Serves: {rc['serves']}")
            card.append("Ingredients: " + "; ".join(rc["ingredients"]))
            card += [f"Step {i}: {step}" for i, step in enumerate(rc["steps"], 1)]
            card += [f"Tip: {t}" for t in rc.get("tips", [])]
            parts.append("\n".join(card))
        if "timeline" in r:
            rows = []
            for t in r["timeline"]:
                sources.append({"memo_id": t["memo_id"], "title": t["title"], "start": 0.0, "end": 0.0,
                                "text": t["summary"], "overview": True, "via": ["timeline"]})
                rows.append(f"[{len(sources)}] {t['year'] or t['era'] or 'year unknown'}: \"{t['title']}\". {t['summary']}")
            parts.append("Timeline of the memories:\n" + ("\n".join(rows) or "(no memories yet)"))
        if "plan" in r:
            p = r["plan"]
            if p.get("ready"):
                best = "; ".join(f"{b['label']} about {b['topic']} ({round(b['p_keeper'] * 100)}% chance of a keeper"
                                 + (f", about {b['minutes']} min" if b.get("minutes") else "") + ")" for b in p["best"][:3])
                topics = ", ".join(f"{t['topic']} {round(t['p'] * 100)}%" for t in p["topics"])
                parts.append(f"TabPFN forecast from {p['rated']} rated sessions. Best slots: {best}. Topics: {topics}.")
            else:
                parts.append(f"The call planner is not ready yet: {p.get('reason')}")
    return "\n\n".join(parts) or "(nothing found)", sources


QUOTE = re.compile(r"[\"“]([^\"“”]{12,400})[\"”]")


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def verify_quotes(text: str, sources: list[dict]) -> tuple[str, list[str]]:
    """Drop any sentence that puts words in their mouth.

    Every quoted span must match what was actually said: the transcripts of the memos behind the
    sources. A small model will sometimes write a warm line and present it as a quote. That is
    the one mistake an archive of someone's voice cannot make, so it is checked, not trusted.
    """
    memo_ids = {s["memo_id"] for s in sources}
    said = [s["text"] for s in sources]
    for mid in memo_ids:
        m = store.db().memos.find_one({"_id": store.oid(mid)}, {"transcript": 1, "quotes": 1})
        if m:
            said.append(m.get("transcript") or "")
            said += m.get("quotes") or []
    corpus = _norm(" ".join(said))
    removed = []
    for q in QUOTE.findall(text):
        nq = _norm(q)
        if not nq or nq in corpus:
            continue
        match = SequenceMatcher(None, nq, corpus, autojunk=False).find_longest_match(0, len(nq), 0, len(corpus))
        if match.size / len(nq) >= 0.85:
            continue
        removed.append(q)
    for q in removed:
        # Remove the whole sentence that carries the invented quote.
        text = re.sub(r"[^.!?\n]*" + re.escape(q) + r"[^.!?\n]*[.!?]?[\"”]?", "", text)
    if removed:
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, removed


def answer(question: str, history: list[dict]) -> Iterator[dict]:
    fam = store.get_family()
    elder = fam.get("elder_name") or "them"
    family = fam.get("family_name") or "the people who love them"
    pr = store.pronouns(fam)

    calls = route(question, history, elder, pr)
    yield {"type": "plan", "calls": calls}
    results = []
    for c in calls:
        r = run_tool(c, question)
        results.append(r)
        yield {"type": "tool", "tool": r["tool"], "query": r["query"], "modes": r.get("modes"),
               "plan": r.get("plan"), "recipes": r.get("recipes")}

    material, sources = build_material(results)
    yield {"type": "sources", "sources": sources}

    messages = [{"role": "system", "content": ANSWER.format(elder=elder, family=family, material=material, **pr)}]
    for h in history[-4:]:
        messages.append({"role": "assistant" if h["role"] == "assistant" else "user", "content": h["content"][:800]})
    messages.append({"role": "user", "content": question})

    full = []
    for piece in llm.stream(messages):
        full.append(piece)
        yield {"type": "token", "text": piece}
    text, removed = verify_quotes("".join(full), sources)
    if removed:
        yield {"type": "removed_quotes", "quotes": removed}
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 0 < int(n) <= len(sources)})
    ask = re.search(r"\n?\s*Ask (?:her|him|them):\s*(.+)", text)
    not_found = re.search(r"\b(has not|hasn't|hasn’t|did not|didn't|didn’t|never) (talk|mention|say|said)", text, re.I)
    if ask and cited and not not_found:
        # The answer is grounded, so a "what to ask next" line would only be noise.
        text, ask = text[:ask.start()].rstrip(), None
    question = ask.group(1).strip().strip('"“”') if ask else None
    yield {"type": "done", "text": text, "cited": cited, "ask_her": question}
