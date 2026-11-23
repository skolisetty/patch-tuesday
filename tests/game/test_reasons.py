import asyncio
import json
import types

from game import band, intel, reasons
from game.reasons import ReasonEngine, build_messages, clean_sentence, is_interesting

ROW = {"ledger_id": "m-1-005-red1", "kind": "agent", "team": "red", "action": "EXPLOIT", "policy": "opportunist",
       "outcome": "failed", "p_breach": 0.41, "reason_text": "template"}
FACTS = {"actor": "Opportunist", "cve": "CVE-2099-0007", "score": 0.2, "target": "DB-01", "red_in_sight": ["x"]}


class FakeClient:
    def __init__(self, text=None, delay=0.0, exc=None):
        self.calls = []
        self.text, self.delay, self.exc = text, delay, exc
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        msg = types.SimpleNamespace(content=self.text)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def _run(engine_kw, rows=(ROW,), facts=None):
    got = []

    async def go():
        async def cb(lid, text, model, ms):
            got.append((lid, text, model, ms))
        eng = ReasonEngine(cb, **engine_kw)
        n = eng.schedule(list(rows), facts or {ROW["ledger_id"]: FACTS})
        await eng.drain()
        return eng, n
    eng, n = asyncio.run(go())
    return eng, n, got


def test_disabled_without_key():
    eng, n, got = _run({})
    assert not eng.enabled and n == 0 and got == []


def test_success_updates_through_callback():
    client = FakeClient("The Opportunist tried DB-01 because the fresh card looked easy. Extra sentence here.")
    eng, n, got = _run({"client": client, "model": "test-model"})
    assert n == 1 and len(got) == 1
    lid, text, model, ms = got[0]
    assert lid == ROW["ledger_id"] and model == "test-model" and ms >= 0
    assert text == "The Opportunist tried DB-01 because the fresh card looked easy."
    kw = client.calls[0]
    assert kw["model"] == "test-model"
    user = kw["messages"][1]["content"]
    assert "CVE-2099-0007" in user and "red_in_sight" not in user


def test_timeout_and_errors_fall_back_to_template():
    eng, n, got = _run({"client": FakeClient("late", delay=0.5), "model": "m", "timeout": 0.1})
    assert n == 1 and got == [] and eng.stats["failed"] == 1
    eng, n, got = _run({"client": FakeClient(exc=RuntimeError("boom")), "model": "m"})
    assert got == [] and eng.stats["failed"] == 1
    eng, n, got = _run({"client": FakeClient(""), "model": "m"})
    assert got == []


def test_only_interesting_agent_rows():
    assert is_interesting(ROW, {})
    assert not is_interesting({**ROW, "action": "MOVE"}, {})
    assert is_interesting({**ROW, "action": "MOVE", "policy": "campaign"}, {})
    assert not is_interesting({**ROW, "kind": "human", "action": "PATCH"}, {})
    assert not is_interesting({**ROW, "action": "PATCH"}, {"continuing": True})
    for a in ("PATCH", "ISOLATE", "RESTORE"):
        assert is_interesting({**ROW, "action": a}, {})


def test_bounded_pending():
    rows = [{**ROW, "ledger_id": f"id-{i}"} for i in range(20)]
    facts = {r["ledger_id"]: FACTS for r in rows}
    eng, n, got = _run({"client": FakeClient("A fine short reason sentence."), "model": "m", "max_pending": 4},
                       rows, facts)
    assert n == 4 and eng.stats["dropped"] == 16 and len(got) == 4


def test_clean_sentence():
    assert clean_sentence(None) is None
    assert clean_sentence("ok") is None
    long = " ".join(["word"] * 40)
    assert len(clean_sentence(long).split()) < 25
    assert "http" not in clean_sentence("See https://example.com/x for the reason it moved.")
    assert clean_sentence('"Quoted reason text here"') == "Quoted reason text here."


def test_prompt_is_structured_facts_only():
    msgs = build_messages(ROW, FACTS)
    assert msgs[0]["role"] == "system" and "under 25 words" in msgs[0]["content"]
    payload = json.loads(msgs[1]["content"].split("Facts: ", 1)[1].split("\n")[0])
    assert payload["action"] == "EXPLOIT" and payload["breach_odds"] == 0.41


def test_band_and_intel_are_off_by_default(monkeypatch):
    assert band.enabled() is False and intel.enabled() is False
    assert asyncio.run(band.post_event("breach", "x")) is False
    assert asyncio.run(intel.search_intel("q", "2026-03-16", "2026-03-22")) == []
    monkeypatch.setenv("BAND_ENABLED", "1")
    monkeypatch.setenv("TELEM_ENABLED", "1")
    monkeypatch.delenv("TELEM_API_KEY", raising=False)
    assert band.enabled()
    assert intel.enabled() is False                                   # the flag alone is not enough
    assert asyncio.run(band.post_event("breach", "x")) is False      # no mirror running: nothing sent
    assert asyncio.run(intel.search_intel("q", None, None)) == []
