#!/usr/bin/env python3
"""Golden parity for every Jev decision payload.

Sets HOME, TYPESAFE_API_KEY and JEV_LOG_DIR to a temp directory, blocks
network, then imports jev_bridge and records call_typesafe_jev arguments.

    python3 bridge/test_harness_parity.py
    python3 bridge/test_harness_parity.py --update

--update rewrites bridge/testdata/jev_payloads_golden.json from the live
call sites. Run it before editing those call sites, and commit the golden
with this test. After a refactor the captured state, questions and timeout
must match the golden exactly.
"""
from __future__ import annotations

import copy
import difflib
import json
import os
import random
import socket
import sys
import tempfile
import urllib.request
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["HOME"] = _TMP.name
os.environ["TYPESAFE_API_KEY"] = "redacted"
os.environ["JEV_LOG_DIR"] = str(Path(_TMP.name) / "logs")

_NET_HITS = 0


def _block_network(*_args, **_kwargs):
    global _NET_HITS
    _NET_HITS += 1
    raise OSError("network disabled")


urllib.request.urlopen = _block_network
socket.create_connection = _block_network

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_bridge as jb  # noqa: E402
from journal import default_journal  # noqa: E402
from learning import DEFAULT_POLICY, default_knowledge  # noqa: E402

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "testdata" / "jev_payloads_golden.json"

CALLS: list[dict] = []
_NET_AFTER_IMPORT = _NET_HITS


def _capture(state, questions, timeout=6.0):
    CALLS.append(copy.deepcopy({"state": state, "questions": questions, "timeout": timeout}))
    return None


jb.call_typesafe_jev = _capture

CASE_ORDER = (
    "handle_levelup",
    "handle_item",
    "handle_bond",
    "handle_tactics",
    "handle_run_start",
    "handle_checkpoint",
    "handle_run_end",
    "handle_opportunity",
    "handle_meta",
    "handle_upgrade",
    "plan_next",
)

# Call counts of the pre-refactor handlers on the fixture below. A refactor
# that drops or adds a Jev call fails here even if someone regenerates carelessly
# without noticing an empty capture.
EXPECTED_CALLS = {
    "handle_levelup": 1,
    "handle_item": 1,
    "handle_bond": 1,
    "handle_tactics": 0,
    "handle_run_start": 0,
    "handle_checkpoint": 1,
    "handle_run_end": 2,
    "handle_opportunity": 1,
    "handle_meta": 2,
    "handle_upgrade": 1,
    "plan_next": 1,
}


def _belief(claim: str, confidence: float, kind: str = "hunch", status: str = "hunch", cond=None) -> dict:
    return {
        "claim": claim,
        "about": claim,
        "kind": kind,
        "status": status,
        "confidence": confidence,
        "for": [1],
        "against": [],
        "origin": "fixture",
        "cond": cond,
        "updated": 1.0,
    }


def _card_rule(move: str, stat: str, confidence: float) -> dict:
    return _belief(
        f"'{move} evo' shows up after I upgrade/pick '{move}' 4x and pick '{stat}' (fixture)",
        confidence,
        kind="card",
        status="supported",
        cond={"mined": True, "card": f"evo:{move}", "after": f"up:{move}", "n": 4, "paired_with": f"stat:{stat}"},
    )


def _lesson(text: str, confidence: float) -> dict:
    return {"text": text, "kind": "observation", "confidence": confidence, "evidence_runs": [1], "updated": confidence}


def _run(i: int, partner: str) -> dict:
    return {
        "run": i,
        "run_id": f"fix-{i}",
        "time": float(30 + i),
        "level": i,
        "kills": 10 * i,
        "boss_kills": 0,
        "power_score": 100 + i,
        "cause": "enemy:Rattata",
        "phase": "early",
        "diagnosis": "enemy:Rattata",
        "verdict": "unclear",
        "policy": {"aggression": 1.5},
        "why": "fixture",
        "partner": partner,
        "ts": float(i),
    }


def _knowledge() -> dict:
    kb = default_knowledge()
    kb["total_runs"] = 8
    kb["best_time"] = 120.0
    kb["best_score"] = 400.0
    kb["best_level"] = 10
    kb["best_run_id"] = "best-1"
    kb["best_policy"] = dict(DEFAULT_POLICY)
    kb["lessons"] = {f"L{i}": _lesson(f"lesson {i}", round(0.90 - i * 0.01, 2)) for i in range(8)}
    kb["runs"] = [_run(i, "Bulbasaur" if i == 8 else "Charmander") for i in range(1, 9)]
    kb["danger"] = {
        f"enemy:mob{i}": [[20.0, float(i + 1)], [10.0, 0.2], [1.0, 0.0], [1.0, 0.0]] for i in range(6)
    }
    kb["objects"] = {
        f"object:chest{i}": {
            "seen": 1, "tried": 0, "positive": 0, "died": 0, "sum": {}, "hp_cost": 0.0, "time": 0.0, "messages": [],
        }
        for i in range(4)
    }
    kb["partners"] = {
        "Bulbasaur": {"runs": 2, "time_sum": 180.0, "best": 120.0},
        "Charmander": {"runs": 1, "time_sum": 90.0, "best": 90.0},
    }
    kb["upgrades_hist"] = {"upg_a": {"bought": 1, "bought_at_run": [3], "runs_after": 2, "avg_delta": 15}}
    kb.setdefault("pickups", {})
    kb.setdefault("kills", {})
    kb.setdefault("discoveries", {})
    return kb


