"""FastAPI game server (docs/CONTRACTS.md, "Game server (port 8000)").

    .venv\\Scripts\\python -m uvicorn game.server:app --port 8000

One process, one worker. The DuckDB ledger connection lives on the event-loop thread; the
tick loop never touches the network or the serving files (the world watcher and advance.py
run in worker threads and hand over an already-loaded ServingData object).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import random
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import band, config, intel, match, rules
from . import state as st
from .ledger import Ledger
from .reasons import ReasonEngine
from .world import WorldManager

log = logging.getLogger("game.server")


# Browsers send an Origin header on WebSocket handshakes and on cross-site POSTs, and apply no
# same-origin policy to either, so any page the player visits could otherwise drive this server.
# Requests without an Origin (curl, the Python tests, start.ps1's health checks) are allowed.
DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:8000", "http://127.0.0.1:8000")
WS_MAX_MESSAGE = 1024          # contract messages are under 200 bytes
MAX_CLIENTS = 8
# While advance.py runs and no match is ticking, a state message goes out this often so the
# pipeline strip still moves (a running match already publishes every tick).
ADVANCE_HEARTBEAT_SECONDS = 1.0


def allowed_origins() -> set[str]:
    extra = config.env("PT_ALLOWED_ORIGINS") or ""
    return set(DEFAULT_ORIGINS) | {o.strip().rstrip("/") for o in extra.split(",") if o.strip()}


def origin_ok(headers) -> bool:
    """True when the request did not come from another site's page."""
    origin = headers.get("origin")
    if origin is not None and origin.rstrip("/") not in allowed_origins():
        return False
    return headers.get("sec-fetch-site", "").lower() != "cross-site"


class _Client:
    __slots__ = ("ws", "queue", "task")

    def __init__(self, ws: WebSocket, maxsize: int) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self.task: asyncio.Task | None = None


