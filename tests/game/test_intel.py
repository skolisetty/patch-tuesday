"""Telem INTEL: reduction to domain + date (never a headline), budgets, timeout, failure, the Hunter's move."""
import asyncio
import random
from types import SimpleNamespace

from game import config, intel, match, rules, state as st
from game.rules import Action

WORLD = {"asof_date": "2026-03-22", "window_start": "2026-03-16"}


class FakeTelem:
    def __init__(self, results=None, delay=0.0, exc=None):
        self.results = results or []
        self.delay = delay
        self.exc = exc
        self.calls = []

    async def search(self, query, **kw):
        self.calls.append((query, kw))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return SimpleNamespace(results=self.results, session_id="sess-123", status="ok", by_provider=[], raw={})


def R(title, url, date=None):
    return SimpleNamespace(title=title, url=url, publish_date=date)


def test_reduces_to_domain_and_date_without_urls_or_exploit_material():
    results = [R("Vendor fixes flaw in Widget", "https://www.example.com/news/1?x=y", "2026-03-18T10:00:00Z"),
               R("PoC exploit released for CVE-2026-1", "https://evil.example/poc"),
               R("See https://x.example/a for details", "https://news.example.org/a", None),
               R("Vendor fixes flaw in Widget", "https://example.com/other", "2026-03-18"),   # same site and day
               R("Fourth item", "https://d.example"), R("Fifth", "https://e.example")]
    fake = FakeTelem(results)
    res = asyncio.run(intel.IntelClient(client=fake, providers=["brave"]).search("CVE-2026-1234", WORLD))
    assert res.ok and res.session_id == "sess-123"
    assert res.items == [{"domain": "example.com", "date": "2026-03-18"},
                         {"domain": "news.example.org", "date": None},
                         {"domain": "d.example", "date": None}]
    text = res.text()
    assert text == ("Hunter found 3 public reports on CVE-2026-1234 this world week "
                    "(example.com 2026-03-18, news.example.org, d.example).")
    assert "http" not in text and "www." not in text and "PoC" not in text and "Widget" not in text
    q, kw = fake.calls[0]
    assert q == "CVE-2026-1234"
    assert kw["published_after"] == "2026-03-16" and kw["published_before"] == "2026-03-22"
    assert kw["providers_include"] == ["brave"]


def test_advisory_headlines_never_reach_the_text():
    """Review finding: raw headlines with affected versions and weakness names were shown."""
    results = [R("CVE-2025-71257 Detail - NVD", "https://nvd.nist.gov/vuln/detail/CVE-2025-71257", "2026-03-17"),
               R("Acme FootPrints 20.20.02 <= 20.24.01.001 Authentication Bypass | Advisories",
                 "https://advisories.example.com/a/1", "2026-03-18"),
               R("CVE-2025-71257: CWE-306 Missing Authentication for Critical Function in Acme",
                 "https://radar.example.org/x", "2026-03-19"),
               R("Acme authentication bypass", "https://www.exploit-db.com/exploits/1", "2026-03-19"),
               R("Repo", "https://github.com/someone/cve-2025-71257", "2026-03-19"),
               R("Tracker", "https://vulncheck.com/advisories/x", "2026-03-19")]
    res = asyncio.run(intel.IntelClient(client=FakeTelem(results)).search("CVE-2025-71257", WORLD))
    text = res.text()
    assert [i["domain"] for i in res.items] == ["nvd.nist.gov", "advisories.example.com", "radar.example.org"]
    assert all(set(i) == {"domain", "date"} for i in res.items)
    for leaked in ("20.20.02", "Authentication", "CWE-306", "Missing", "Detail", "Acme", "exploit-db",
                   "github", "vulncheck"):
        assert leaked not in text, leaked
    assert text.startswith("Hunter found 3 public reports on CVE-2025-71257 this world week (")


def test_domain_filter_has_no_false_positive_on_ordinary_words():
    assert intel.reduce_results([R("News", "https://epochtimes.example.com/a")]) == [
        {"domain": "epochtimes.example.com", "date": None}]
    assert intel.reduce_results([R("News", "https://poc.example.com/a")]) == []


def test_process_budget_stops_searches(monkeypatch):
    fake = FakeTelem([R("x", "https://a.example")])
    c = intel.IntelClient(client=fake, max_calls=2)
    outs = [asyncio.run(c.search("CVE-2026-1234", WORLD)) for _ in range(4)]
    assert [o.ok for o in outs] == [True, True, False, False]
    assert len(fake.calls) == 2 and c.calls == 2 and c.budget_left == 0
    assert "budget" in outs[2].text()
    monkeypatch.setenv("TELEM_MAX_PER_PROCESS", "5")
    assert intel.IntelClient(client=fake).max_calls == 5
    monkeypatch.delenv("TELEM_MAX_PER_PROCESS")
    assert intel.IntelClient(client=fake).max_calls == intel.DEFAULT_MAX_PER_PROCESS == 10


def test_timeout_and_errors_mean_no_intel():
    slow = intel.IntelClient(client=FakeTelem(delay=1.0), timeout=0.1)
    res = asyncio.run(slow.search("CVE-2026-1234", WORLD))
    assert not res.ok and res.error == "timeout" and "no intel" in res.text()
    bad = intel.IntelClient(client=FakeTelem(exc=RuntimeError("boom")))
    res = asyncio.run(bad.search("CVE-2026-1234", WORLD))
    assert not res.ok and res.error == "RuntimeError"
    res = asyncio.run(bad.search("not-a-cve", WORLD))
    assert not res.ok and len(bad._client.calls) == 1          # no request for a non-CVE query


def test_hunter_uses_intel_only_when_enabled_and_within_budget(serving):
    for enabled in (False, True):
        tun = config.TUNING.with_overrides(intel_enabled=enabled)
        s = st.new_match(serving, 4, "m-intel", tun)
        rng = random.Random(4)
        rows = []
        while s.phase == "running":
            rows += match.run_tick(s, {}, rng).rows
        intel_rows = [r for r in rows if r["action"] == "INTEL"]
        if not enabled:
            assert intel_rows == []
        else:
            assert 1 <= len(intel_rows) <= tun.intel_max_per_match
            assert all(r["actor_id"] == "blue3" and r["outcome"] == "requested" and r["cve_id"] for r in intel_rows)
            ticks = [r["tick"] for r in intel_rows]
            assert all(b - a >= tun.intel_cooldown for a, b in zip(ticks, ticks[1:]))
            assert len({r["cve_id"] for r in intel_rows}) == len(intel_rows)


def test_humans_cannot_send_intel(serving):
    s = st.new_match(serving, 1, "m-h")
    assert rules.human_action(s, 1, "INTEL") is None
    tun = config.TUNING.with_overrides(intel_enabled=True)
    s = st.new_match(serving, 1, "m-h2", tun)
    res = rules.step(s, {"p1": Action("p1", "INTEL", card_id=s.nodes[0].cards[0].card_id)}, random.Random(1))
    p1 = next(r for r in res.rows if r["actor_id"] == "p1")
    assert p1["action"] == "WAIT"
