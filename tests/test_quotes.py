from app import agent

TRANSCRIPT = ("Everything I know today is my mom that taught me because majority have different method. "
              "But that uziza leaf and the uziza seed gives it a very sweet flavor.")


class FakeMemos:
    def find_one(self, *_args, **_kwargs):
        return {"transcript": TRANSCRIPT, "quotes": ["Everything I know today is my mom that taught me",
                                                     "that uziza leaf and the uziza seed gives it a very sweet flavor"]}


class FakeDB:
    memos = FakeMemos()


SOURCES = [{"memo_id": "6ac24ffbc2ebe366b9253d6d", "text": ""}]


def check(text, monkeypatch):
    monkeypatch.setattr(agent.store, "db", lambda: FakeDB())
    return agent.verify_quotes(text, SOURCES)


def test_real_quote_is_kept(monkeypatch):
    text, removed = check('She said "Everything I know today is my mom that taught me" [1].', monkeypatch)
    assert removed == [] and "my mom that taught me" in text


def test_invented_quote_is_removed(monkeypatch):
    text, removed = check('Here is how. She always said "okra is the queen of all soups". Enjoy.', monkeypatch)
    assert removed and "queen" not in text and "Here is how." in text


def test_two_real_sentences_stitched_into_one_quote_are_removed(monkeypatch):
    stitched = '"Everything I know today is my mom that taught me that uziza leaf and the uziza seed gives it a very sweet flavor"'
    _, removed = check(f"She said {stitched}.", monkeypatch)
    assert removed
