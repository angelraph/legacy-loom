"""Best time to call. TabPFN learns from the session log which slots and topics
lead to the recordings they treasure, and how long she tends to talk.

A friendship has dozens of recorded sessions, not thousands. That is the size of table TabPFN was built for:
it is pretrained, so it predicts well from a handful of rows with no training loop of our own.
"""
from collections import Counter
from datetime import datetime, timedelta

import numpy as np

from . import config

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
KEEPER_RATING = 4


def _models():
    """Prior Labs hosted TabPFN when a token is set, otherwise the open local package."""
    if config.TABPFN_TOKEN:
        import tabpfn_client
        tabpfn_client.set_access_token(config.TABPFN_TOKEN)
        return tabpfn_client.TabPFNClassifier, tabpfn_client.TabPFNRegressor, "TabPFN (Prior Labs API)"
    try:
        from tabpfn import TabPFNClassifier, TabPFNRegressor
        return TabPFNClassifier, TabPFNRegressor, "TabPFN (local)"
    except ImportError:
        return None, None, None


def _client_installed() -> bool:
    try:
        import tabpfn_client  # noqa: F401
        return True
    except ImportError:
        return False


def forecast(asked_by: str | None = None) -> dict:
    """Run the planner here, or on Vercel ask the separate planner function that carries TabPFN.

    The TabPFN client brings about 400 MB of scientific libraries, more than fits next to the
    rest of the app in one serverless function, so it lives in its own function there.
    """
    from . import store
    if not (config.SERVERLESS and not _client_installed()):
        return plan(store.list_sessions(), asked_by=asked_by)
    import httpx
    r = httpx.get(config.PLANNER_URL or f"{config.PUBLIC_BASE_URL}/api/planner", params={"asked_by": asked_by} if asked_by else None, timeout=200)
    r.raise_for_status()
    return r.json()


def status() -> dict:
    if config.TABPFN_TOKEN:
        return {"ok": True, "detail": "Prior Labs API token set"}
    try:
        import tabpfn  # noqa: F401
        return {"ok": True, "detail": "local tabpfn package"}
    except ImportError:
        return {"ok": False, "detail": "set TABPFN_TOKEN (free at ux.priorlabs.ai)"}


class Encoder:
    """Stable integer codes for the categorical columns."""

    def __init__(self, values):
        self.codes = {v: i for i, v in enumerate(sorted(set(values)))}

    def __call__(self, v):
        return self.codes.get(v, len(self.codes))


def build_features(rows: list[dict], asker_enc: Encoder, topic_enc: Encoder) -> np.ndarray:
    return np.array([[r["hour"], r["weekday"], asker_enc(r.get("asked_by") or "unknown"),
                      topic_enc(r.get("topic") or "general")] for r in rows], dtype=float)


def readiness(sessions: list[dict]) -> dict:
    rated = [s for s in sessions if s.get("rating")]
    keepers = sum(1 for s in rated if s["rating"] >= KEEPER_RATING)
    need = max(0, config.PLANNER_MIN_ROWS - len(rated))
    both = keepers > 0 and keepers < len(rated)
    return {"sessions": len(sessions), "rated": len(rated), "keepers": keepers,
            "need_more": need, "both_classes": both, "ready": need == 0 and both}


def plan(sessions: list[dict], asked_by: str | None = None, days: int = 7, start: datetime | None = None) -> dict:
    ready = readiness(sessions)
    if not ready["ready"]:
        reason = (f"Rate {ready['need_more']} more sessions so TabPFN has enough to learn from."
                  if ready["need_more"] else
                  "Every rated session has the same outcome. TabPFN needs some keepers and some that were not.")
        return {"ready": False, "reason": reason, **ready}

    Clf, Reg, engine = _models()
    if Clf is None:
        return {"ready": False, "reason": "TabPFN is not configured. Set TABPFN_TOKEN.", **ready}

    rated = [s for s in sessions if s.get("rating")]
    timed = [s for s in sessions if s.get("duration_min")]
    asker_enc = Encoder([s.get("asked_by") or "unknown" for s in sessions])
    topic_enc = Encoder([s.get("topic") or "general" for s in sessions])

    X = build_features(rated, asker_enc, topic_enc)
    y = np.array([1 if s["rating"] >= KEEPER_RATING else 0 for s in rated])
    clf = Clf()
    clf.fit(X, y)

    reg = None
    if len(timed) >= config.PLANNER_MIN_ROWS:
        reg = Reg()
        reg.fit(build_features(timed, asker_enc, topic_enc), np.array([s["duration_min"] for s in timed], dtype=float))

    hours_seen = [s["hour"] for s in sessions]
    lo, hi = max(7, min(hours_seen) - 1), min(21, max(hours_seen) + 1)
    topic_counts = Counter(s.get("topic") or "general" for s in sessions)
    topics = [t for t, _ in topic_counts.most_common(6)]
    who = asked_by or Counter(s.get("asked_by") or "unknown" for s in sessions).most_common(1)[0][0]

    start = (start or datetime.now()).replace(minute=0, second=0, microsecond=0)
    grid = []
    for d in range(1, days + 1):
        day = start + timedelta(days=d)
        for h in range(lo, hi + 1):
            for t in topics:
                grid.append({"date": day.date().isoformat(), "hour": h, "weekday": day.weekday(),
                             "asked_by": who, "topic": t})
    Xg = build_features(grid, asker_enc, topic_enc)
    proba = clf.predict_proba(Xg)
    keeper_col = list(clf.classes_).index(1)
    p = proba[:, keeper_col]
    minutes = reg.predict(Xg) if reg is not None else [None] * len(grid)

    for g, pk, m in zip(grid, p, minutes):
        g["p_keeper"] = round(float(pk), 3)
        g["minutes"] = round(float(m), 1) if m is not None else None

    # Best slot per (date, hour): pick the topic most likely to produce a keeper.
    best_by_slot: dict = {}
    for g in grid:
        key = (g["date"], g["hour"])
        if key not in best_by_slot or g["p_keeper"] > best_by_slot[key]["p_keeper"]:
            best_by_slot[key] = g
    top = sorted(best_by_slot.values(), key=lambda g: g["p_keeper"], reverse=True)[:5]
    for g in top:
        g["label"] = f"{WEEKDAYS[g['weekday']]} {g['date'][5:]} at {g['hour']:02d}:00"

    heat = {}
    for g in grid:
        heat.setdefault((g["weekday"], g["hour"]), []).append(g["p_keeper"])
    heatmap = [{"weekday": wd, "day": WEEKDAYS[wd], "hour": h, "p": round(float(np.mean(v)), 3)}
               for (wd, h), v in sorted(heat.items())]
    by_topic = sorted(({"topic": t, "p": round(float(np.mean([g["p_keeper"] for g in grid if g["topic"] == t])), 3)}
                       for t in topics), key=lambda x: x["p"], reverse=True)

    return {"ready": True, "engine": engine, "asked_by": who, "best": top, "heatmap": heatmap,
            "topics": by_topic, "hours": [lo, hi], "duration_model": reg is not None, **ready}
