"""Late one-sentence reasons from an OpenAI-compatible endpoint, with the template as fallback.

Every ledger row already carries a template sentence rendered by the policy, so a failed,
slow or disabled LLM call changes nothing. When a call succeeds inside the timeout, the
callback updates the ledger row and the server broadcasts {"t":"reason",...}.

The model sees only structured game facts (actor, action, node, card id, numbers); never a
CVE description, a reference URL or anything from the outside world.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable

from . import config

log = logging.getLogger("game.reasons")

INTERESTING = {"EXPLOIT", "PATCH", "ISOLATE", "RESTORE"}
MAX_WORDS = 25
SYSTEM_PROMPT = (
    "You narrate a turn-based cyber defence board game. Moves are abstract game moves, not real attacks. "
    "Given the structured facts of one move, write exactly one plain sentence under 25 words explaining "
    "why the character chose it, using only the facts given. No technical details, no instructions, "
    "no links, no quotation marks."
)
_URL = re.compile(r"(https?://|www\.)\S+", re.I)

Callback = Callable[[str, str, str | None, int | None], Awaitable[None] | None]


def is_interesting(row: dict, facts: dict | None) -> bool:
    if row.get("kind") != "agent" or (facts or {}).get("continuing"):
        return False
    return row.get("action") in INTERESTING or row.get("policy") == "campaign"


def clean_sentence(text: str | None) -> str | None:
    """One sentence, under 25 words, no links. None when nothing usable is left."""
    if not text:
        return None
    t = _URL.sub("", str(text)).replace("\n", " ").replace('"', "").strip()
    t = re.sub(r"\s+", " ", t)
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    if m:
        t = m.group(1)
    words = t.split(" ")
    if len(words) > MAX_WORDS - 1:
        t = " ".join(words[: MAX_WORDS - 1]).rstrip(",;:") + "."
    if len(t) < 8:
        return None
    if t[-1] not in ".!?":
        t += "."
    return t


def build_messages(row: dict, facts: dict) -> list[dict]:
    keep = {k: v for k, v in facts.items() if isinstance(v, (str, int, float, bool)) or v is None}
    payload = {"actor": facts.get("actor"), "team": row.get("team"), "policy": row.get("policy"),
               "action": row.get("action"), "outcome": row.get("outcome"), "facts": keep}
    if row.get("p_breach") is not None:
        payload["breach_odds"] = row["p_breach"]
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Facts: " + json.dumps(payload, sort_keys=True)
                                        + "\nOne sentence, under 25 words."}]


class ReasonEngine:
    def __init__(self, on_reason: Callback, timeout: float = 4.0, concurrency: int = 2,
                 max_pending: int = 6, client=None, model: str | None = None):
        self.on_reason = on_reason
        self.timeout = timeout
        self.max_pending = max_pending
        self._sem = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task] = set()
        self.model = model or config.env("LLM_MODEL") or None
        self.client = client
        self.stats = {"scheduled": 0, "ok": 0, "failed": 0, "dropped": 0}
        self._no_think = config.env("LLM_DISABLE_THINKING", "1") != "0"
        # LLM_REASONS=0 is the off switch (design.md section 8): template reasons only.
        if self.client is None and config.env("LLM_API_KEY") and self.model and config.env("LLM_REASONS", "1") != "0":
            try:
                import openai  # noqa: WPS433
                kw = {"api_key": config.env("LLM_API_KEY"), "timeout": timeout, "max_retries": 0}
                if config.env("LLM_BASE_URL"):
                    kw["base_url"] = config.env("LLM_BASE_URL")
                self.client = openai.AsyncOpenAI(**kw)
            except Exception as exc:  # missing package or bad config: templates only
                log.warning("LLM reasons disabled: %s", type(exc).__name__)
                self.client = None

    @property
    def enabled(self) -> bool:
        return self.client is not None and bool(self.model)

    async def resolve_model(self) -> str | None:
        """Once at start-up, off the tick: match LLM_MODEL against the endpoint's model list
        case-insensitively (model ids are case-sensitive on some servers). Best effort."""
        if not self.enabled:
            return None
        try:
            listing = await asyncio.wait_for(self.client.models.list(), self.timeout)
            ids = [m.id for m in getattr(listing, "data", [])]
        except Exception as exc:
            log.info("could not list LLM models: %s", type(exc).__name__)
            return self.model
        if ids and self.model not in ids:
            match = next((i for i in ids if i.lower() == self.model.lower()), None)
            if match:
                log.info("LLM_MODEL resolved to %s (case differs from .env)", match)
                self.model = match
            else:
                log.warning("LLM_MODEL is not offered by the endpoint; reasons will use templates")
        return self.model

    def schedule(self, rows: list[dict], facts: dict[str, dict]) -> int:
        """Fire-and-forget. Never blocks the tick. Returns how many calls were started."""
        if not self.enabled:
            return 0
        started = 0
        for row in rows:
            f = facts.get(row["ledger_id"], {})
            if not is_interesting(row, f):
                continue
            if len(self._tasks) >= self.max_pending:
                self.stats["dropped"] += 1
                continue
            task = asyncio.get_running_loop().create_task(self._one(row, f))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self.stats["scheduled"] += 1
            started += 1
        return started

    async def _call(self, messages: list[dict]) -> str | None:
        kw = {"model": self.model, "messages": messages, "max_tokens": 120, "temperature": 0.4}
        if self._no_think:
            # vLLM-style servers: switch a reasoning model's thinking off so the token budget
            # goes to the sentence. Servers that reject the field get it dropped for good.
            try:
                resp = await self.client.chat.completions.create(
                    **kw, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            except Exception as exc:
                if getattr(exc, "status_code", None) not in (400, 422):
                    raise
                self._no_think = False
                resp = await self.client.chat.completions.create(**kw)
        else:
            resp = await self.client.chat.completions.create(**kw)
        try:
            return resp.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            return None

    async def _one(self, row: dict, facts: dict) -> None:
        async with self._sem:
            t0 = time.monotonic()
            try:
                text = await asyncio.wait_for(self._call(build_messages(row, facts)), self.timeout)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # timeout, HTTP error, bad response: keep the template
                self.stats["failed"] += 1
                log.info("LLM reason fell back to template: %s", type(exc).__name__)
                return
            sentence = clean_sentence(text)
            if not sentence:
                self.stats["failed"] += 1
                return
            latency = int((time.monotonic() - t0) * 1000)
            self.stats["ok"] += 1
            res = self.on_reason(row["ledger_id"], sentence, self.model, latency)
            if asyncio.iscoroutine(res):
                await res

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def cancel_all(self) -> None:
        for t in list(self._tasks):
            t.cancel()
