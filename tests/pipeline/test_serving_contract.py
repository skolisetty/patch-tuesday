#!/usr/bin/env python3
"""Assert that serving/*.json match docs/CONTRACTS.md field by field.

Standard library only; runs in WSL (python3 tests/pipeline/test_serving_contract.py) or on Windows.
Optional argument: the serving directory (default: <repo>/serving). Exit 0 = all checks passed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVING = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "serving"

CVE_RX = re.compile(r"^CVE-\d{4}-\d{4,19}$")
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FORBIDDEN_KEYS = re.compile(r"desc|url|uri|href|reference|link|payload|exploit_code", re.I)
URLISH = re.compile(r"https?://|www\.", re.I)

WORLD_FIELDS = {
    "asof_date": "date", "window_start": "date", "pipeline_run_id": "str", "score_source": "source",
    "model_version": "str?", "challenger_p_at_50": "num?", "epss_p_at_50": "num?",
    "challenger_hits_at_50": "int?", "epss_hits_at_50": "int?", "n_positives": "int?", "n_candidates": "int?",
    "n_cves_visible": "int", "n_kev_visible": "int", "n_new_cves_this_week": "int", "n_cards": "int",
}
CARD_FIELDS = {
    "card_id": "int", "cve_id": "cve", "title": "str", "published_date": "date", "age_days": "int",
    "vendor": "str?", "product": "str?", "cwe_id": "str?", "cvss_base_score": "num?", "severity": "severity?",
    "score": "unit", "score_source": "source", "epss": "unit?", "epss_percentile": "unit?", "model_score": "unit?",
    "in_kev": "bool", "technique_id": "str?", "technique_name": "str?", "tactic": "str?",
    "mapping_source": "mapping", "is_new_this_week": "bool", "priority_rank": "int?",
}
PRIORITY_FIELDS = {
    "rank": "int", "cve_id": "cve", "score": "unit", "score_source": "source", "epss": "unit?",
    "model_score": "unit?", "in_kev": "bool", "vendor": "str?", "product": "str?", "cvss_base_score": "num?",
    "technique_id": "str?", "in_deck": "bool",
}
REVEAL_FIELDS = {"cve_id": "cve", "entered_kev_within_91d": "bool", "kev_date_added": "date?"}

failures: list[str] = []
passed = 0


def check(cond: bool, msg: str) -> None:
    global passed
    if cond:
        passed += 1
    else:
        failures.append(msg)


def type_ok(value, kind: str) -> bool:
    nullable = kind.endswith("?")
    kind = kind.rstrip("?")
    if value is None:
        return nullable
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "unit":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "str":
        return isinstance(value, str) and value != ""
    if kind == "date":
        if not (isinstance(value, str) and DATE_RX.match(value)):
            return False
        try:
            dt.date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if kind == "cve":
        return isinstance(value, str) and bool(CVE_RX.match(value))
    if kind == "source":
        return value in ("model", "epss")
    if kind == "severity":
        return value in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    if kind == "mapping":
        return value in ("kev_ctid", "cwe_capec", "heuristic", "none")
    raise ValueError(kind)


def check_rows(name: str, rows, fields: dict) -> None:
    check(isinstance(rows, list), f"{name}: not a JSON array")
    for i, row in enumerate(rows):
        keys = set(row)
        check(keys == set(fields), f"{name}[{i}]: fields {sorted(keys ^ set(fields))} differ from the contract")
        for k, kind in fields.items():
            check(type_ok(row.get(k), kind), f"{name}[{i}].{k}={row.get(k)!r} is not {kind}")
        for k, v in row.items():
            check(not FORBIDDEN_KEYS.search(k), f"{name}[{i}]: forbidden field name {k}")
            if isinstance(v, str):
                check(not URLISH.search(v), f"{name}[{i}].{k}: looks like a URL")
                check(len(v) <= 160, f"{name}[{i}].{k}: {len(v)} chars, too long for a label")


def load(name: str, required: bool = True):
    path = SERVING / name
    if not path.exists():
        check(not required, f"{name}: missing")
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


world_rows = load("world.json")
cards = load("attack_cards.json")
priority = load("patch_priority.json")
reveal = load("reveal.json", required=False)

if world_rows is not None:
    check_rows("world.json", world_rows, WORLD_FIELDS)
    check(len(world_rows) == 1, f"world.json: {len(world_rows)} objects, expected exactly one")
world = world_rows[0] if world_rows else {}

if world:
    asof = dt.date.fromisoformat(world["asof_date"])
    check(asof.weekday() == 6, f"world.json: asof_date {asof} is not a Sunday")
    check(dt.date(2026, 3, 22) <= asof <= dt.date(2026, 6, 14), f"world.json: asof_date {asof} outside the replay range")
    check(world["window_start"] == (asof - dt.timedelta(days=6)).isoformat(), "world.json: window_start is not asof-6")
    if world["score_source"] == "model":
        check(world["model_version"] is not None, "world.json: model source but no model_version")
        check(world["challenger_hits_at_50"] > world["epss_hits_at_50"], "world.json: model served although challenger did not beat EPSS")
    else:
        check(world["model_version"] is None, "world.json: epss source but a model_version is set")
    for p, h in (("challenger_p_at_50", "challenger_hits_at_50"), ("epss_p_at_50", "epss_hits_at_50")):
        if world[p] is not None:
            check(0.0 <= world[p] <= 1.0, f"world.json: {p} out of range")
            check(abs(world[p] - world[h] / 50.0) < 1e-9, f"world.json: {p} != {h}/50")
    check(world["n_kev_visible"] > 1000, "world.json: fewer than 1000 KEV entries visible")
    check(world["n_new_cves_this_week"] <= world["n_cves_visible"], "world.json: more new CVEs than visible CVEs")

if cards is not None:
    check_rows("attack_cards.json", cards, CARD_FIELDS)
    n = len(cards)
    check(100 <= n <= 140, f"attack_cards.json: {n} cards, expected about 120")
    check(len({c["card_id"] for c in cards}) == n, "attack_cards.json: card_id not unique")
    check(len({c["cve_id"] for c in cards}) == n, "attack_cards.json: cve_id not unique")
    check(len({c["score_source"] for c in cards}) <= 1, "attack_cards.json: more than one score_source")
    if world:
        check(world["n_cards"] == n, f"world.json n_cards {world['n_cards']} != {n} cards")
        asof = dt.date.fromisoformat(world["asof_date"])
        start = dt.date.fromisoformat(world["window_start"])
        for c in cards:
            pub = dt.date.fromisoformat(c["published_date"])
            check(c["score_source"] == world["score_source"], f"card {c['card_id']}: score_source differs from world.json")
            check(pub <= asof, f"card {c['card_id']}: published after the world clock")
            check(c["age_days"] == (asof - pub).days, f"card {c['card_id']}: age_days wrong")
            check(c["is_new_this_week"] == (start <= pub <= asof), f"card {c['card_id']}: is_new_this_week wrong")
            if c["score_source"] == "epss":
                check(c["model_score"] is None, f"card {c['card_id']}: model_score set although the gate failed")
                check(abs(c["score"] - (c["epss"] or 0.0)) < 1e-12, f"card {c['card_id']}: epss source but score != epss")
            else:
                check(c["model_score"] is not None and abs(c["score"] - c["model_score"]) < 1e-12,
                      f"card {c['card_id']}: model source but score != model_score")
    for c in cards:   # the CTID KEV mapping exists only for KEV entries: a non-KEV kev_ctid card leaks the future
        check(c["mapping_source"] != "kev_ctid" or c["in_kev"], f"card {c['card_id']}: kev_ctid mapping but not in KEV")
    check(any(c["is_new_this_week"] for c in cards), "attack_cards.json: no card from the current week")
    check(any(c["in_kev"] for c in cards), "attack_cards.json: no KEV card")

if priority is not None:
    check_rows("patch_priority.json", priority, PRIORITY_FIELDS)
    check(len(priority) == 500, f"patch_priority.json: {len(priority)} rows, expected 500")
    check([p["rank"] for p in priority] == list(range(1, len(priority) + 1)), "patch_priority.json: ranks not 1..n in order")
    check(all(priority[i]["score"] >= priority[i + 1]["score"] for i in range(len(priority) - 1)), "patch_priority.json: not ordered by score")
    check(len({p["cve_id"] for p in priority}) == len(priority), "patch_priority.json: cve_id not unique")
    if world:
        check(all(p["score_source"] == world["score_source"] for p in priority), "patch_priority.json: score_source differs from world.json")
    if cards is not None:
        deck = {c["cve_id"]: c for c in cards}
        for p in priority:
            check(p["in_deck"] == (p["cve_id"] in deck), f"patch_priority rank {p['rank']}: in_deck wrong")
        ranks = {p["cve_id"]: p["rank"] for p in priority}
        for c in cards:
            check(c["priority_rank"] == ranks.get(c["cve_id"]), f"card {c['card_id']}: priority_rank does not match patch_priority.json")

if reveal is not None:
    check_rows("reveal.json", reveal, REVEAL_FIELDS)
    if cards is not None:
        check(sorted(r["cve_id"] for r in reveal) == sorted(c["cve_id"] for c in cards), "reveal.json: not one row per deck card")
    if world:
        asof = dt.date.fromisoformat(world["asof_date"])
        for r in reveal:
            if r["entered_kev_within_91d"]:
                d = dt.date.fromisoformat(r["kev_date_added"])
                check(asof < d <= asof + dt.timedelta(days=91), f"reveal {r['cve_id']}: date outside the 91-day window")

print(f"serving contract: {passed} checks passed, {len(failures)} failed "
      f"({SERVING}; asof {world.get('asof_date')}, score_source {world.get('score_source')}, "
      f"{len(cards or [])} cards, {len(priority or [])} priority rows, reveal {'present' if reveal is not None else 'absent'})")
for f in failures[:40]:
    print("FAIL:", f)
if len(failures) > 40:
    print(f"... {len(failures) - 40} more")
sys.exit(1 if failures else 0)
