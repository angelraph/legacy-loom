from datetime import datetime, timedelta

from app import extract, llm, planner, store


def test_parse_json_handles_fences_and_trailing_commas():
    assert llm.parse_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert llm.parse_json('Sure! {"tool": "timeline"} hope that helps') == {"tool": "timeline"}


def test_normalize_promotes_recipes_and_drops_bad_years():
    out = extract.normalize({"title": "Egusi", "kind": "story", "year": "300",
                             "recipe": {"name": "Egusi soup", "ingredients": ["melon seeds", ""], "steps": []}})
    assert out["kind"] == "recipe"
    assert out["year"] is None
    assert out["recipe"]["ingredients"] == ["melon seeds"]


def test_normalize_without_recipe():
    out = extract.normalize({"title": "The flood", "kind": "story", "year": 1987, "recipe": {"name": "x"}})
    assert out["recipe"] is None and out["year"] == 1987 and out["kind"] == "story"


def test_chunk_segments_keeps_times_and_merges_short_tail():
    segs = [{"start": i * 10.0, "end": i * 10.0 + 9, "text": f"s{i}"} for i in range(11)]
    chunks = store.chunk_segments(segs, target=45)
    assert chunks[0]["start"] == 0.0
    assert chunks[-1]["end"] == 109.0
    assert " ".join(c["text"] for c in chunks).split() == [f"s{i}" for i in range(11)]


def test_rrf_rewards_agreement():
    a, b, c = {"_id": "a"}, {"_id": "b"}, {"_id": "c"}
    fused = store.rrf({"vector": [a, b], "text": [b, c]}, k=3)
    assert fused[0]["_id"] == "b"
    assert fused[0]["via"] == ["vector", "text"]


def test_planner_waits_for_enough_rated_sessions():
    rows = [{"hour": 10, "weekday": 1, "rating": 5, "topic": "school", "asked_by": "Ada"}] * 3
    out = planner.plan(rows)
    assert out["ready"] is False and out["need_more"] > 0


def test_planner_feature_encoding_is_stable():
    enc = planner.Encoder(["b", "a"])
    rows = [{"hour": 9, "weekday": 6, "asked_by": "a", "topic": "b"}]
    assert planner.build_features(rows, enc, enc).tolist() == [[9, 6, 0, 1]]