def _journal() -> dict:
    data = default_journal()
    data["seeded"] = True
    data["seed_version"] = 2
    beliefs = {}
    for i, conf in enumerate((0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92)):
        beliefs[f"h{i}"] = _belief(f"hunch {i}", conf)
    for i, move in enumerate(("gust", "ember", "water", "vine", "quick", "scratch")):
        beliefs[f"c{i}"] = _card_rule(move, "speed" if move == "gust" else f"stat{i}", round(0.50 - i * 0.02, 2))
    data["beliefs"] = beliefs
    data["questions"] = {
        "q_shrine": {
            "ask": "What happens when I go to the shrine?",
            "about": "object:shrine",
            "kind": "object",
            "status": "open",
            "priority": 0.9,
            "times_focused": 1,
            "origin": "seen once",
            "candidates": [],
            "created": 1.0,
            "answer": None,
        },
        "q_partner": {
            "ask": "What is it like to play as Squirtle?",
            "about": "partner:7",
            "kind": "partner",
            "status": "open",
            "priority": 0.4,
            "times_focused": 0,
            "origin": "I own it but never played it",
            "candidates": [],
            "created": 2.0,
            "answer": None,
        },
    }
    data["topic_stats"] = {"object:shrine": {"tested": 1, "untestable": 1}}
    data["topic_stats_v1"] = True
    data["diary"] = [{"text": f"diary {i}"} for i in range(1, 5)]
    data["stance_stats"] = {"aggressive_farmer": {"n": 2, "score_sum": 300.0, "best": 200.0}}
    data["meta_seen"] = {
        "owned": [
            {"dex": 1, "name": "Bulbasaur", "hp": 45, "atk": 49, "spd": 45, "rarity": 1},
            {"dex": 4, "name": "Charmander", "hp": 39, "atk": 52, "spd": 65, "rarity": 2},
            {"dex": 7, "name": "Squirtle", "hp": 44, "atk": 48, "spd": 43, "rarity": 1},
        ],
        "tickets": {"t1": 2, "t10": 0},
        "gacha_pulls": 1,
        "save_streak": 1,
        "partners_gained": [{"name": "Pidgey"}],
    }
    data["next_plan"] = {
        "build_text": "chase gust",
        "question_text": "What happens when I go to the shrine?",
        "prefer": ["stat:power", "new:ember"],
        "focus_question": "q_shrine",
        "stance": "aggressive_farmer",
        "build_id": "free",
        "partner_dex": None,
        "partner_name": "Bulbasaur",
        "policy": dict(DEFAULT_POLICY),
        "source": "fixture",
    }
    data["cause_stance"] = {}
    return data


_OWNED = [
    {"dex": 1, "name": "Bulbasaur", "hp": 45, "atk": 49, "spd": 45, "rarity": 1},
    {"dex": 4, "name": "Charmander", "hp": 39, "atk": 52, "spd": 65, "rarity": 2},
    {"dex": 7, "name": "Squirtle", "hp": 44, "atk": 48, "spd": 43, "rarity": 1},
]

_LEVELUP_CARDS = [
    {"title": "Power Upgrade", "key": "stat:power", "type": "stat", "desc": "raises power"},
    {"title": "Ember New", "key": "new:ember", "type": "new", "desc": "a flame"},
    {"title": "Gust Nv.2 > 3", "key": "up:gust", "type": "up", "desc": "stronger gust"},
    {"title": "Growl Nv.1 > 2", "key": "up:growl", "type": "up", "desc": "lowers attack"},
    {"title": "Tackle New", "key": "new:tackle", "type": "new", "desc": "a tackle"},
    {"title": "Speed Upgrade", "key": "stat:speed", "type": "stat", "desc": "raises speed"},
]


def install() -> None:
    eng = jb.ENGINE
    for path in eng.runs_dir.glob("*.json"):
        path.unlink()
    run_file = {
        "run_id": "best-1",
        "time": 120,
        "level": 10,
        "kills": 80,
        "partner": "Bulbasaur",
        "power_score": 400,
        "picks": [
            {"key": "up:gust", "title": "Gust", "offered": ["up:gust", "stat:power"]},
            {"key": "stat:power", "title": "Power", "offered": ["stat:power"]},
        ],
        "moves": [{"id": "gust", "evolved": False}],
        "trial": {"plan": {"build_id": "free", "stance": "aggressive_farmer"}},
    }
    (eng.runs_dir / "run_0001_best-1.json").write_text(json.dumps(run_file), encoding="utf-8")
    eng.kb.clear()
    eng.kb.update(_knowledge())
    eng.journal.data.clear()
    eng.journal.data.update(_journal())
    random.seed(12345)


