import asyncio
import json
import sys
import threading
import time

import pytest

from game import world
from game.world import ServingError, load_current, load_serving, read_json_array

NULLABLE_CARD = ["title", "published_date", "age_days", "vendor", "product", "cwe_id", "cvss_base_score",
                 "severity", "epss", "epss_percentile", "model_score", "technique_id", "technique_name", "tactic",
                 "priority_rank", "score", "score_source", "mapping_source", "in_kev", "is_new_this_week"]


def _write(d, name, obj):
    (d / name).write_text(json.dumps(obj), encoding="utf-8")


def _minimal(d, cards=None, world_obj=None):
    d.mkdir(parents=True, exist_ok=True)
    _write(d, "attack_cards.json", cards if cards is not None else
           [{"card_id": 1, "cve_id": "CVE-2099-0001", "title": "x", "score": 0.3, "in_kev": False}])
    _write(d, "world.json", [world_obj or {"asof_date": "2026-03-22", "score_source": "epss"}])


def test_mock_deck_is_contract_shaped_and_clearly_fake(serving):
    assert serving.source == "serving_mock"
    assert 100 <= len(serving.cards) <= 140
    assert all(c["cve_id"].startswith("CVE-2099-") for c in serving.cards)
    assert len({c["card_id"] for c in serving.cards}) == len(serving.cards)
    assert len(serving.priority) == 500 and serving.priority[0]["rank"] == 1
    assert serving.reveal


def test_mock_files_have_contract_fields(mock_dir):
    cards = json.loads((mock_dir / "attack_cards.json").read_text())
    want = {"card_id", "cve_id", "title", "published_date", "age_days", "vendor", "product", "cwe_id",
            "cvss_base_score", "severity", "score", "score_source", "epss", "epss_percentile", "model_score",
            "in_kev", "technique_id", "technique_name", "tactic", "mapping_source", "is_new_this_week",
            "priority_rank"}
    assert all(set(c) == want for c in cards)
    w = json.loads((mock_dir / "world.json").read_text())
    assert isinstance(w, list) and len(w) == 1
    assert set(w[0]) == {"asof_date", "window_start", "pipeline_run_id", "score_source", "model_version",
                         "challenger_p_at_50", "epss_p_at_50", "challenger_hits_at_50", "epss_hits_at_50",
                         "n_positives", "n_candidates", "n_cves_visible", "n_kev_visible",
                         "n_new_cves_this_week", "n_cards"}
    pp = json.loads((mock_dir / "patch_priority.json").read_text())
    assert set(pp[0]) == {"rank", "cve_id", "score", "score_source", "epss", "model_score", "in_kev", "vendor",
                          "product", "cvss_base_score", "technique_id", "in_deck"}
    rv = json.loads((mock_dir / "reveal.json").read_text())
    assert set(rv[0]) == {"cve_id", "entered_kev_within_91d", "kev_date_added"}


def test_optional_files_missing(tmp_path):
    _minimal(tmp_path)
    d = load_serving(tmp_path)
    assert d.priority == [] and d.reveal == {} and len(d.cards) == 1


def test_nulls_in_every_nullable_field(tmp_path):
    card = {"card_id": 5, "cve_id": "CVE-2099-0005"}
    card.update({k: None for k in NULLABLE_CARD})
    w = {"asof_date": "2026-03-22", "window_start": None, "pipeline_run_id": None, "score_source": None,
         "model_version": None, "challenger_p_at_50": None, "epss_p_at_50": None, "challenger_hits_at_50": None,
         "epss_hits_at_50": None, "n_positives": None, "n_candidates": None, "n_cves_visible": None,
         "n_kev_visible": None, "n_new_cves_this_week": None, "n_cards": None}
    _minimal(tmp_path, [card, {"card_id": None, "cve_id": "CVE-2099-0006", "score": "0.4"},
                        {"card_id": 7, "cve_id": None}], w)
    _write(tmp_path, "patch_priority.json", [{"rank": 1, "cve_id": "CVE-2099-0005", "score": None},
                                             {"rank": None, "cve_id": "CVE-2099-0006"}])
    _write(tmp_path, "reveal.json", [{"cve_id": "CVE-2099-0005", "entered_kev_within_91d": None,
                                      "kev_date_added": None}])
    d = load_serving(tmp_path)
    assert [c["cve_id"] for c in d.cards] == ["CVE-2099-0005", "CVE-2099-0006"]
    c = d.cards[0]
    assert c["score"] == 0.0 and c["in_kev"] is False and c["title"] == "CVE-2099-0005"
    assert c["priority_rank"] == 1 and c["score_source"] == "epss" and c["mapping_source"] == "none"
    assert d.cards[1]["score"] == 0.4
    assert d.world["score_source"] == "epss" and d.world["n_cards"] == 2
    from game import state as st
    s = st.new_match(d, 1, "m-null")
    assert sum(len(n.cards) for n in s.nodes) == 2
    st.snapshot(s)


