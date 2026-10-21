#!/usr/bin/env python3
"""
PokéSurvivor x TypeSafe Jev Strategic Bridge
============================================
Local bridge server connecting the Chrome extension reflex engine
with TypeSafe Jev (SystemOne) structured decision API.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning import LearningEngine, canonical_card_key, combo_pair_index  # noqa: E402
from opportunity import WorldModel  # noqa: E402
from auto_iteration import AutoIterSupervisor  # noqa: E402
from journal import Journal, bridge_build  # noqa: E402
from harness_recursion import HarnessRecursion  # noqa: E402

PORT = int(os.environ.get("JEV_BRIDGE_PORT", "8765"))
API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = os.environ.get("JEV_MODEL", "jev-latest")

LOG_DIR = Path(os.environ.get("JEV_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
DECISION_LOG = LOG_DIR / "jev_decisions.jsonl"


def load_api_key() -> str:
    env = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env:
        return env
    key_path = Path.home() / ".config" / "typesafe" / "api_key"
    if key_path.is_file():
        k = key_path.read_text().strip()
        if k:
            return k
    return ""


API_KEY = load_api_key()

STATS = {
    "total_calls": 0,
    "jev_success": 0,
    "fallback_count": 0,
    "avg_latency_ms": 0.0,
}


def log_decision(entry: dict[str, Any]) -> None:
    try:
        with open(DECISION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[Bridge Log Error] {e}", file=sys.stderr)


def call_typesafe_jev(state: dict[str, Any], questions: dict[str, Any], timeout: float | None = None) -> dict[str, Any] | None:
    if timeout is None:
        timeout = float(ENGINE.harness.get("plugins.jev_timeout_s.default"))
    if not API_KEY:
        return None
    if ENGINE.inject_guardrails():
        state = {**state, "guardrails": ENGINE.guardrails_text()}

    body = {
        "model": DEFAULT_MODEL,
        "state": state,
        "questions": questions,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = (time.perf_counter() - t0) * 1000
            STATS["total_calls"] += 1
            if resp.status == 200:
                STATS["jev_success"] += 1
                res = json.loads(resp.read().decode("utf-8"))
                # update moving average latency
                n = STATS["jev_success"]
                STATS["avg_latency_ms"] = (STATS["avg_latency_ms"] * (n - 1) + elapsed) / n
                res["latency_ms"] = round(elapsed, 1)
                return res
    except Exception as err:
        print(f"[Jev API warning] call failed: {err}", file=sys.stderr)
        STATS["fallback_count"] += 1
    return None


ENGINE = LearningEngine(LOG_DIR, jev=lambda s, q, t: call_typesafe_jev(s, q, timeout=t))
OPP = WorldModel(ENGINE)


def _world_brief() -> list:
    return OPP.brief(int(ENGINE.harness.get("memory.shared.extra_context.k")))


ENGINE.extra_context = _world_brief
ENGINE.journal = Journal(LOG_DIR, ENGINE, OPP)
ENGINE.journal.seed()
ENGINE.journal.mine_card_rules()
_np = ENGINE.journal.data.get("next_plan") or {}
if not _np or (_np.get("focus_question") and _np["focus_question"] not in ENGINE.journal.data["questions"]) \
        or str(_np.get("build_id", "")).startswith("test_") or _np.get("bridge_build") != bridge_build():
    ENGINE.journal.plan_next(use_jev=True)
ENGINE.journal.save()
ENGINE.journal.write_markdown()
AUTO_ITER = AutoIterSupervisor(LOG_DIR)
# Harness² loop: JEV_HARNESS_RECURSION=0 turns it off (then the base tree is used for every run, as in PR1).
RECURSION = HarnessRecursion(LOG_DIR, ENGINE)
RECURSION.activate_next()


def _card_key(c: dict[str, Any]) -> str:
    return canonical_card_key(c)


def expected_bot_build() -> str:
    try:
        js_path = Path(__file__).resolve().parent.parent / "extension" / "page_agent.js"
        m = re.search(r'const\s+BOT_BUILD\s*=\s*"([^"]+)"', js_path.read_text(encoding="utf-8"))
        return m.group(1) if m else ""
    except Exception:
        return ""


def handle_levelup(data: dict[str, Any]) -> dict[str, Any]:
    """PURE mode: Jev sees card text, my current moves & observed kills, and my empirical card history."""
    player = data.get("player") or {}
    cards = data.get("cards") or []
    rerolls = int(data.get("rerolls") or 0)
    game_time = float(data.get("time") or 0)
    if not cards:
        return {"action": "pick", "index": 0, "source": "error_no_cards"}

    def done(dec: dict[str, Any]) -> dict[str, Any]:
        log_decision({"event": "levelup", "time": game_time, "player_level": player.get("level"), "decision": dec,
                      "cards": [c.get("title") for c in cards]})
        return dec

    my_moves = player.get("moves") or []
    in_run_buffs = {str(m.get("id") or "").lower() for m in my_moves if m.get("role") == "0-damage support buff"}
    unevolved_ids = {str(m.get("id") or "").lower(): int(m.get("lvl") or 1) for m in my_moves if not (m.get("evolved") or m.get("evo"))}
    unevolved_names = {str(m.get("name") or "").lower(): str(m.get("id") or "").lower() for m in my_moves if not (m.get("evolved") or m.get("evo"))}
    picked_keys = set(data.get("picked_keys") or [])
    if not picked_keys:
        for t in (data.get("picked_titles") or []):
            picked_keys.add(canonical_card_key({"title": t, "key": ""}))

    shortcuts = ENGINE.harness.get("skills.handle_levelup.shortcuts")

    # 1. If a true EVOLUTION card is on screen, pick it immediately (highest empirical power spike)
    if shortcuts["learned_evolution"]["enabled"]:
        for i, c in enumerate(cards):
            ckey = canonical_card_key(c)
            if c.get("type") == "evo" or ckey.startswith("evo:"):
                return done({"action": "pick", "index": i, "card": c.get("title"), "source": "learned_evolution"})

    # 2. If a card's on-screen title explicitly states "<Stat> Evolves <Move>" for one of our un-evolved moves, pick it!
    if shortcuts["on_screen_evolves_hint"]["enabled"]:
        for i, c in enumerate(cards):
            hint = str(c.get("evolves_hint") or "").strip()
            if hint:
                hint_slug = re.sub(r"[^a-z0-9]+", "-", hint.lower()).strip("-")
                if hint_slug in unevolved_ids or hint.lower() in unevolved_names:
                    return done({"action": "pick", "index": i, "card": c.get("title"), "source": "on_screen_evolves_hint"})

    # 3. A SUPPORTED combo my journal mined from my own runs can be completed by a card
    # on screen right now: take it (as advice alone, my history took it only ~1/3 of the time).
    if shortcuts["learned_combo_pair"]["enabled"] and ENGINE.journal:
        supported_conds = [b.get("cond") or {} for b in ENGINE.journal.card_rules(only_supported=True)]
        combo_i = combo_pair_index(cards, unevolved_ids, picked_keys, supported_conds)
        if combo_i is not None:
            return done({"action": "pick", "index": combo_i, "card": cards[combo_i].get("title"), "source": "learned_combo_pair"})

    card_t = ENGINE.harness_text("decisions.handle_levelup.card_selection.criteria.card")
    ann = ENGINE.harness.get("tool_guidance.decisions.handle_levelup.card_selection.annotations")
    level_mem = ENGINE.harness.get("memory.decisions.handle_levelup")
    criteria = {
        f"card_{i}": card_t.format(title=c.get("title"), desc=c.get("desc", ""), experience=ENGINE.card_hint(canonical_card_key(c)))
        for i, c in enumerate(cards)
    }
    if rerolls > 0:
        criteria["reroll"] = ENGINE.harness_text("decisions.handle_levelup.card_selection.criteria.reroll")
    state = {
        "game": "pokesurvivor",
        "goal": ENGINE.decision_goal("handle_levelup"),
        "time_seconds": round(game_time),
        "me": {
            "level": player.get("level"),
            "kills": player.get("kills", 0),
            "kills_per_min": player.get("kpm", 0),
            "hp_pct": round(float(player.get("hpPct", 1)), 2),
            "cards_picked_so_far": data.get("picked_titles") or [],
        },
        "my_moves_now": my_moves,
        "my_lessons": ENGINE.top_lessons(int(level_mem["lessons"])),
    }
    discovered_pairs: dict[str, str] = {}
    required_n: dict[str, int] = {}
    if ENGINE.journal:
        plan = ENGINE.journal.data.get("next_plan") or {}
        prefer = set(plan.get("prefer") or [])
        state["my_plan_this_run"] = {"build_goal": plan.get("build_text"), "question": plan.get("question_text")}
        state["my_beliefs"] = ENGINE.journal.top_beliefs(int(level_mem["beliefs"]))
        state["card_rules_i_discovered"] = [b["claim"] for b in ENGINE.journal.card_rules(only_supported=True)][:int(level_mem["card_rules"])]
        for b in ENGINE.journal.card_rules():
            if b.get("status") in ("supported", "testing"):
                cond = b.get("cond") or {}
                after_k = str(cond.get("after") or "")
                if after_k.startswith("up:"):
                    m_slug = after_k.split(":", 1)[1]
                    required_n[m_slug] = int(cond.get("n") or 4)
                    if cond.get("paired_with"):
                        discovered_pairs[m_slug] = str(cond["paired_with"])
        for i, c in enumerate(cards):
            ckey = canonical_card_key(c)
            if c.get("key") in prefer or ckey in prefer:
                if ckey.startswith("stat:") and ckey in picked_keys:
                    criteria[f"card_{i}"] += ann["already_picked_stat"]
                else:
                    criteria[f"card_{i}"] += ann["plan_wants_card"]

    # Identify highest-level un-evolved damaging attack that still needs upgrades toward evolution
    damaging_unevolved = {
        m_id: lvl for m_id, lvl in unevolved_ids.items()
        if m_id not in in_run_buffs and not ENGINE.is_zero_dmg_move(m_id) and (lvl - 1) < required_n.get(m_id, 4)
    }
    focus_evo_move = max(damaging_unevolved, key=lambda m: (damaging_unevolved[m], ENGINE.card_value(f"up:{m}")[0])) if damaging_unevolved else None

    for i, c in enumerate(cards):
        ckey = canonical_card_key(c)
        if ckey.startswith("up:"):
            m_id = ckey.split(":", 1)[1]
            if m_id in in_run_buffs or ENGINE.is_zero_dmg_move(m_id):
                criteria[f"card_{i}"] += ann["zero_damage_buff"]
            elif m_id in unevolved_ids:
                lvl = unevolved_ids[m_id]
                if m_id == focus_evo_move and lvl >= 2:
                    criteria[f"card_{i}"] += ENGINE.harness_text(
                        "decisions.handle_levelup.card_selection.annotations.top_priority_evolution", lvl=lvl)
                else:
                    criteria[f"card_{i}"] += ENGINE.harness_text(
                        "decisions.handle_levelup.card_selection.annotations.upgrades_equipped_attack", lvl=lvl)
        elif ckey.startswith("stat:") and ckey not in picked_keys:
            for m_id in unevolved_ids:
                if discovered_pairs.get(m_id) == ckey:
                    criteria[f"card_{i}"] += ENGINE.harness_text(
                        "decisions.handle_levelup.card_selection.annotations.journal_stat_pair", m_id=m_id)
    damaging_moves = [m for m in my_moves if m.get("role") != "0-damage support buff" and not ENGINE.is_zero_dmg_move(str(m.get("id") or ""))]
    early = shortcuts["need_early_attack"]
    need_early_attack = (
        bool(early["enabled"])
        and int(player.get("level") or 1) <= int(early["max_level"])
        and len(damaging_moves) < int(early["damaging_moves_below"])
    )
    if need_early_attack:
        for i, c in enumerate(cards):
            ckey = canonical_card_key(c)
            m_id = ckey.split(":", 1)[1] if ":" in ckey else ckey
            if (ckey.startswith("new:") or c.get("type") == "new") and not ENGINE.is_zero_dmg_move(m_id):
                criteria[f"card_{i}"] += ann["need_early_attack"]

    spec = ENGINE.harness.get("tool_guidance.decisions.handle_levelup.card_selection")
    instructions = spec["instructions"]
    if need_early_attack:
        instructions += ENGINE.harness_text("decisions.handle_levelup.card_selection.instructions_early_survival")

    q = {"card_selection": {"type": spec["type"], "criteria": criteria, "instructions": instructions}}
    res = call_typesafe_jev(state, q, timeout=ENGINE.jev_timeout("handle_levelup"))
    if res and "answers" in res:
        a = res["answers"].get("card_selection", {})
        ch = a.get("choice") or LearningEngine._sample(a)
        if ch == "reroll" and rerolls > 0:
            return done({"action": "reroll", "source": "jev", "confidence": a.get("confidence")})
        if ch.startswith("card_"):
            idx = int(ch.split("_")[1])
            if 0 <= idx < len(cards):
                return done({"action": "pick", "index": idx, "card": cards[idx].get("title"), "source": "jev",
                             "confidence": a.get("confidence"), "latency_ms": res.get("latency_ms", 0)})

    weights = {
        item["id"]: float(item["value"])
        for item in ENGINE.harness.get("skills.handle_levelup.fallback_score.weights")
    }

    def fallback_score(i: int) -> float:
        ckey = canonical_card_key(cards[i])
        val, seen_n, _, _ = ENGINE.card_value(ckey)
        bonus = weights["unseen"] if seen_n == 0 else 0.0
        m_id = ckey.split(":", 1)[1] if ":" in ckey else ckey
        if m_id in in_run_buffs or ENGINE.is_zero_dmg_move(m_id):
            bonus += weights["zero_damage"]
        if need_early_attack and (ckey.startswith("new:") or cards[i].get("type") == "new") and not ENGINE.is_zero_dmg_move(m_id):
            bonus += weights["early_attack"]
        if ckey.startswith("up:") and m_id in unevolved_ids and m_id not in in_run_buffs and not ENGINE.is_zero_dmg_move(m_id):
            bonus += weights["upgrade_base"] + weights["upgrade_per_level"] * unevolved_ids[m_id]
        if ckey.startswith("stat:"):
            if ckey in picked_keys:
                bonus += weights["stat_repeat"]
            elif any(discovered_pairs.get(m) == ckey for m in unevolved_ids):
                bonus += weights["paired_stat"]
        return val + bonus

    best = max(range(len(cards)), key=fallback_score)
    return done({"action": "pick", "index": best, "card": cards[best].get("title"), "source": "experience_fallback"})


def handle_item(data: dict[str, Any]) -> dict[str, Any]:
    """PURE mode: keep or swap a held item using the shown descriptions + my results with each item."""
    cur, new = data.get("current") or {}, data.get("offered") or {}
    if isinstance(cur, str):
        cur = {"id": cur, "title": cur}
    if isinstance(new, str):
        new = {"id": new, "title": new}
    game_time = data.get("time", 0)
    item_spec = ENGINE.harness.get("tool_guidance.decisions.handle_item.item_decision")
    crit = {
        "keep_current": item_spec["criteria"]["keep_current"].format(
            title=cur.get("title"), desc=cur.get("desc", ""), experience=ENGINE.card_hint("item:" + str(cur.get("id")))),
        "equip_new": item_spec["criteria"]["equip_new"].format(
            title=new.get("title"), desc=new.get("desc", ""), experience=ENGINE.card_hint("item:" + str(new.get("id")))),
    }
    action, source = "keep", "experience_fallback"
    res = call_typesafe_jev({"game": "pokesurvivor", "time": game_time,
                             "my_lessons": ENGINE.top_lessons(ENGINE.memory_int("handle_item", "lessons"))},
                            {"item_decision": {"type": item_spec["type"], "criteria": crit,
                                               "instructions": item_spec["instructions"]}},
                            timeout=ENGINE.jev_timeout("handle_item"))
    if res and "answers" in res:
        a = res["answers"].get("item_decision", {})
        ch = a.get("choice") or LearningEngine._sample(a)
        if ch in ("keep_current", "equip_new"):
            action, source = ("equip" if ch == "equip_new" else "keep"), "jev"
    else:
        if ENGINE.card_value("item:" + str(new.get("id")))[0] > ENGINE.card_value("item:" + str(cur.get("id")))[0]:
            action = "equip"
    decision = {"action": action, "index": 1 if action == "equip" else 0, "source": source,
                "current": cur.get("id"), "offered": new.get("id")}
    log_decision({"event": "item", "time": game_time, "decision": decision})
    return decision


def handle_bond(data: dict[str, Any]) -> dict[str, Any]:
    """Decide which moves to fuse so they fire concurrently."""
    player = data.get("player") or {}
    mode = data.get("mode", "pair")
    loose = data.get("loose") or []
    game_time = data.get("time", 0)

    if not loose:
        return {"action": "pick", "index": 0, "source": "fallback"}

    # Pair mode: choose first move to bond
    bond = ENGINE.harness.get("tool_guidance.decisions.handle_bond.bond_pick")
    move_t = bond["criteria"]["move"]
    criteria = {
        f"move_{i}": move_t.format(name=m.get("name", m.get("id", "")), lvl=m.get("lvl", 1))
        for i, m in enumerate(loose)
    }
    state = {
        "game": "pokesurvivor",
        "time": game_time,
        "bond_mode": mode,
        "moves_available": [m.get("name") for m in loose]
    }
    questions = {
        "bond_pick": {
            "type": bond["type"],
            "instructions": bond["instructions"],
            "criteria": criteria,
        }
    }
    jev_res = call_typesafe_jev(state, questions, timeout=ENGINE.jev_timeout("handle_bond"))
    pick_idx = 0
    source = "fallback"
    if jev_res and "answers" in jev_res:
        ch = jev_res["answers"].get("bond_pick", {}).get("choice", "")
        if ch.startswith("move_"):
            try:
                pick_idx = int(ch.split("_")[1])
                source = "jev"
            except Exception:
                pick_idx = 0

    decision = {"action": "pick", "index": pick_idx, "source": source}
    log_decision({"event": "bond", "time": game_time, "decision": decision})
    return decision


def handle_tactics(data: dict[str, Any]) -> dict[str, Any]:
    """Disabled in pure-learning mode (it contained hand-written game rules)."""
    return {"stance": "learned", "source": "pure_mode"}

ENGINE_LOCK = threading.Lock()


def handle_run_start(data: dict[str, Any]) -> dict[str, Any]:
    with ENGINE_LOCK:
        res = ENGINE.start_run(data)
        res["world_model"] = OPP.snapshot()
        res["expected_bot_build"] = expected_bot_build()
        harness_id = RECURSION.on_run_start(res["run_id"])
        if harness_id:
            res["harness_id"] = harness_id
    log_decision({"event": "run_start", **res})
    print(f"[Learning] ▶ Run #{res['run_number']} experiment={res['experiment']} ({res['source']}) :: {res['hypothesis']}")
    return res


def handle_checkpoint(data: dict[str, Any]) -> dict[str, Any]:
    res = ENGINE.checkpoint(data)
    log_decision({"event": "checkpoint", "t": data.get("t"), "verdict": res["verdict"],
                  "posture": res["posture"], "source": res["source"]})
    return res


def handle_run_end(data: dict[str, Any]) -> dict[str, Any]:
    persist = not bool(data.get("dry_run"))
    with ENGINE_LOCK:
        if persist and ENGINE.is_duplicate(data):
            res = {"status": "duplicate", "run": None, "lesson": "duplicate run ignored (already recorded)",
                   "decision": "duplicate", "verdict": "n/a", "diagnosis": "n/a", "source": "dedupe",
                   "expected_bot_build": expected_bot_build()}
            log_decision({"event": "run_end_duplicate", "run_id": data.get("run_id"), "time": data.get("time")})
            print(f"[Learning] ⨯ duplicate run ignored (run_id={data.get('run_id')}, t={data.get('time')})")
            return res
        if persist:
            OPP.record_partner_result(str(data.get("partner") or ""), float(data.get("time") or 0))
            OPP.merge(data.get("world_deltas") or {})
        trial = copy.deepcopy(ENGINE.kb["pending"].get(str(data.get("run_id") or ""))) if persist else None
        res = ENGINE.end_run(data, persist=persist)
        if persist:
            OPP.update_upgrade_effects()
            AUTO_ITER.audit_and_tune(ENGINE.kb, ENGINE)
            ENGINE.save()
            try:
                harness_res = RECURSION.after_run(data, trial=trial, result=res)
            except Exception as e:
                print(f"[Harness] after_run failed: {e}", file=sys.stderr)
                harness_res = {"status": "error", "error": str(e)}
            if harness_res is not None:
                res["harness"] = harness_res
        res["world_model"] = OPP.snapshot()
        res["expected_bot_build"] = expected_bot_build()
    log_decision({"event": "run_end", "dry_run": not persist, **{k: res.get(k) for k in
                  ("run", "lesson", "decision", "verdict", "diagnosis", "source", "focus_verdict", "new_questions", "next_plan", "harness")}})
    print(f"[Learning] ■ {res['lesson']}")
    if res.get("next_plan"):
        np_ = res["next_plan"]
        print(f"[Journal] next: ❓ {np_.get('question_text')} | 🎮 {np_.get('stance_label')} | 🃏 {np_.get('build_text')} | 🐉 {np_.get('partner_name')}")
    if persist:
        try:
            (LOG_DIR / "LEARNING_REPORT.md").write_text(ENGINE.report_markdown(), encoding="utf-8")
        except Exception as e:
            print(f"[Learning] report write failed: {e}", file=sys.stderr)
    return res


def handle_opportunity(data: dict[str, Any]) -> dict[str, Any]:
    res = OPP.decide(data)
    log_decision({"event": "opportunity", "t": data.get("t"), **res})
    print(f"[Opportunity] {res['type']}: {'ENGAGE' if res['engage'] else 'skip'} ({res['source']}) {res.get('reason', '')}")
    return res


def handle_opportunity_outcome(data: dict[str, Any]) -> dict[str, Any]:
    with ENGINE_LOCK:
        res = OPP.outcome(data)
    log_decision({"event": "opportunity_outcome", **data})
    print(f"[Opportunity] outcome {res['evidence']}")
    return res


def handle_meta(data: dict[str, Any]) -> dict[str, Any]:
    with ENGINE_LOCK:
        res = OPP.decide_meta(data)
        if ENGINE.journal:
            ENGINE.journal.save()
    log_decision({"event": "meta", **res})
    print(f"[Meta] partner={res['partner_dex']} ({res['partner_source']})")
    return res


def handle_upgrade(data: dict[str, Any]) -> dict[str, Any]:
    with ENGINE_LOCK:
        res = OPP.decide_upgrade(data)
        ENGINE.save()
    log_decision({"event": "upgrade", **res})
    print(f"[Meta] upgrade -> {res['buy']} ({res['source']})")
    return res


class BridgeHandler(BaseHTTPRequestHandler):
    def _send_cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def _reply(self, payload: Any, ctype: str = "application/json") -> None:
        body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._reply({"status": "ok", "typesafe_key_present": bool(API_KEY), "model": DEFAULT_MODEL,
                         "expected_bot_build": expected_bot_build(),
                         "harness": {"enabled": RECURSION.enabled, "active": RECURSION.active_id, "current": RECURSION.lib.get("current")},
                         "stats": STATS, "total_runs": ENGINE.kb["total_runs"], "best_time": ENGINE.kb["best_time"]})
        elif self.path in ("/memory", "/knowledge"):
            self._reply(ENGINE.summary())
        elif self.path == "/kb":
            self._reply({"mode": "pure_learning", "learned_brief": OPP.brief(20), "world_model": OPP.snapshot(),
                         "discoveries": ENGINE.kb.get("discoveries"), "partners": ENGINE.kb.get("partners"),
                         "upgrades": ENGINE.kb.get("upgrades_hist")})
        elif self.path == "/report":
            self._reply(ENGINE.report_markdown().encode("utf-8"), "text/markdown; charset=utf-8")
        elif self.path == "/journal":
            self._reply(ENGINE.journal.data if ENGINE.journal else {})
        elif self.path == "/harness":
            self._reply(RECURSION.status())
        elif self.path == "/harness/library":
            self._reply(RECURSION.lib)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8") if content_len else "{}"
        try:
            req_data = json.loads(post_body)
        except Exception:
            req_data = {}

        routes = {
            "/decide/levelup": handle_levelup,
            "/decide/item": handle_item,
            "/decide/bond": handle_bond,
            "/decide/tactics": handle_tactics,
            "/run/start": handle_run_start,
            "/run/checkpoint": handle_checkpoint,
            "/run/end": handle_run_end,
            "/decide/postmortem": lambda d: {"status": "ignored_legacy", "hint": "reload the extension + refresh the game tab"},
            "/decide/opportunity": handle_opportunity,
            "/opportunity/outcome": handle_opportunity_outcome,
            "/decide/meta": handle_meta,
            "/decide/upgrade": handle_upgrade,
        }
        if self.path == "/telemetry":
            log_decision({"event": "telemetry", "data": req_data})
            self._reply({"status": "recorded"})
            return
        fn = routes.get(self.path)
        if not fn:
            self.send_response(404)
            self.end_headers()
            return
        try:
            result = fn(req_data)
        except Exception as e:
            print(f"[Bridge] handler error on {self.path}: {e}", file=sys.stderr)
            result = {"error": str(e), "source": "bridge_error"}
        self._reply(result)

    def log_message(self, format: str, *args: Any) -> None:
        if "/health" not in str(args[0]) and "/checkpoint" not in str(args[0]):
            print(f"[Bridge] {self.address_string()} - {format % args}")


def main() -> None:
    print("==================================================")
    print(" PokéSurvivor x Jev Bridge - MINIMAL RULES (Jev decides, code = body + memory)")
    print(f" Port: {PORT} | Model: {DEFAULT_MODEL} | Key: {bool(API_KEY)}")
    print(f" Knowledge: {ENGINE.kb_file}  (runs so far: {ENGINE.kb['total_runs']}, best {ENGINE.kb['best_time']}s)")
    print(f" Report:    {LOG_DIR / 'LEARNING_REPORT.md'}  |  GET /report")
    print("==================================================")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), BridgeHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Jev bridge server.")
        httpd.server_close()


if __name__ == "__main__":
    main()
