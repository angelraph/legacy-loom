import pytest

from app import config, voice


class FakeUsage:
    def __init__(self):
        self.counts = {}

    def find_one(self, query):
        n = self.counts.get(query["_id"])
        return {"n": n} if n is not None else None


class FakeDB:
    def __init__(self):
        self.tts_usage = FakeUsage()


def test_visitor_limit_and_daily_ceiling(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(voice.store, "db", lambda: db)
    monkeypatch.setattr(config, "TTS_PER_VISITOR", 3)
    monkeypatch.setattr(config, "TTS_PER_DAY", 40)
    mine, everyone = voice._check_quota("visitor-a")
    db.tts_usage.counts[mine] = 3
    with pytest.raises(voice.QuotaReached, match="today's 3"):
        voice._check_quota("visitor-a")
    voice._check_quota("visitor-b")  # someone else still has theirs
    db.tts_usage.counts[everyone] = 40
    with pytest.raises(voice.QuotaReached, match="everyone"):
        voice._check_quota("visitor-b")