def test_partial_write_is_retried(tmp_path):
    p = tmp_path / "attack_cards.json"
    p.write_text('[{"card_id": 1, "cve_id": "CVE-20', encoding="utf-8")

    def finish():
        time.sleep(0.2)
        p.write_text('[{"card_id": 1, "cve_id": "CVE-2099-0001", "score": 0.1}]', encoding="utf-8")

    t = threading.Thread(target=finish)
    t.start()
    rows = read_json_array(p, retries=10, delay=0.1)
    t.join()
    assert rows[0]["cve_id"] == "CVE-2099-0001"


def test_partial_write_that_never_finishes_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("[{", encoding="utf-8")
    with pytest.raises(ServingError):
        read_json_array(p, retries=2, delay=0.01)


def test_falls_back_to_mock_when_serving_missing_or_broken(tmp_path, mock_dir):
    empty = tmp_path / "serving"
    empty.mkdir()
    assert load_current(empty, mock_dir).source == "serving_mock"
    _minimal(empty)
    assert load_current(empty, mock_dir).source == "serving"
    (empty / "attack_cards.json").write_text("[]", encoding="utf-8")
    assert load_current(empty, mock_dir).source == "serving_mock"


def test_parse_advance_output():
    out = 'bruin says hi\n{"not": "last"}\n{"ok": true, "asof_date": "2026-03-29", "gate_passed": false}\n'
    assert world.parse_advance_output(out)["asof_date"] == "2026-03-29"
    assert world.parse_advance_output("nothing here") is None


def test_advance_missing_script_gives_clear_error(tmp_path, mock_dir):
    async def go():
        wm = world.WorldManager(tmp_path / "serving", mock_dir, advance_script=tmp_path / "nope" / "advance.py")
        return wm.start_advance()
    res = asyncio.run(go())
    assert res["ok"] is False and "advance.py not found" in res["message"]


def test_advance_runs_in_background_and_reloads(tmp_path, mock_dir):
    serving_dir = tmp_path / "serving"
    _minimal(serving_dir)
    script = tmp_path / "scripts" / "advance.py"
    script.parent.mkdir()
    script.write_text(
        "import json, pathlib, time, sys\n"
        "time.sleep(0.5)\n"
        f"d = pathlib.Path({str(serving_dir)!r})\n"
        "(d / 'world.json').write_text(json.dumps([{'asof_date': '2026-03-29', 'score_source': 'model'}]))\n"
        "print('working...')\n"
        "print(json.dumps({'ok': True, 'asof_date': '2026-03-29', 'score_source': 'model', 'gate_passed': True,"
        " 'message': 'done'}))\n", encoding="utf-8")

    async def go():
        wm = world.WorldManager(serving_dir, mock_dir, advance_script=script)
        changed = []
        wm.on_change(lambda d: changed.append(d.asof))
        t0 = time.monotonic()
        res = wm.start_advance()
        assert time.monotonic() - t0 < 0.3          # returns at once
        assert res["ok"] and wm.advancing
        again = wm.start_advance()
        assert again["ok"] is False and again["advancing"]
        ticks = 0
        while wm.advancing:                          # the event loop keeps running meanwhile
            await asyncio.sleep(0.05)
            ticks += 1
        assert ticks > 3
        assert wm.current.asof == "2026-03-29"       # "advancing" ends only once the new week is loaded
        result = await wm.wait_advance()
        return wm, result, changed
    wm, result, changed = asyncio.run(go())
    assert result["ok"] and result["asof_date"] == "2026-03-29" and result["seconds"] >= 0.5
    assert wm.current.asof == "2026-03-29" and changed == ["2026-03-29"]


def test_watcher_and_advance_announce_a_change_once(tmp_path, mock_dir):
    serving_dir = tmp_path / "serving"
    _minimal(serving_dir)
    script = tmp_path / "scripts" / "advance.py"
    script.parent.mkdir()
    script.write_text(
        "import json, pathlib, time\n"
        "time.sleep(0.3)\n"
        f"d = pathlib.Path({str(serving_dir)!r})\n"
        "(d / 'world.json').write_text(json.dumps([{'asof_date': '2026-03-29', 'score_source': 'model'}]))\n"
        "print(json.dumps({'ok': True, 'asof_date': '2026-03-29', 'score_source': 'model', 'message': 'done'}))\n",
        encoding="utf-8")

    async def go():
        wm = world.WorldManager(serving_dir, mock_dir, advance_script=script, poll_seconds=0.01)
        changed = []
        wm.on_change(lambda d: changed.append(d.asof))
        watcher = asyncio.create_task(wm.watch())
        wm.start_advance()
        await wm.wait_advance()
        await asyncio.sleep(0.2)
        watcher.cancel()
        return changed
    assert asyncio.run(go()) == ["2026-03-29"]
