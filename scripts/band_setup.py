#!/usr/bin/env python
"""One-time BAND setup for "Call for backup" (docs/BAND.md). Standard library only.

    python scripts/band_setup.py [--env PATH] [--suffix TEXT] [--dry-run]

What it does, and it is safe to run again (a second run changes nothing):

  1. Reads BAND_USER_API_KEY from the env file (or BAND_API_KEY when that holds a user key,
     band_u_...). Only a user key may register agents.
  2. For each of the seven agents (Coach, Triage, Patcher, Hunter, Responder, Hardener, Sentinel):
     keeps the key already in the env file when BAND accepts it; otherwise registers the agent
     and stores its key (BAND shows a key once) and id in the env file.
     If an agent of that name already exists but the env file has no working key for it, the
     script STOPS before changing anything. It never deletes an agent. Regenerate that agent's
     key in the BAND web app and put it in the env file, or run again with --suffix.
  3. Creates the room "Patch Tuesday - Blue team" with Coach's key, unless BAND_BLUE_CHAT_ID
     already names a room Coach can read.
  4. Adds the missing members: Triage, Patcher, Hunter and you (the owner). The three
     specialists are NOT added: Coach brings one in during a match.
  5. Posts one hello line from Coach when the room is new or a member was added.

It prints names, ids and handles. It never prints a key, and only BAND_* lines of the env file
are ever written. --dry-run reads only: no POST, no change to the env file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://app.band.ai/api/v1"
ROOM_TITLE = "Patch Tuesday - Blue team"
TIMEOUT_S = 15

# env name part, display name, description (BAND wants 10-500 characters)
AGENTS = [
    ("COACH", "Coach", "Patch Tuesday game: watches the Blue team, names its weakest point and calls in a specialist."),
    ("TRIAGE", "Triage", "Patch Tuesday game: blue AI defender that patches the most dangerous cards first."),
    ("PATCHER", "Patcher", "Patch Tuesday game: blue AI defender that works through open cards machine by machine."),
    ("HUNTER", "Hunter", "Patch Tuesday game: blue AI defender that monitors machines and reveals attackers."),
    ("RESPONDER", "Responder", "Patch Tuesday game: backup specialist that restores breached machines fast."),
    ("HARDENER", "Hardener", "Patch Tuesday game: backup specialist that patches the cards worth the most."),
    ("SENTINEL", "Sentinel", "Patch Tuesday game: backup specialist that keeps the most valuable machines monitored."),
]
ROOM_MEMBERS = ("TRIAGE", "PATCHER", "HUNTER")          # plus the owner; specialists join in a match
SPECIALISTS = ("RESPONDER", "HARDENER", "SENTINEL")

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class Stop(Exception):
    """A problem the owner has to solve; the message is safe to print."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ env file
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[m.group(1)] = value
    return out


