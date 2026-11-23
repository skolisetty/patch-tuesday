"""Write dev-only mock serving files in the exact contract shape.

    python -m game.make_mock            # writes serving_mock/ and serving_mock/next/

Every CVE id is fake (CVE-2099-NNNN for the deck, CVE-2098-NNNN for non-deck priority rows)
and every vendor is invented, so mock data cannot be mistaken for real data.
serving_mock/next/ is the same world one week later (new cards, gate passed) and is used to
exercise the mid-match drop.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from pathlib import Path

from . import config

VENDORS = ["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Vandelay", "Wonka", "Stark", "Wayne", "Tyrell",
           "Soylent", "Cyberdyne"]
PRODUCTS = ["Mailer", "Gateway", "Portal", "Sync", "Vault", "Router OS", "Print Server", "CMS", "VPN Appliance",
            "Directory", "Backup Agent", "Build Runner", "Wiki", "ERP Suite", "Camera Firmware"]
WEAKNESS = [("CWE-79", "cross-site scripting"), ("CWE-89", "SQL injection"), ("CWE-78", "command injection"),
            ("CWE-22", "path traversal"), ("CWE-287", "improper authentication"), ("CWE-502", "deserialization"),
            ("CWE-787", "out-of-bounds write"), ("CWE-416", "use after free"), ("CWE-918", "request forgery"),
            ("CWE-269", "privilege management"), (None, "unclassified weakness")]
TECHNIQUES = [("T1190", "Exploit Public-Facing Application", "initial-access"),
              ("T1133", "External Remote Services", "initial-access"),
              ("T1068", "Exploitation for Privilege Escalation", "privilege-escalation"),
              ("T1203", "Exploitation for Client Execution", "execution"),
              ("T1210", "Exploitation of Remote Services", "lateral-movement"),
              ("T1059", "Command and Scripting Interpreter", "execution"),
              ("T1078", "Valid Accounts", "defense-evasion")]


def _score(rng: random.Random, new: bool = False) -> float:
    """Skewed low like real EPSS: most CVEs are unlikely to be exploited, a few are very likely.
    Fresh CVEs skew lower still (EPSS has little signal on them yet)."""
    r = rng.random()
    lo, mid = (0.55, 0.85) if new else (0.40, 0.72)
    if r < lo:
        return round(rng.uniform(0.001, 0.05), 4)
    if r < mid:
        return round(rng.uniform(0.05, 0.30), 4)
    return round(rng.uniform(0.30, 0.97), 4)


def _severity(cvss: float | None) -> str | None:
    if cvss is None:
        return None
    return "CRITICAL" if cvss >= 9 else "HIGH" if cvss >= 7 else "MEDIUM" if cvss >= 4 else "LOW"


def _card(rng: random.Random, n: int, asof: dt.date, window_start: dt.date, new: bool, source: str) -> dict:
    vendor, product = rng.choice(VENDORS), rng.choice(PRODUCTS)
    cwe, weak = rng.choice(WEAKNESS)
    age = rng.randint(0, 6) if new else rng.randint(7, 400)
    cvss = None if rng.random() < 0.08 else round(rng.uniform(4.0, 10.0), 1)
    epss = _score(rng, new)
    model =round(min(0.99, max(0.001, epss * rng.uniform(0.6, 1.4))), 4) if source == "model" else None
    tech = rng.choice(TECHNIQUES) if rng.random() > 0.15 else (None, None, None)
    mapping = "none" if tech[0] is None else rng.choice(["kev_ctid", "cwe_capec", "heuristic"])
    if rng.random() < 0.06:
        vendor = product = None            # exercise the nullable fields
    title = f"{vendor} {product}: {weak}" if vendor else f"Unknown product: {weak}"
    return {
        "card_id": n, "cve_id": f"CVE-2099-{n:04d}", "title": title,
        "published_date": (asof - dt.timedelta(days=age)).isoformat(), "age_days": age,
        "vendor": vendor, "product": product, "cwe_id": cwe, "cvss_base_score": cvss,
        "severity": _severity(cvss), "score": model if source == "model" else epss, "score_source": source,
        "epss": None if rng.random() < 0.03 else epss,
        "epss_percentile": round(min(0.999, 0.5 + epss / 2), 4), "model_score": model,
        "in_kev": rng.random() < 0.12, "technique_id": tech[0], "technique_name": tech[1], "tactic": tech[2],
        "mapping_source": mapping, "is_new_this_week": new, "priority_rank": None,
    }


def _priority(rng: random.Random, cards: list[dict], source: str) -> list[dict]:
    """Top 500: the 100 best deck cards mixed with invented non-deck rows."""
    in_deck = sorted(cards, key=lambda c: -c["score"])[:100]
    rows = [{"cve_id": c["cve_id"], "score": c["score"], "score_source": source, "epss": c["epss"],
             "model_score": c["model_score"], "in_kev": c["in_kev"], "vendor": c["vendor"], "product": c["product"],
             "cvss_base_score": c["cvss_base_score"], "technique_id": c["technique_id"], "in_deck": True}
            for c in in_deck]
    for n in range(400):
        s = _score(rng)
        rows.append({"cve_id": f"CVE-2098-{n + 1:04d}", "score": s, "score_source": source, "epss": s,
                     "model_score": s if source == "model" else None, "in_kev": rng.random() < 0.05,
                     "vendor": rng.choice(VENDORS), "product": rng.choice(PRODUCTS),
                     "cvss_base_score": round(rng.uniform(4, 10), 1), "technique_id": rng.choice(TECHNIQUES)[0],
                     "in_deck": False})
    rows.sort(key=lambda r: (-r["score"], r["cve_id"]))
    rows = rows[:500]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    rank = {r["cve_id"]: r["rank"] for r in rows}
    for c in cards:
        c["priority_rank"] = rank.get(c["cve_id"])
    keys = ["rank", "cve_id", "score", "score_source", "epss", "model_score", "in_kev", "vendor", "product",
            "cvss_base_score", "technique_id", "in_deck"]
    return [{k: r[k] for k in keys} for r in rows]


def build(asof: dt.date, seed: int, source: str, first_id: int = 1, carry: list[dict] | None = None,
          n_new: int = 16, n_total: int = 120) -> dict[str, list]:
    rng = random.Random(seed)
    window_start = asof - dt.timedelta(days=6)
    cards: list[dict] = []
    if carry:  # the same CVEs one week later: older, re-scored
        for c in carry[: n_total - n_new]:
            c = dict(c)
            c["age_days"] += 7
            c["is_new_this_week"] = False
            drift = min(0.99, max(0.001, c["epss"] * rng.uniform(0.85, 1.25))) if c["epss"] is not None else c["score"]
            c["epss"] = round(drift, 4) if c["epss"] is not None else None
            c["model_score"] = round(min(0.99, drift * rng.uniform(0.7, 1.3)), 4) if source == "model" else None
            c["score"] = c["model_score"] if source == "model" else round(drift, 4)
            c["score_source"] = source
            cards.append(c)
    n = first_id
    while len(cards) < n_total:
        new = (n_total - len(cards)) <= n_new
        cards.append(_card(rng, n, asof, window_start, new, source))
        n += 1
    for i, c in enumerate(cards, 1):
        if not carry:
            c["card_id"] = i
    priority = _priority(rng, cards, source)
    gate = source == "model"
    world = [{
        "asof_date": asof.isoformat(), "window_start": window_start.isoformat(),
        "pipeline_run_id": f"mock-{asof.isoformat()}", "score_source": source,
        "model_version": f"hgb-{asof.isoformat()}" if gate else None,
        "challenger_p_at_50": 0.22 if gate else 0.12, "epss_p_at_50": 0.16,
        "challenger_hits_at_50": 11 if gate else 6, "epss_hits_at_50": 8,
        "n_positives": 41, "n_candidates": 250000,
        "n_cves_visible": 251000, "n_kev_visible": 1500,
        "n_new_cves_this_week": 900, "n_cards": len(cards),
    }]
    reveal = [{"cve_id": c["cve_id"],
               "entered_kev_within_91d": (hit := rng.random() < min(0.6, c["score"] * 0.5)),
               "kev_date_added": (asof + dt.timedelta(days=rng.randint(3, 90))).isoformat() if hit else None}
              for c in cards]
    return {"world": world, "attack_cards": cards, "patch_priority": priority, "reveal": reveal}


def write(directory: Path, files: dict[str, list]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("attack_cards", "patch_priority", "reveal", "world"):   # world.json last, like the pipeline
        tmp = directory / f"{name}.json.tmp"
        tmp.write_text(json.dumps(files[name], indent=None), encoding="utf-8")
        tmp.replace(directory / f"{name}.json")


def main(out: Path | None = None) -> None:
    out = Path(out or config.MOCK_DIR)
    week0 = build(dt.date(2026, 3, 22), seed=2099, source="epss")
    write(out, week0)
    week1 = build(dt.date(2026, 3, 29), seed=2100, source="model", first_id=121, carry=week0["attack_cards"])
    write(out / "next", week1)
    (out / "README.txt").write_text(
        "DEV-ONLY MOCK DATA. Every CVE id here is fake (CVE-2099-*, CVE-2098-*) and every vendor is invented.\n"
        "Generated by: python -m game.make_mock. The game uses this folder only when serving/ has no world.json.\n",
        encoding="utf-8")
    print(f"wrote {len(week0['attack_cards'])} cards to {out} and {len(week1['attack_cards'])} to {out / 'next'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