class ConnectionManager:
    """One send queue and one sender task per socket, so a slow client can never stall the tick
    loop. A client whose queue overflows or whose send takes longer than SEND_TIMEOUT is closed
    (code 1011), which fires the browser's onclose and makes it reconnect."""

    SEND_TIMEOUT = 1.0
    QUEUE_MAX = 8

    def __init__(self, max_clients: int = MAX_CLIENTS) -> None:
        self.max_clients = max_clients
        self._clients: dict[WebSocket, _Client] = {}
        self.dropped = 0

    @property
    def active(self) -> list[WebSocket]:
        return list(self._clients)

    async def connect(self, ws: WebSocket) -> bool:
        if len(self._clients) >= self.max_clients:
            await ws.close(code=1013)          # try again later
            return False
        await ws.accept()
        c = _Client(ws, self.QUEUE_MAX)
        c.task = asyncio.get_running_loop().create_task(self._sender(c))
        self._clients[ws] = c
        return True

    def disconnect(self, ws: WebSocket) -> None:
        c = self._clients.pop(ws, None)
        if c is not None and c.task is not None and c.task is not asyncio.current_task():
            c.task.cancel()

    def _drop(self, c: _Client, why: str) -> None:
        if self._clients.get(c.ws) is not c:
            return
        self.dropped += 1
        log.info("closing a websocket client: %s", why)
        self.disconnect(c.ws)
        asyncio.get_running_loop().create_task(self._close(c.ws))

    @staticmethod
    async def _close(ws: WebSocket) -> None:
        try:
            await asyncio.wait_for(ws.close(code=1011), timeout=2.0)
        except Exception:
            pass

    async def _sender(self, c: _Client) -> None:
        try:
            while True:
                text = await c.queue.get()
                try:
                    await asyncio.wait_for(c.ws.send_text(text), timeout=self.SEND_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._drop(c, f"send failed ({type(exc).__name__})")
                    return
        except asyncio.CancelledError:
            pass

    async def send(self, ws: WebSocket, text: str) -> None:
        c = self._clients.get(ws)
        if c is None:
            return
        try:
            c.queue.put_nowait(text)
        except asyncio.QueueFull:
            self._drop(c, "send queue full")

    async def broadcast(self, text: str) -> None:
        """Never waits on a socket: it only queues the message for each client."""
        for c in list(self._clients.values()):
            try:
                c.queue.put_nowait(text)
            except asyncio.QueueFull:
                self._drop(c, "send queue full (client not reading)")


def _new_match_id() -> str:
    return "m-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


class Game:
    def __init__(self, tick_seconds: float | None = None) -> None:
        self.tick_seconds = tick_seconds if tick_seconds is not None else config.tick_seconds()
        self.manager = ConnectionManager()
        self.world = WorldManager()
        self.ledger = Ledger()
        self.reasons = ReasonEngine(self._on_reason)
        self.state = self._lobby_state()
        self.rng = random.Random(0)
        self.pending_world = None
        self.human_inputs: dict[str, dict] = {}
        self.unseen_ids: set[str] = set()    # ledger ids fogged in this match's broadcasts
        self.last_message: dict = st.snapshot(self.state, advancing=self.world.advancing)
        self.recent_events: list[dict] = []
        self.pending_events: list[dict] = []   # late events (intel) published with the next tick
        self.match_reveal: dict[str, dict] = {}
        self.band = band.BandMirror(flag=False)  # replaced in start()
        self.intel: intel.IntelClient | None = None
        self._loop_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._bg: set[asyncio.Task] = set()
        self.world.on_change(self._on_world_change)
        self.world.on_advance_result(self._on_advance_result)

    # ------------------------------------------------------------ lifecycle
    def _lobby_state(self) -> st.MatchState:
        return st.new_match(self.world.current, seed=0, match_id="m-lobby", phase="lobby")

    async def start(self) -> None:
        self._watch_task = asyncio.create_task(self.world.watch())
        self._spawn(self.reasons.resolve_model())
        self.band = band.BandMirror()
        await self.band.start()
        band.set_default(self.band)
        if intel.enabled():
            self.intel = intel.IntelClient()

    async def stop(self) -> None:
        for t in (self._loop_task, self._watch_task):
            if t:
                t.cancel()
        self.reasons.cancel_all()
        for t in (self._loop_task, self._watch_task):
            if t:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await self.band.stop()
        band.set_default(None)
        if self.intel is not None:
            await self.intel.aclose()
        self.ledger.close()

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # ------------------------------------------------------------ messages
    def advance_public(self) -> dict | None:
        """`world.advance`: live progress while advance.py runs; the closing "done" object while
        its new week still waits in pending_world; else None. Reads memory only."""
        live = self.world.advance_progress()
        if live is not None:
            return live
        if self.pending_world is not None and self.world.final_progress is not None:
            return dict(self.world.final_progress)
        return None

    def _advance_delivered(self) -> None:
        """Forget the closing object of the last advance once nothing waits for it any more.
        Otherwise a later world change that this server did not start (advance.py or reset.sh
        run from a terminal) would be published with the end state of an older run."""
        if self.pending_world is None and not self.world.advancing:
            self.world.final_progress = None

    def snapshot(self, rows=None, events=None, deltas=None, feedback=None, advance=None) -> dict:
        # "advancing" stays true until the new week is on the board: a world loaded between
        # ticks waits in pending_world for the next tick, and the HUD must not flip early.
        try:
            adv = advance if advance is not None else self.advance_public()
        except Exception:                      # progress is decoration: never break a tick over it
            adv = None
        reveal = None
        if self.state.phase == "ended":
            try:
                reveal = match.reveal_data(self.state, self.match_reveal)
            except Exception:
                log.exception("could not build the end-of-match reveal")
        return st.snapshot(self.state, rows, events,
                           advancing=self.world.advancing or self.pending_world is not None,
                           advance=adv, deltas=deltas, feedback=feedback, reveal=reveal)

    async def publish(self, rows=None, events=None, deltas=None, feedback=None, advance=None) -> None:
        msg = self.snapshot(rows, events, deltas, feedback, advance)
        if self.state.phase == "running":
            self.unseen_ids.update(r["ledger_id"] for r in msg["ledger"] if r.get("action") == "UNSEEN")
        else:
            self.unseen_ids.clear()          # match over: the whole ledger may be seen
        self.last_message = msg
        if events:
            self.recent_events = (self.recent_events + list(events))[-50:]
            for e in events:
                self.band.submit(e["kind"], e["text"])     # non-blocking; drops when backed up
        await self.manager.broadcast(json.dumps(msg, separators=(",", ":")))

    async def _on_reason(self, ledger_id: str, text: str, model: str | None, latency: int | None) -> None:
        try:
            self.ledger.update_reason(ledger_id, text, model, latency)
        except Exception as exc:
            log.warning("could not store a late reason: %s", type(exc).__name__)
        if ledger_id in self.unseen_ids:
            return                               # the row was fogged; its reason would give it away
        await self.manager.broadcast(json.dumps({"t": "reason", "ledger_id": ledger_id, "reason_text": text}))

    # ------------------------------------------------------------ world
    def _on_world_change(self, data) -> None:
        if self.state.phase == "running":
            board = str(self.state.world.get("asof_date") or "")
            if data.asof <= board:
                # Not a new week (a reset to day 0, or a re-run of this week): nothing drops onto
                # the board; the next match deals from the new world.
                self.pending_world = None
                self.pending_events.append({"kind": "info", "text": (
                    f"World data replaced: now as of {data.asof}, danger set by {match.source_name(data.world)}. "
                    f"This match keeps its {board} cards; the next match deals from the new data.")})
                return
            self.pending_world = data          # applied at the start of the next tick
        else:
            self.state = self._lobby_state() if self.state.phase == "lobby" else self.state
            self._spawn(self.publish(events=[{"kind": "gate", "text": match.gate_text(data.world)}]))

    def _on_advance_result(self, res: dict) -> None:
        ok = bool(res.get("ok"))
        msg = str(res.get("message") or "")[:200]
        text = (f"Advance finished: data as of {res.get('asof_date')}, danger set by {match.source_name(res)}."
                if ok else f"Advance did not complete: {msg or 'no message'}")
        # This one message carries the closing progress object (stage "done" or "failed").
        final = self.world.final_progress
        self._spawn(self.publish(events=[{"kind": "info", "text": text}],
                                 advance=dict(final) if final else None))
        self._advance_delivered()      # kept only while its new week still waits in pending_world

    def advance(self) -> dict:
        res = self.world.start_advance()
        text = ("Advancing the world one week in the background." if res.get("ok")
                else f"Advance not started: {res.get('message')}")
        self._spawn(self.publish(events=[{"kind": "info", "text": text}]))
        if res.get("ok"):
            self._spawn(self._advance_heartbeat())
        return res

    async def _advance_heartbeat(self) -> None:
        """Outside a running match nothing ticks, so publish the progress here."""
        while self.world.advancing:
            await asyncio.sleep(ADVANCE_HEARTBEAT_SECONDS)
            if self.world.advancing and self.state.phase != "running":
                await self.publish()

    def _reveal_of(self, data) -> dict[str, dict]:
        """A serving folder's reveal entries, each stamped with the week it was computed for."""
        return {cve: {**r, "asof_date": data.asof} for cve, r in (data.reveal or {}).items()}

    # ------------------------------------------------------------ match control
    def start_match(self, seed: int | None = None) -> dict:
        if self.state.phase == "running":
            return {"ok": False, "message": "a match is already running", "phase": "running"}
        seed = int(seed) if seed is not None else random.SystemRandom().randrange(1, 2**31)
        tuning = config.TUNING
        if self.intel is not None:     # TELEM_MAX_PER_MATCH caps paid searches per match (default 3)
            cap = int(config.env_float("TELEM_MAX_PER_MATCH", tuning.intel_max_per_match))
            cap = min(cap, self.intel.budget_left)   # TELEM_MAX_PER_PROCESS caps the whole process
            tuning = tuning.with_overrides(intel_enabled=True, intel_max_per_match=max(0, cap))
        self.state = st.new_match(self.world.current, seed=seed, match_id=_new_match_id(), tuning=tuning)
        self.rng = random.Random(seed)
        self.pending_world = None
        self._advance_delivered()
        self.pending_events = []
        self.match_reveal = self._reveal_of(self.world.current)   # grows with each week dropped mid-match
        self.human_inputs = {}
        self.unseen_ids = set()
        self._loop_task = asyncio.get_running_loop().create_task(self._run_match())
        return {"ok": True, "match_id": self.state.match_id, "seed": seed, "phase": "running"}

    async def reset(self) -> dict:
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
        self.state = self._lobby_state()
        self.pending_world = None
        self._advance_delivered()
        self.human_inputs = {}
        await self.publish(events=[{"kind": "info", "text": "Back to the lobby."}])
        return {"ok": True, "phase": "lobby"}

    def human_input(self, msg: dict) -> bool:
        p = msg.get("player")
        if type(p) is int and p in (1, 2):          # bool and float are refused (int(True) == 1)
            player = p
        elif isinstance(p, str) and p in ("1", "2"):
            player = int(p)
        else:
            return False
        a = msg.get("action")
        action = a.upper() if isinstance(a, str) and len(a) <= 10 else ""
        if action not in rules.BLUE_ACTIONS:
            return False
        d = msg.get("dir")
        cve = msg.get("cve")
        self.human_inputs[f"p{player}"] = {"player": player, "action": action,
                                           "dir": d[:2].upper() if isinstance(d, str) and d else None,
                                           "cve": cve[:40] if isinstance(cve, str) and cve else None}
        return True

    def take_human_actions(self, state: st.MatchState) -> dict:
        """Drain the latest input per player into Actions for this tick. A player whose PATCH,
        RESTORE, ... is still running keeps the input for the first free tick (rules.step would
        ignore it now); a newer key press replaces it."""
        inputs, self.human_inputs = self.human_inputs, {}
        humans = {}
        for aid, m in inputs.items():
            actor = state.actors.get(aid)
            if actor is not None and actor.busy_ticks > 0:
                self.human_inputs.setdefault(aid, m)
                continue
            a = rules.human_action(state, m["player"], m["action"], m["dir"], m["cve"])
            if a is not None:
                humans[aid] = a
        return humans

    # ------------------------------------------------------------ Telem intel (off the tick)
    async def _run_intel(self, row: dict, world: dict, match_id: str) -> None:
        """Run the Hunter's search, write the reduced result into its ledger row (reason_text,
        with llm_model = "telem:<session id>"), broadcast it like a late reason, and queue an
        "intel" event for the next tick. Failure or the 4 s timeout means "no intel"."""
        try:
            res = await self.intel.search(row["cve_id"], world)
        except Exception as exc:  # never let a background task die loudly
            log.info("intel task failed: %s", type(exc).__name__)
            return
        actor = self.state.actors.get(row["actor_id"]) if self.state.match_id == match_id else None
        text = res.text(actor.name if actor else "Hunter")
        source = f"telem:{res.session_id}" if res.session_id else "telem"
        try:
            self.ledger.update_reason(row["ledger_id"], text, source, res.latency_ms)
        except Exception as exc:
            log.warning("could not store intel: %s", type(exc).__name__)
        await self.manager.broadcast(json.dumps({"t": "reason", "ledger_id": row["ledger_id"], "reason_text": text}))
        if res.usage:
            log.info("Telem reported usage for %s: %s", row["cve_id"], res.usage)
        ev = {"kind": "intel", "text": text}
        if self.state.phase == "running" and self.state.match_id == match_id:
            self.pending_events.append(ev)
        else:
            await self.publish(events=[ev])

    # ------------------------------------------------------------ the tick loop
    async def _run_match(self) -> None:
        state = self.state
        await self.publish(events=[{"kind": "info", "text": f"Match started: data as of {state.world.get('asof_date')}, "
                                                            f"danger set by {match.source_name(state.world)}."}])
        try:
            while state.phase == "running" and state is self.state:
                t0 = time.monotonic()
                humans = self.take_human_actions(state)
                pending, self.pending_world = self.pending_world, None
                pending_taken = pending is not None
                if pending is not None and state.tick >= state.ticks_total - state.tuning.world_change_cutoff:
                    pending = None             # too late in the match: the next match deals from it
                res = match.run_tick(state, humans, self.rng, new_world=pending)
                ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
                for r in res.rows:
                    r["ts"] = ts
                try:
                    self.ledger.write(res.rows)
                except Exception as exc:
                    log.error("ledger write failed: %s", exc)
                self.reasons.schedule(res.rows, res.facts)
                for r in res.rows:
                    if r["action"] == "INTEL" and r["outcome"] == "requested" and self.intel is not None:
                        self._spawn(self._run_intel(r, dict(state.world), state.match_id))
                late, self.pending_events = self.pending_events, []
                events = late + list(res.events)
                if pending is not None:
                    self.match_reveal.update(self._reveal_of(pending))
                if state.phase == "ended":
                    # the board holds cards from the starting week and from any mid-match drop
                    rv = match.reveal_text(state, self.match_reveal)
                    if rv:
                        events.append({"kind": "info", "text": rv})
                await self.publish(res.rows, events, res.deltas, res.feedback)
                if pending_taken:
                    self._advance_delivered()   # its new week is on the board now
                await asyncio.sleep(max(0.0, self.tick_seconds - (time.monotonic() - t0)))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tick loop crashed")
            state.phase = "ended"
            await self.publish(events=[{"kind": "info", "text": "The match stopped on a server error."}])


# ---------------------------------------------------------------- FastAPI app
game: Game | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global game
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # httpx logs every request URL at INFO; endpoints come from .env, so keep them out of the log.
    for name in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)
    config.load_env()
    game = Game()
    await game.start()
    log.info("serving data from %s (%s), tick %.2fs, LLM reasons %s, BAND %s, Telem %s",
             game.world.current.source, game.world.current.asof, game.tick_seconds,
             "on" if game.reasons.enabled else "off (templates)",
             "on" if game.band.active else ("off: " + game.band.disabled_reason if game.band.disabled_reason else "off"),
             "on" if game.intel is not None else "off")
    try:
        yield
    finally:
        await game.stop()
        game = None