def _cases() -> dict:
    def levelup():
        jb.handle_levelup({
            "time": 42,
            "rerolls": 1,
            "player": {
                "level": 4,
                "kills": 12,
                "kpm": 20,
                "hpPct": 0.5,
                "moves": [
                    {"id": "gust", "name": "Gust", "lvl": 3, "role": "attack", "evolved": False},
                    {"id": "growl", "name": "Growl", "lvl": 1, "role": "0-damage support buff", "evolved": False},
                ],
            },
            "cards": _LEVELUP_CARDS,
            "picked_keys": ["stat:power"],
            "picked_titles": ["Power Upgrade"],
        })

    def item():
        jb.handle_item({
            "time": 50,
            "current": {"id": "berry", "title": "Berry", "desc": "heals a little"},
            "offered": {"id": "scope", "title": "Scope", "desc": "see farther"},
        })

    def bond():
        jb.handle_bond({
            "time": 60,
            "mode": "pair",
            "player": {"level": 6},
            "loose": [
                {"id": "gust", "name": "Gust", "lvl": 3},
                {"id": "tackle", "name": "Tackle", "lvl": 1},
            ],
        })

    def tactics():
        jb.handle_tactics({"time": 10})

    def run_start():
        jb.handle_run_start({"client_run_id": "parity-start", "partner": "Bulbasaur", "bot_build": "parity"})

    def checkpoint():
        jb.handle_checkpoint({"t": 80, "level": 5, "hp_pct": 0.5, "kills": 20, "enemies": 4, "dmg_per_min": 30})

    def run_end():
        jb.handle_run_end({
            "run_id": "parity-end-1",
            "time": 95.5,
            "level": 7,
            "kills": 33,
            "boss_kills": 0,
            "partner": "Bulbasaur",
            "death_cause": "enemy:Rattata",
            "damage_sources": {"enemy:Rattata": 50},
            "picks": [],
            "timeline": [],
            "messages": ["hello"],
            "moves": [],
            "world_deltas": {},
        })

    def opportunity():
        jb.handle_opportunity({
            "type": "shrine",
            "hp_pct": 0.7,
            "level": 4,
            "explore": True,
            "distance": 40,
            "enemies_near": 2,
            "t": 90,
            "messages": ["a sign"],
        })

    def meta():
        jb.handle_meta({"owned": _OWNED, "current": _OWNED[0], "tickets": {"t1": 2, "t10": 0}})

    def upgrade():
        jb.handle_upgrade({
            "coins": 100,
            "options": [
                {"id": "upg_a", "name": "Vitality", "desc": "more hp", "level": 1, "max": 5, "cost": 50, "affordable": True},
                {"id": "upg_b", "name": "Might", "desc": "more atk", "level": 0, "max": 5, "cost": 40, "affordable": True},
            ],
        })

    def plan_next():
        jb.ENGINE.journal.plan_next(use_jev=True)

    fns = {
        "handle_levelup": levelup,
        "handle_item": item,
        "handle_bond": bond,
        "handle_tactics": tactics,
        "handle_run_start": run_start,
        "handle_checkpoint": checkpoint,
        "handle_run_end": run_end,
        "handle_opportunity": opportunity,
        "handle_meta": meta,
        "handle_upgrade": upgrade,
        "plan_next": plan_next,
    }
    out = {}
    for name in CASE_ORDER:
        install()
        CALLS.clear()
        fns[name]()
        out[name] = copy.deepcopy(CALLS)
    return out


def canon(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def test_payloads_match_golden(update: bool) -> None:
    payload = _cases()
    counts = {name: len(payload[name]) for name in CASE_ORDER}
    print("captured calls:", counts)
    mismatches = {name: counts[name] for name in CASE_ORDER if counts[name] != EXPECTED_CALLS[name]}
    if mismatches:
        print("FAIL call counts", mismatches, "expected", EXPECTED_CALLS)
        sys.exit(1)
    if _NET_HITS != _NET_AFTER_IMPORT:
        print(f"FAIL network used during cases ({_NET_HITS - _NET_AFTER_IMPORT} new hits)")
        sys.exit(1)
    text = canon(payload)
    if update:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
        print(f"updated {GOLDEN}")
        return
    if not GOLDEN.is_file():
        print(f"FAIL missing {GOLDEN}; run python3 bridge/test_harness_parity.py --update")
        sys.exit(1)
    want = GOLDEN.read_text(encoding="utf-8")
    if text != want:
        diff = "".join(difflib.unified_diff(
            want.splitlines(True), text.splitlines(True), fromfile="golden", tofile="live",
        ))
        print(diff[:12000])
        print("FAIL payloads differ from golden")
        sys.exit(1)
    print("PASS harness parity (state, questions, timeout match golden)")


if __name__ == "__main__":
    test_payloads_match_golden("--update" in sys.argv)
