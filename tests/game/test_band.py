"""BAND mirror against a fake HTTP transport: throttle, queue/drop, switch-off rules, room choice."""
import asyncio
import time

import httpx
import pytest

from game import band

AGENT_KEY = "band_a_test_only_not_a_real_key"


class FakeBand:
    def __init__(self, event_status=200, chats=None, chats_status=200):
        self.event_status = event_status
        self.chats_status = chats_status
        self.chats = chats if chats is not None else [{"id": "room-1", "title": "Patch Tuesday SOC"},
                                                      {"id": "room-2", "title": "Unrelated"}]
        self.posts = []          # (monotonic time, chat id, json body)
        self.headers = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(dict(request.headers))
        if request.method == "GET" and request.url.path.endswith("/agent/chats"):
            return httpx.Response(self.chats_status, json={"data": self.chats})
        if request.method == "POST" and request.url.path.endswith("/events"):
            chat = request.url.path.split("/chats/")[1].split("/")[0]
            import json
            self.posts.append((time.monotonic(), chat, json.loads(request.content)))
            return httpx.Response(self.event_status, json={"data": {"id": "e1"}} if self.event_status < 400
                                  else {"error": {"code": "limit_reached"}})
        return httpx.Response(404)


def mirror(fake, **kw):
    return band.BandMirror(api_key=AGENT_KEY, flag=True, interval=kw.pop("interval", 0.2),
                           transport=httpx.MockTransport(fake.handler), **kw)


async def _run(m, items, wait):
    await m.start()
    for kind, text in items:
        m.submit(kind, text)
    await asyncio.sleep(wait)
    await m.stop()


def test_posts_events_to_the_patch_tuesday_room_with_throttle():
    fake = FakeBand()
    m = mirror(fake)
    asyncio.run(_run(m, [("breach", "Opportunist breached WEB-01"), ("drop", "12 new cards"),
                         ("patched", "Triage patched X")], 1.0))
    assert [p[1] for p in fake.posts] == ["room-1"] * 3
    body = fake.posts[0][2]
    assert body == {"event": {"content": "[Patch Tuesday] BREACH: Opportunist breached WEB-01",
                              "message_type": "attention"}}
    assert fake.posts[1][2]["event"]["message_type"] == "task"
    gaps = [b[0] - a[0] for a, b in zip(fake.posts, fake.posts[1:])]
    assert all(g >= 0.18 for g in gaps)                       # one post per interval
    assert fake.headers[0]["x-api-key"] == AGENT_KEY
    assert m.stats["sent"] == 3


def test_non_highlights_are_not_sent():
    fake = FakeBand()
    m = mirror(fake)
    asyncio.run(_run(m, [("info", "Advancing the world"), ("info", "Match over. Red wins: ...")], 0.5))
    assert [p[2]["event"]["content"] for p in fake.posts] == ["[Patch Tuesday] INFO: Match over. Red wins: ..."]


def test_queue_drops_when_backed_up_and_submit_never_blocks():
    fake = FakeBand()
    m = mirror(fake, interval=5.0, max_queue=3)

    async def go():
        await m.start()
        t0 = time.monotonic()
        results = [m.submit("breach", f"b{i}") for i in range(10)]
        took = time.monotonic() - t0
        await asyncio.sleep(0.3)
        await m.stop()
        return results, took

    results, took = asyncio.run(go())
    assert took < 0.05
    assert results[:3] == [True, True, True] and results.count(False) >= 6
    assert m.stats["dropped"] >= 6
    assert len(fake.posts) == 1                                # the rest waits for the 5 s throttle


@pytest.mark.parametrize("code", [401, 403, 429])
def test_switches_itself_off_on_auth_or_rate_limit(code):
    fake = FakeBand(event_status=code)
    m = mirror(fake)
    asyncio.run(_run(m, [("breach", "a"), ("breach", "b"), ("breach", "c")], 0.8))
    assert len(fake.posts) == 1
    assert m.active is False and str(code) in m.disabled_reason
    assert m.submit("breach", "d") is False


def test_no_matching_room_means_off_and_nothing_posted():
    fake = FakeBand(chats=[{"id": "x", "title": "Some other project"}])
    m = mirror(fake)
    asyncio.run(_run(m, [("breach", "a")], 0.4))
    assert fake.posts == [] and m.active is False and "Patch Tuesday" in m.disabled_reason


def test_explicit_chat_id_skips_discovery():
    fake = FakeBand(chats=[])
    m = mirror(fake, chat_id="room-9")
    asyncio.run(_run(m, [("gate", "Gate passed")], 0.4))
    assert [p[1] for p in fake.posts] == ["room-9"]


def test_user_key_is_refused_before_any_request():
    fake = FakeBand()
    m = band.BandMirror(api_key="band_u_something", flag=True, transport=httpx.MockTransport(fake.handler))
    asyncio.run(_run(m, [("breach", "a")], 0.2))
    assert m.active is False and "user key" in m.disabled_reason
    assert fake.headers == []


def test_flag_off_does_nothing():
    fake = FakeBand()
    m = band.BandMirror(api_key=AGENT_KEY, flag=False, transport=httpx.MockTransport(fake.handler))
    asyncio.run(_run(m, [("breach", "a")], 0.2))
    assert fake.headers == [] and m.submit("breach", "a") is False


def test_message_mode_posts_one_digest_that_mentions_the_human():
    """Band Desktop's Chat view shows messages, not thought events: highlights go out as a digest."""
    import json as _json

    import httpx

    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/participants"):
            return httpx.Response(200, json={"data": [
                {"id": "a1", "type": "Agent", "handle": "me/patch-tuesday", "name": "Patch Tuesday"},
                {"id": "u1", "type": "User", "handle": "me", "name": "Me"}]})
        if path.endswith("/messages"):
            posts.append(_json.loads(request.content))
            return httpx.Response(201, json={"data": {"id": "m1"}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    m = band.BandMirror(api_key="band_a_test", chat_id="c1", base_url="https://band.test/api/v1", interval=0.05,
                        transport=httpx.MockTransport(handler), flag=True, post_as="message")

    async def run():
        await m.start()
        for i in range(3):
            m.submit("breach", f"breach {i}")
        await asyncio.sleep(0.4)
        await m.stop()

    asyncio.run(run())
    assert len(posts) >= 1
    first = posts[0]["message"]
    assert first["mentions"] == [{"id": "u1", "handle": "me", "name": "Me"}]
    assert first["content"].startswith("@me BREACH: breach 0")
    assert sum(p["message"]["content"].count("BREACH:") for p in posts) == 3
    assert m.stats["sent"] == 3