def write_env(path: Path, updates: dict[str, str]) -> None:
    """Set BAND_* variables in the env file. Every other line stays byte for byte as it was."""
    for k, v in updates.items():
        if not k.startswith("BAND_"):
            raise ValueError("only BAND_* variables may be written")
        if "\n" in v or "\r" in v:
            raise ValueError("a value may not hold a line break")
    raw = path.read_bytes() if path.exists() else b""
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline) if text else []
    ended = bool(lines) and lines[-1] == ""
    if ended:
        lines.pop()
    left = dict(updates)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if m and m.group(1) in left:
            lines[i] = f"{m.group(1)}={left.pop(m.group(1))}"
    if left:
        if not any("Call for backup" in ln for ln in lines):
            if lines and lines[-1].strip():
                lines.append("")
            lines.append('# BAND "Call for backup": written by scripts/band_setup.py')
        lines.extend(f"{k}={v}" for k, v in left.items())
    body = newline.join(lines) + newline
    data = (b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ HTTP
def check_base(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    local = u.hostname in ("127.0.0.1", "localhost", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise Stop("BAND_BASE_URL must be https (keys are sent in a header).")
    return url.rstrip("/")


class Api:
    """Tiny JSON client. Keys travel only in the X-API-Key header; bodies are never printed."""

    def __init__(self, base: str, dry_run: bool):
        self.base = base
        self.dry_run = dry_run
        self.writes = 0

    def call(self, method: str, path: str, key: str, body: dict | None = None, query: dict | None = None):
        if method != "GET":
            if self.dry_run:
                raise AssertionError("a write was attempted in --dry-run")   # guarded by the callers
            self.writes += 1
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", key)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise Stop(f"BAND could not be reached ({type(exc).__name__}). Nothing else was changed.", 4)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = None
        return status, parsed


def err_text(status: int, body) -> str:
    """HTTP status plus BAND's error code and message. An error body never holds a key."""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        extra = ""
        details = err.get("details")
        if isinstance(details, dict) and details.get("key"):
            extra = f" [{details.get('key')}]"
        return f"HTTP {status} {err.get('code')}: {str(err.get('message'))[:200]}{extra}"
    return f"HTTP {status}"


def data_of(body):
    return body.get("data") if isinstance(body, dict) else None


# ------------------------------------------------------------------ steps
def find_agent(api: Api, user_key: str, name: str) -> dict | None:
    """An owned agent with exactly this name (the API filter is a substring match)."""
    status, body = api.call("GET", "/me/agents", user_key, query={"name": name, "limit": 100})
    if status == 403:
        raise Stop(f"BAND refused the agent list ({err_text(status, body)}). The user key needs Human API access.", 4)
    if status != 200:
        raise Stop(f"Could not list your agents ({err_text(status, body)}).", 4)
    rows = data_of(body)
    for a in rows if isinstance(rows, list) else []:
        if isinstance(a, dict) and str(a.get("name") or "").strip().lower() == name.lower():
            return a
    return None


def run(env_path: Path, suffix: str, dry_run: bool, out=print) -> int:
    if not env_path.exists():
        raise Stop(f"No env file at {env_path}. Copy .env.example to .env and put your BAND user key in "
                   f"BAND_USER_API_KEY.")
    env = read_env(env_path)
    base = check_base(env.get("BAND_BASE_URL") or os.environ.get("BAND_BASE_URL") or BASE_URL)
    user_key = env.get("BAND_USER_API_KEY") or ""
    if not user_key and (env.get("BAND_API_KEY") or "").startswith("band_u_"):
        user_key = env["BAND_API_KEY"]
    if not user_key:
        raise Stop("BAND_USER_API_KEY is empty in the env file. Create a user key (band_u_...) in the BAND "
                   "web app and put it there; an agent key cannot register agents.")
    if user_key.startswith("band_a_"):
        raise Stop("BAND_USER_API_KEY holds an agent key (band_a_...). A user key (band_u_...) is needed.")
    api = Api(base, dry_run)
    mode = " (dry run: nothing is created or written)" if dry_run else ""
    out(f'BAND setup for "Call for backup"{mode}')
    out(f"  env file: {env_path}")

    status, body = api.call("GET", "/me/profile", user_key)
    if status != 200:
        raise Stop(f"BAND did not accept the user key ({err_text(status, body)}).", 4)
    me = data_of(body) or {}
    owner_id, owner_handle = str(me.get("id") or ""), str(me.get("handle") or "")
    owner_name = " ".join(x for x in (me.get("first_name"), me.get("last_name")) if x) or owner_handle
    out(f"  owner: {owner_name} (@{owner_handle})")

    # ---- 1. look at every agent first; write nothing until all seven are clear
    agents: dict[str, dict] = {}
    blocked: list[str] = []
    for var, title, description in AGENTS:
        name = f"{title}{suffix}"
        if not 3 <= len(name) <= 100:
            raise Stop(f'The agent name "{name}" must be 3 to 100 characters.')
        info = {"name": name, "description": description, "key": "", "id": "", "handle": "", "state": "register"}
        key = env.get(f"BAND_KEY_{var}") or ""
        if key:
            status, body = api.call("GET", "/agent/me", key)
            d = data_of(body)
            if status == 200 and isinstance(d, dict) and d.get("id"):
                info.update(key=key, id=str(d["id"]), handle=str(d.get("handle") or ""),
                            name=str(d.get("name") or name), state="keep")
            elif status not in (401, 403):
                raise Stop(f"Could not check the stored key of {title} ({err_text(status, body)}).", 4)
        if info["state"] != "keep":
            existing = find_agent(api, user_key, name)
            if existing is not None:
                info.update(state="blocked", id=str(existing.get("id") or ""))
                blocked.append(name)
        agents[var] = info
    if blocked:
        out("")
        out("STOPPED. Nothing was created, changed or deleted.")
        out("These agents already exist in your BAND account, but the env file has no working key for them:")
        for name in blocked:
            out(f"  - {name}")
        out("Either regenerate each one's API key in the BAND web app (Agents) and put it in the env file as")
        out("BAND_KEY_<NAME>, or run this script again with --suffix to register a separate set, for example:")
        out('  python scripts/band_setup.py --suffix " PT2"')
        return 3

    # ---- 2. register what is missing; each key is stored the moment BAND returns it
    for var, title, _ in AGENTS:
        info = agents[var]
        if info["state"] == "keep":
            if env.get(f"BAND_ID_{var}") != info["id"] and not dry_run:
                write_env(env_path, {f"BAND_ID_{var}": info["id"]})
            continue
        if dry_run:
            out(f'  would register the agent "{info["name"]}"')
            continue
        status, body = api.call("POST", "/me/agents/register", user_key,
                                {"agent": {"name": info["name"], "description": info["description"]}})
        d = data_of(body) or {}
        agent = d.get("agent") if isinstance(d.get("agent"), dict) else {}
        key = (d.get("credentials") or {}).get("api_key") if isinstance(d.get("credentials"), dict) else None
        if not 200 <= status < 300 or not key or not agent.get("id"):
            raise Stop(f'Could not register "{info["name"]}" ({err_text(status, body)}). '
                       f"Agents registered before this one are already saved in the env file.", 4)
        info.update(key=str(key), id=str(agent["id"]), state="registered")
        write_env(env_path, {f"BAND_KEY_{var}": info["key"], f"BAND_ID_{var}": info["id"]})
        out(f'  registered "{info["name"]}" (key stored in the env file, not shown)')

    coach = agents["COACH"]
    if not coach["key"]:                                   # only in a dry run
        out(f'  would create the room "{ROOM_TITLE}" and add Triage, Patcher, Hunter and you')
        _summary(out, agents, None, None, owner_handle)
        return 0

    # ---- 3. the room
    def participants(chat: str):
        s, b = api.call("GET", f"/agent/chats/{chat}/participants", coach["key"])
        rows = data_of(b)
        return rows if s == 200 and isinstance(rows, list) else None

    chat_id = env.get("BAND_BLUE_CHAT_ID") or ""
    people = participants(chat_id) if chat_id else None
    created = False
    if people is None:
        if chat_id:
            out("  BAND_BLUE_CHAT_ID names a room Coach cannot read: a new room is needed")
        if dry_run:
            out(f'  would create the room "{ROOM_TITLE}" and add Triage, Patcher, Hunter and you')
            _summary(out, agents, None, None, owner_handle)
            return 0
        status, body = api.call("POST", "/agent/chats", coach["key"], {"chat": {"title": ROOM_TITLE}})
        d = data_of(body) or {}
        if not 200 <= status < 300 or not d.get("id"):
            raise Stop(f"Could not create the room ({err_text(status, body)}).", 4)
        chat_id, created = str(d["id"]), True
        write_env(env_path, {"BAND_BLUE_CHAT_ID": chat_id})
        out(f'  created the room "{ROOM_TITLE}"')
        people = participants(chat_id) or []

    # ---- 4. members
    present = {str(p.get("id")) for p in people if isinstance(p, dict)}
    wanted = [(agents[v]["id"], agents[v]["name"]) for v in ROOM_MEMBERS]
    if owner_id:
        wanted.append((owner_id, f"{owner_name} (you)"))
    added = 0
    for pid, label in wanted:
        if not pid or pid in present:
            continue
        if dry_run:
            out(f"  would add {label} to the room")
            continue
        status, body = api.call("POST", f"/agent/chats/{chat_id}/participants", coach["key"],
                                {"participant": {"participant_id": pid, "role": "member"}})
        if 200 <= status < 300:
            added += 1
            out(f"  added {label} to the room")
        else:
            out(f"  could not add {label} to the room ({err_text(status, body)})")
    if added:
        people = participants(chat_id) or people

    # ---- 5. hello
    if (created or added) and not dry_run:
        mention = None
        for p in people:
            if isinstance(p, dict) and str(p.get("id")) == owner_id:
                mention = {"id": owner_id, "name": str(p.get("name") or owner_name)}
                if p.get("handle"):
                    mention["handle"] = str(p["handle"])
        if mention is None:
            mention = {"id": agents["TRIAGE"]["id"], "name": agents["TRIAGE"]["name"]}
        text = ("Coach here. This is the Blue team room of Patch Tuesday. During a match I will say what "
                "the team is weakest at and ask you whether to call in a specialist. Reply to me with yes, "
                "responder, hardener, sentinel or no.")
        if mention.get("handle"):
            text = f"@{mention['handle']} {text}"
        status, body = api.call("POST", f"/agent/chats/{chat_id}/messages", coach["key"],
                                {"message": {"content": text, "mentions": [mention]}})
        out("  Coach said hello in the room" if 200 <= status < 300
            else f"  the hello line was not posted ({err_text(status, body)})")

    _summary(out, agents, chat_id, people, owner_handle)
    if not dry_run and not api.writes:
        out("Everything was already in place: nothing was created or changed.")
    return 0


def _summary(out, agents: dict, chat_id: str | None, people, owner_handle: str) -> None:
    by_id = {str(p.get("id")): p for p in (people or []) if isinstance(p, dict)}
    out("")
    out("Agents (keys are in the env file and are not shown):")
    for var, title, _ in AGENTS:
        a = agents[var]
        handle = a["handle"] or str((by_id.get(a["id"]) or {}).get("handle") or "")
        state = {"keep": "ready", "registered": "new", "register": "not registered yet"}.get(a["state"], a["state"])
        out(f"  {a['name']:<12} {a['id'] or '-':<38} {('@' + handle) if handle else '':<34} {state}")
    if chat_id:
        out(f'Room "{ROOM_TITLE}": {chat_id}')
        names = sorted(str(p.get("name") or p.get("handle") or "?") for p in by_id.values())
        out(f"  {len(names)} participants: " + ", ".join(names))
        inside = [agents[v]["name"] for v in SPECIALISTS if agents[v]["id"] in by_id]
        if inside:
            out(f"  note: {', '.join(inside)} is in the room from an earlier match; the game takes it out "
                f"when the next match starts")
        out("Next: scripts\\start.ps1 -Band")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Register the BAND agents and the room for "Call for backup".')
    ap.add_argument("--env", default=str(ROOT / ".env"), help="env file to read and update (default: .env)")
    ap.add_argument("--suffix", default="", help='text added to every agent name, e.g. " PT2"')
    ap.add_argument("--dry-run", action="store_true", help="read only: no POST, no change to the env file")
    args = ap.parse_args(argv)
    try:
        return run(Path(args.env), args.suffix, args.dry_run)
    except Stop as exc:
        print(f"band_setup: {exc}")
        return exc.code
    except Exception as exc:                               # never a traceback with local values in it
        print(f"band_setup: unexpected {type(exc).__name__}; nothing more was changed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