app = FastAPI(title="Patch Tuesday game server", lifespan=lifespan)


@app.middleware("http")
async def refuse_cross_site_writes(request: Request, call_next):
    """CSRF guard: a POST from another site's page (a form post needs no CORS preflight) is
    refused. GETs stay open; they change nothing."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_ok(request.headers):
        log.warning("refused %s %s from origin %r", request.method, request.url.path,
                    request.headers.get("origin"))
        return JSONResponse({"ok": False, "message": "cross-site request refused"}, status_code=403)
    return await call_next(request)


@app.get("/api/state")
async def api_state():
    return game.last_message


@app.get("/api/world")
async def api_world():
    return {**game.world.current.world, "advancing": game.world.advancing or game.pending_world is not None,
            "data_source": game.world.current.source, "last_advance": game.world.last_result,
            "advance": game.advance_public()}


@app.get("/api/status")
async def api_status():
    """Not in CONTRACTS.md: which optional integrations are live (no secrets)."""
    return {"phase": game.state.phase, "tick": game.state.tick, "tick_seconds": game.tick_seconds,
            "data_source": game.world.current.source, "asof_date": game.world.current.asof,
            "advancing": game.world.advancing, "last_advance": game.world.last_result,
            "llm": {"enabled": game.reasons.enabled, "model": game.reasons.model if game.reasons.enabled else None,
                    **game.reasons.stats},
            "band": game.band.status(),
            "telem": {"enabled": game.intel is not None, "searches": game.intel.calls if game.intel else 0},
            "clients": len(game.manager.active)}


@app.get("/api/ledger")
async def api_ledger(limit: int = Query(200, ge=1, le=5000)):
    rows = game.ledger.recent(limit)
    if game.unseen_ids:
        rows = [st.hidden_row(r) if r.get("ledger_id") in game.unseen_ids else r for r in rows]
    return rows


@app.post("/api/match/start")
async def api_match_start(seed: int | None = None):
    res = game.start_match(seed)
    return JSONResponse(res, status_code=200 if res["ok"] else 409)


@app.post("/api/match/reset")
async def api_match_reset():
    return await game.reset()


@app.post("/api/world/advance")
async def api_world_advance():
    res = game.advance()
    code = 202 if res.get("ok") else (409 if res.get("advancing") else 503)
    return JSONResponse(res, status_code=code)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not origin_ok(ws.headers):
        log.warning("refused a websocket from origin %r", ws.headers.get("origin"))
        await ws.close(code=1008)              # policy violation; before accept = HTTP 403
        return
    if not await game.manager.connect(ws):
        return
    try:
        await game.manager.send(ws, json.dumps(game.snapshot(), separators=(",", ":")))
        while True:
            raw = await ws.receive_text()
            if len(raw) > WS_MAX_MESSAGE:      # contract messages are tiny; ignore anything big
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("t") == "act":
                game.human_input(msg)
            elif msg.get("t") == "control":
                cmd = msg.get("cmd")
                if cmd == "start":
                    res = game.start_match()
                    if not res["ok"]:
                        await game.publish(events=[{"kind": "info", "text": res["message"]}])
                elif cmd == "reset":
                    await game.reset()
                elif cmd == "advance":
                    game.advance()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        game.manager.disconnect(ws)
