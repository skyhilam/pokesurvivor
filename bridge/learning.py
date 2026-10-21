#!/usr/bin/env python3
"""
Minimal-rules learner: Jev is the brain, the code is only body + memory
=======================================================================
Goal: clear 31:00 by becoming strong enough to wipe dense waves and bosses.
Fleeing without killing starves XP, freezes levels, and guarantees death.
Power-Scaling & Combat-Focused Learning Engine (Version 4).

Pure observation only:
  - Perception of screen elements (positions, tags, text, resources)
  - Damage attribution to nearby entities within an eligibility window
  - Evaluation by active combat progress (power score = combat time + kills + levels)
  - Card attribution from observed post-pick kill-rate deltas + power advantage
"""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from harness_tree import HarnessTree

JevFn = Callable[[dict, dict, float], Optional[dict]]

# knob: (min, max, default, kind, meaning)   kind: mul = multiplicative, add = additive, opt = discrete options
KNOBS: dict[str, tuple] = {
    "aggression":     (0.2, 4.0, 1.5, "mul", "how strongly I approach enemies into my attack range to kill them and farm XP"),
    "combat_range":   (60, 180, 110, "add", "preferred distance in px from enemies where I try to fight and circle them"),
    "orbit":          (0.2, 3.0, 1.3, "mul", "how strongly I circle/kite tangentially around enemy packs to hit them and loop back for dropped XP"),
    "greed":          (0.2, 4.0, 1.8, "mul", "how strongly I go for XP gems and valuable pickups to level up faster"),
    "fear":           (0.2, 3.5, 1.0, "mul", "how strongly I steer away from overlapping/touching things that have hurt me"),
    "telegraph_fear": (0.2, 4.0, 1.2, "mul", "how strongly I steer away from ground markings that have hurt me"),
    "keep_distance":  (-10, 60, 0, "add", "extra pixels I keep between me and things that have hurt me"),
    "w_level":        (0, 100, 40, "add", "how much I value gaining a level"),
    "w_xp":           (0, 100, 30, "add", "how much I value filling the XP bar"),
    "w_hp":           (0, 100, 35, "add", "how much I value recovering HP"),
    "w_coins":        (0, 5, 0.3, "add", "how much I value one coin"),
    "w_tickets":      (0, 100, 15, "add", "how much I value one ticket"),
    "w_bonds":        (0, 100, 25, "add", "how much I value one bond"),
    "rotate_s":       (0, 8, 2, "opt", "switch between damaging move slots every N seconds (0 = stay on highest kill-rate move)"),
    "explore":        (0, 1, 1, "opt", "1 = go look at things I have never investigated, 0 = ignore unknown things"),
    "roam":           (0, 4, 1, "mul", "how strongly I wander across the map to find new things when nothing is nearby"),
}
DEFAULT_POLICY = {k: v[2] for k, v in KNOBS.items()}
PRIOR_N = 3.0
MAX_RUNS = 300


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _phase(t: float) -> str:
    return "final" if t >= 1800 else "late" if t >= 1200 else "mid" if t >= 300 else "early"


def apply_step(knob: str, value: float, step: int) -> float:
    lo, hi, default, kind, _ = KNOBS[knob]
    if kind == "mul":
        v = value * (1.35 ** step)
    elif kind == "add":
        span = (hi - lo) * 0.12
        v = value + step * max(span, abs(value) * 0.25)
    else:
        return value
    return round(_clamp(v, lo, hi), 3)


def canonical_card_key(card: dict[str, Any]) -> str:
    """Derive a canonical key from visible text, avoiding icon Map collisions."""
    title = (card.get("title") or "").strip()
    tag = (card.get("tag") or "").strip()
    key = str(card.get("key") or "")
    if key in ("evo:power", "evo:power-upgrade"):
        return "stat:power"
    if key in ("evo:sprint", "evo:sprint-upgrade"):
        return "stat:sprint"
    if key in ("evo:armor", "evo:armor-upgrade"):
        return "stat:armor"
    if key.startswith("stat:") and not (" " in key):
        return key

    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

    has_arrow = "→" in title or "->" in title
    m_hint = re.match(r"^(.*?)\s+evolves\s+(.*)$", title, re.IGNORECASE) or re.match(r"^evolves\s+(.*)$", tag, re.IGNORECASE)
    if not has_arrow and m_hint:
        stat_name = m_hint.group(1).strip() if (m_hint.lastindex and m_hint.lastindex >= 2 and m_hint.group(1)) else title.replace("Upgrade", "").strip()
        return f"stat:{slug(stat_name)}"

    if has_arrow or (card.get("type") == "evo" and "evolves" not in title.lower()) or re.search(r"\bEVOLUTION\b", title, re.I):
        clean = re.sub(r"\s+EVOLUTION\b", "", title, flags=re.I).strip()
        clean = re.split(r"\s+Evo\b", clean, flags=re.I)[0].strip()
        return f"evo:{slug(clean)}"

    if " Nv." in title or " Lv." in title or ">" in title:
        base = re.split(r"\s+[NL]v\.", title)[0].strip()
        return f"up:{slug(base)}"
    if " New" in title:
        base = title.split(" New")[0].strip()
        return f"new:{slug(base)}"
    if title.endswith("Upgrade"):
        base = title[:-7].strip()
        return f"stat:{slug(base)}"
    return key or f"card:{slug(title)}"


def combo_pair_index(cards: list[dict[str, Any]], unevolved_ids: dict[str, int],
                     picked_keys: set[str], rule_conds: list[dict[str, Any]]) -> int | None:
    """Index of the paired stat card that completes a mined combo, else None.

    A mined rule says: after I upgraded move X n times and picked stat S once,
    a special card for X showed up. When X is equipped and un-evolved, has been
    upgraded at least n times (lvl - 1, as shown on screen), S is not picked yet
    this run, and S is one of the cards on screen, that card finishes the combo.
    Rules come only from my own run history comparisons (journal.mine_card_rules).
    """
    for cond in rule_conds:
        after_k = str(cond.get("after") or "")
        paired = str(cond.get("paired_with") or "")
        if not (after_k.startswith("up:") and paired.startswith("stat:")):
            continue
        m_id = after_k.split(":", 1)[1]
        if m_id not in unevolved_ids or paired in picked_keys:
            continue
        if unevolved_ids[m_id] - 1 < int(cond.get("n") or 0):
            continue
        for i, c in enumerate(cards):
            if canonical_card_key(c) == paired:
                return i
    return None


def compute_power_score(t: float, level: int, kills: int, timeline: list[dict[str, Any]], boss_kills: int = 0) -> tuple[float, float, float]:
    """
    Active combat progress score:
    - eff_t: time spent actively fighting/progressing (segments where enemies >= 15 and kills < 2 in 10s
      are 'fleeing/stagnation' and count only 20%).
    - kpm: kills per minute.
    - score: combines active combat survival + kills + levels gained + boss milestone bonus (+250 pts / boss).
    """
    if timeline:
        eff_t = 0.0
        for i, pt in enumerate(timeline):
            dt = max(0.0, float(pt.get("t", 0)) - (float(timeline[i - 1].get("t", 0)) if i > 0 else 0.0))
            dk = int(pt.get("kills", 0)) - (int(timeline[i - 1].get("kills", 0)) if i > 0 else 0)
            en = int(pt.get("enemies", 0))
            eff_t += dt if (dk >= 2 or en < 15) else dt * 0.2
    else:
        eff_t = float(t)
    kpm = (kills * 60.0 / max(10.0, t)) if t > 0 else 0.0
    score = eff_t + kills * 1.5 + max(0, level - 1) * 22.0 + (boss_kills * 250.0)
    return round(score, 1), round(eff_t, 1), round(kpm, 1)


def expected_kpm_at(tp: float, runs_timelines: list[list[dict[str, Any]]]) -> float:
    """Average observed kills/min in the window [tp, tp + 70] across past runs."""
    rates = []
    for tl in runs_timelines:
        pts = [x for x in tl if tp <= float(x.get("t", 0)) <= tp + 75]
        if len(pts) >= 2:
            dt = float(pts[-1]["t"]) - float(pts[0]["t"])
            dk = int(pts[-1].get("kills", 0)) - int(pts[0].get("kills", 0))
            if dt >= 15:
                rates.append(dk * 60.0 / dt)
    if rates:
        return sum(rates) / len(rates)
    return 45.0 if tp < 30 else 65.0 if tp < 80 else 50.0 if tp < 180 else 40.0


def pick_kill_rate(tp: float, timeline: list[dict[str, Any]], expected_base: float = 50.0) -> tuple[float, float]:
    """Observed kills/min in the 45s after picking a card, and within-run delta vs pre-pick kill rate."""
    if not timeline:
        return 0.0, 0.0
    before = [x for x in timeline if max(0.0, tp - 35.0) <= float(x.get("t", 0)) <= tp]
    after = [x for x in timeline if tp <= float(x.get("t", 0)) <= tp + 45.0]
    kr_a = ((after[-1].get("kills", 0) - after[0].get("kills", 0)) * 60.0 / max(8.0, float(after[-1]["t"]) - float(after[0]["t"]))) if len(after) >= 2 else 0.0
    kr_b = ((before[-1].get("kills", 0) - before[0].get("kills", 0)) * 60.0 / max(8.0, float(before[-1]["t"]) - float(before[0]["t"]))) if len(before) >= 2 else expected_base
    # Compare against within-run pre-pick baseline blended with time-matched expected rate
    baseline = 0.7 * kr_b + 0.3 * expected_base
    krd = (kr_a - baseline) if kr_a > 0 else 0.0
    return round(kr_a, 1), round(krd, 1)


def default_knowledge() -> dict[str, Any]:
    return {"version": 8, "mode": "power_learning", "total_runs": 0, "best_time": 0.0, "best_score": 0.0, "best_level": 0,
            "best_run_id": None, "best_policy": dict(DEFAULT_POLICY), "card_stats": {},
            "card_global": {"n": 0.0, "adv_sum": 0.0}, "move_stats": {}, "lessons": {}, "runs": [], "best_timeline": [], "pending": {}}


class LearningEngine:
    def __init__(self, log_dir: Path, jev: Optional[JevFn] = None) -> None:
        self.log_dir = Path(log_dir)
        self.runs_dir = self.log_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.kb_file = self.log_dir / "knowledge.json"
        self.jev = jev
        self.extra_context: Optional[Callable[[], list]] = None
        self.journal: Any = None   # Player Journal (bridge/journal.py); None = legacy knob-tuning mode
        self.kb = self._load()
        self.harness = HarnessTree.load()
        # Set by harness_recursion while a candidate harness is probed: the next run keeps these knobs.
        self.policy_pin: Optional[dict[str, Any]] = None

    def reload_harness(self, directory: str | Path | None = None) -> HarnessTree:
        """Read the component tree again. Import loads it once onto this engine."""
        self.harness = HarnessTree.load(directory)
        return self.harness

    def harness_text(self, key: str, /, **fmt: Any) -> str:
        # key is positional-only so a template may contain {key}.
        return self.harness.text("tool_guidance", key, **fmt)

    def memory_int(self, decision: str, field: str) -> int:
        return int(self.harness.get(f"memory.decisions.{decision}.{field}"))

    def jev_timeout(self, name: str) -> float:
        timeouts = self.harness.get("plugins.jev_timeout_s") or {}
        if name in timeouts:
            return float(timeouts[name])
        return float(timeouts["default"])

    def decision_goal(self, decision: str) -> str:
        return str(self.harness.get(f"tool_guidance.decisions.{decision}.state_goal"))

    def guardrails_text(self) -> str:
        """guardrails.md without its leading 'inject:' marker line (injected only when harness.inject.guardrails is on)."""
        text = self.harness.files.get("guardrails.md") or ""
        lines = text.split("\n")
        if lines and lines[0].startswith("inject:"):
            lines = lines[1:]
        return "\n".join(lines).strip()

    def inject_guardrails(self) -> bool:
        return bool(self.harness.get("harness.inject.guardrails"))

    # ------------------------------------------------------------------ persistence & upgrade
    def _load(self) -> dict[str, Any]:
        kb = default_knowledge()
        self.kb = kb
        if self.kb_file.is_file():
            try:
                raw = json.loads(self.kb_file.read_text(encoding="utf-8"))
                kb.update(raw)
                for k, v in DEFAULT_POLICY.items():
                    kb["best_policy"].setdefault(k, v)
                if raw.get("version", 0) < 8:
                    self._upgrade_to_v7(kb)
                    self.save()
            except Exception as e:
                print(f"[Learning] knowledge load failed: {e}")
        return kb

    def _upgrade_to_v7(self, kb: dict[str, Any]) -> None:
        """Recompute within-run card credit assignment, portal/chest rewards, and uncontaminated move DPS."""
        print("[Learning] Upgrading knowledge base to Version 8 (within-run card deltas + clean portal/chest/trap stats)...")
        kb["version"] = 8
        kb["card_stats"] = {}
        kb["card_global"] = {"n": 0.0, "adv_sum": 0.0}
        kb["move_stats"] = {}
        kb["objects"] = {}
        kb["best_score"] = 0.0

        run_files = sorted(self.runs_dir.glob("*.json"))
        run_data_list = []
        all_timelines = []

        for p in run_files:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                run_data_list.append(d)
                if d.get("timeline"):
                    all_timelines.append(d["timeline"])
            except Exception:
                continue

        recomputed_scores: list[float] = []

        for d in run_data_list:
            tl = d.get("timeline") or []
            t = float(d.get("time") or 0)
            lv = int(d.get("level") or 1)
            kills = int(d.get("kills") or (tl[-1].get("kills", 0) if tl else 0))
            boss_kills = int(d.get("boss_kills") or (tl[-1].get("boss_kills", 0) if tl else 0))
            if not boss_kills and "world_deltas" in d:
                boss_kills = sum(int(obs.get("n") or 1) for k, obs in d.get("world_deltas", {}).get("kills", {}).items() if k.startswith("boss:"))
            score, eff_t, kpm = compute_power_score(t, lv, kills, tl, boss_kills=boss_kills)
            base = sum(recomputed_scores[-20:]) / len(recomputed_scores[-20:]) if recomputed_scores else score
            recomputed_scores.append(score)
            run_adv = (score - base) / 4.0

            kb["best_time"] = max(kb["best_time"], round(t, 1))
            kb["best_level"] = max(kb["best_level"], lv)
            if score > kb["best_score"]:
                kb["best_score"] = score
                kb["best_run_id"] = d.get("run_id")
                trial_pol = d.get("trial", {}).get("policy") or d.get("policy")
                if trial_pol:
                    kb["best_policy"] = {**DEFAULT_POLICY, **trial_pol}

            # Aggregate per-move observed DPS & kills (exclude runs before grace tracking and multi-move Bond fusions)
            rno = int(d.get("run_number") or 0)
            has_bond = any("Vínculo" in str(m.get("text", "")) for m in (d.get("messages") or []) if isinstance(m, dict))
            for m in d.get("moves") or []:
                mid = m.get("id")
                if mid:
                    st = kb["move_stats"].setdefault(mid, {"kills": 0, "combat_sec": 0.0, "dmg": 0.0, "runs": 0, "tested_runs": 0})
                    st["runs"] += 1
                    csec = float(m.get("combat_sec") or 0)
                    if rno >= 140 and csec >= 2.5 and not has_bond:
                        st["tested_runs"] += 1
                        st["kills"] += int(m.get("kills") or 0)
                        st["combat_sec"] = round(st["combat_sec"] + csec, 1)
                        st["dmg"] = round(st["dmg"] + float(m.get("dmg") or 0), 1)

            # Rebuild clean object stats
            SUCCESS = {"came back from the other place", "it changed when I touched it", "it disappeared after I reached it", "stood next to it for 6s"}
            msgs = d.get("messages") or []
            items = d.get("items") or []
            uncredited_item_pickups = int(round(float(((d.get("world_deltas") or {}).get("pickups") or {}).get("pickup:item", {}).get("n") or 0)))
            has_portal_opp = False
            for o in d.get("opportunities") or []:
                k = str(o.get("type") or "")
                if not k.startswith("object:"):
                    continue
                why = o.get("why")
                if why not in SUCCESS:
                    continue
                if k == "object:purple portal":
                    has_portal_opp = True
                st_obj = kb["objects"].setdefault(k, {"seen": 0, "tried": 0, "positive": 0, "died": 0, "sum": {}, "hp_cost": 0.0, "time": 0.0, "messages": []})
                st_obj["seen"] += 1
                st_obj["tried"] += 1
                ot = float(o.get("t") or 0)
                tc = float(o.get("time_cost") or 0)
                hp_cost = max(0.0, float(o.get("hp_cost") or 0))
                rew = dict(o.get("reward") or {})
                if why == "stood next to it for 6s" or (k != "object:purple portal" and (hp_cost >= 0.05 or (why != "it disappeared after I reached it" and tc > 2))):
                    rew["level"] = 0
                    rew["xp"] = 0
                    rew["coins"] = 0
                    rew["items"] = 0
                near_msgs = [m.get("text") for m in msgs if isinstance(m, dict) and -1.5 <= (float(m.get("t") or 0) - ot) <= 3.5] if why != "stood next to it for 6s" else []
                near_items = [it for it in items if -1.5 <= (float(it.get("t") or 0) - ot) <= 4.0] if why != "stood next to it for 6s" else []
                if (near_items or any("Llevas" in str(m) for m in near_msgs)) and hp_cost < 0.05 and why == "it disappeared after I reached it":
                    rew["items"] = max(float(rew.get("items") or 0), 1.0)
                    if uncredited_item_pickups > 0:
                        uncredited_item_pickups -= 1
                elif k == "object:big golden chest" and why == "it disappeared after I reached it" and hp_cost < 0.05 and uncredited_item_pickups > 0 and float(rew.get("items") or 0) == 0:
                    rew["items"] = 1.0
                    uncredited_item_pickups -= 1
                if any("Vínculo" in str(m) for m in near_msgs) or (k == "object:purple portal" and has_bond):
                    rew["bonds"] = max(float(rew.get("bonds") or 0), 1.0)
                for m in near_msgs:
                    if m and m not in st_obj["messages"] and "enfurecido" not in m and "escapado" not in m and (hp_cost < 0.05 or k == "object:purple portal"):
                        st_obj["messages"].append(m)
                st_obj["messages"] = st_obj["messages"][:10]
                if k == "object:purple portal":
                    pos = any(float(rew.get(rk) or 0) > 0 for rk in ("bonds", "items", "level", "t1", "t10"))
                else:
                    pos = (any(float(rew.get(rk) or 0) > 0 for rk in ("items", "level", "bonds", "coins", "t1", "t10"))
                           or float(rew.get("hp") or 0) > 0.05
                           or (why == "it changed when I touched it" and hp_cost < 0.05)) and hp_cost < 0.05
                if pos and not o.get("died"):
                    st_obj["positive"] += 1
                if o.get("died"):
                    st_obj["died"] += 1
                for rk, v_val in rew.items():
                    st_obj["sum"][rk] = round(st_obj["sum"].get(rk, 0.0) + float(v_val or 0), 4)
                st_obj["hp_cost"] = round(st_obj["hp_cost"] + hp_cost, 3)
                st_obj["time"] = round(st_obj["time"] + tc, 1)

            # Also reconstruct untracked purple portal visits evidenced by on-screen 'Has entrado en la grieta…'
            if not has_portal_opp and any("Has entrado en la grieta" in str(m.get("text", "")) for m in msgs if isinstance(m, dict)):
                st_obj = kb["objects"].setdefault("object:purple portal", {"seen": 0, "tried": 0, "positive": 0, "died": 0, "sum": {}, "hp_cost": 0.0, "time": 0.0, "messages": []})
                st_obj["seen"] += 1
                st_obj["tried"] += 1
                st_obj["hp_cost"] = round(st_obj["hp_cost"] + (0.25 if has_bond else 0.55), 3)
                st_obj["time"] = round(st_obj["time"] + 35.0, 1)
                if has_bond:
                    st_obj["positive"] += 1
                    st_obj["sum"]["bonds"] = round(st_obj["sum"].get("bonds", 0.0) + 1.0, 4)
                    if "¡Vínculo! 2 movimientos a la vez" not in st_obj["messages"]:
                        st_obj["messages"].append("¡Vínculo! 2 movimientos a la vez")

            had_evo = False
            for pk in d.get("picks") or []:
                k = canonical_card_key(pk)
                if k.startswith("item:"):
                    continue
                tp = float(pk.get("t") or 0)
                exp_base = expected_kpm_at(tp, all_timelines)
                kra, krd = pick_kill_rate(tp, tl, exp_base)
                eff_run_adv = run_adv * 0.25 if had_evo and not k.startswith("evo:") else run_adv
                pick_adv = (0.30 * eff_run_adv + 0.70 * krd) if kra > 0 else eff_run_adv
                st = kb["card_stats"].setdefault(k, {"n": 0.0, "adv_sum": 0.0, "kra_sum": 0.0, "krd_sum": 0.0, "title": pk.get("title", k)})
                st["n"] = round(st["n"] + 1.0, 2)
                st["adv_sum"] = round(st["adv_sum"] + pick_adv, 2)
                st["kra_sum"] = round(st["kra_sum"] + kra, 2)
                st["krd_sum"] = round(st["krd_sum"] + krd, 2)
                kb["card_global"]["n"] = round(kb["card_global"]["n"] + 1.0, 2)
                kb["card_global"]["adv_sum"] = round(kb["card_global"]["adv_sum"] + pick_adv, 2)
                if k.startswith("evo:"):
                    had_evo = True

        print(f"[Learning] stats rebuilt from run files: {len(kb['card_stats'])} cards indexed, {len(kb['move_stats'])} moves tracked, {len(kb['objects'])} objects tracked, best_score={kb['best_score']:.1f}")

    def rebuild_stats_from_runs(self) -> None:
        """Recompute card/move/best stats from the (deduplicated) run files on disk."""
        self._upgrade_to_v7(self.kb)

    # ------------------------------------------------------------------ duplicate protection
    @staticmethod
    def run_signature(r: dict[str, Any]) -> tuple:
        return (round(float(r.get("time") or 0), 1), int(r.get("level") or 0), int(r.get("kills") or 0), str(r.get("partner") or ""))

    def is_duplicate(self, rec: dict[str, Any]) -> bool:
        rid = rec.get("run_id")
        if rid and any(r.get("run_id") == rid for r in self.kb["runs"]):
            return True
        sig = self.run_signature(rec)
        return sig[0] > 0 and any(self.run_signature(r) == sig for r in self.kb["runs"])

    def save(self) -> None:
        tmp = self.kb_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.kb, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.kb_file)

    # ------------------------------------------------------------------ card credit assignment (power + kill rate)
    def card_value(self, key: str) -> tuple[float, int, float, float]:
        st = self.kb["card_stats"].get(key)
        if not st or st["n"] <= 0:
            return 0.0, 0, 0.0, 0.0
        g = self.kb.get("card_global") or {"n": 0.0, "adv_sum": 0.0}
        g_mean = g["adv_sum"] / g["n"] if g["n"] > 0 else 0.0
        val = (st["adv_sum"] - st["n"] * g_mean) / (st["n"] + PRIOR_N)
        n = int(round(st["n"]))
        kra = (st.get("kra_sum", 0.0) / st["n"]) if st["n"] > 0 else 0.0
        krd = (st.get("krd_sum", 0.0) / st["n"]) if st["n"] > 0 else 0.0
        return val, n, kra, krd

    def is_zero_dmg_move(self, mid: str) -> bool:
        mst = self.kb.get("move_stats", {}).get(mid)
        if not mst or mst.get("runs", 0) < 2:
            return False
        csec = mst.get("combat_sec", 0.0)
        dps = mst.get("dmg", 0.0) / max(1.0, csec) if csec >= 2.5 else 0.0
        return (csec < 2.5 or dps < 5.0) and mst.get("runs", 0) >= 2

    def card_hint(self, key: str) -> str:
        v, n, kra, krd = self.card_value(key)
        prefix = f"picked {n}x: power {v:+.0f}, post-pick kill rate {kra:.0f}/min ({krd:+.0f}/min vs expected)" if n > 0 else "never picked before"
        m_id = key.split(":", 1)[1] if ":" in key else key
        if self.is_zero_dmg_move(m_id):
            return f"{prefix} | 0 direct weapon damage (pure support buff)"
        mst = self.kb.get("move_stats", {}).get(m_id)
        if mst and mst.get("combat_sec", 0) >= 3.0:
            csec = mst["combat_sec"]
            kpm = round(mst["kills"] * 60 / csec)
            dps = round(mst.get("dmg", 0) / csec)
            if dps < 5 and kpm < 5:
                return f"{prefix} | 0 direct weapon damage (pure support buff)"
            return f"{prefix} | direct weapon DPS {dps} HP/s, {kpm} kills/min"
        return prefix

    def _baseline_score(self) -> float:
        runs = self.kb["runs"][-20:]
        if not runs:
            return 300.0
        scores = [r.get("power_score") or (r["time"] + r["level"] * 15) for r in runs]
        return sum(scores) / len(scores)

    # ------------------------------------------------------------------ lessons (data + Jev verdicts)
    def _set_lesson(self, key: str, text: str, kind: str, confidence: float, evidence: list) -> None:
        self.kb["lessons"][key] = {"text": text, "kind": kind, "confidence": round(_clamp(confidence, 0, 1), 2),
                                   "evidence_runs": evidence[-10:], "updated": time.time()}

    def top_lessons(self, k: int = 6) -> list[str]:
        items = sorted(self.kb["lessons"].values(), key=lambda x: (-x.get("confidence", 0), -x.get("updated", 0)))
        return [f"[{x.get('confidence', 0):.2f}] {x.get('text', '')}" for x in items[:k]]

    def history_table(self, n: int = 15) -> list[dict[str, Any]]:
        return [{"run": r["run"], "survived": _mmss(r["time"]), "level": r["level"], "kills": r.get("kills", 0),
                 "boss_kills": r.get("boss_kills", 0),
                 "power": r.get("power_score", 0), "hurt_most_by": r.get("cause"),
                 "policy": r.get("policy")} for r in self.kb["runs"][-n:]]

    # ------------------------------------------------------------------ Jev chooses the policy for the next run
    def _jev_policy(self) -> Optional[tuple[dict[str, float], str, str]]:
        if not self.jev:
            return None
        kb = self.kb
        last = kb["runs"][-1]["policy"] if kb["runs"] and kb["runs"][-1].get("policy") else kb["best_policy"]
        mem = self.harness.get("memory.decisions.jev_policy")
        lower_t = self.harness_text("decisions.jev_policy.experiment.criteria_templates.lower")
        higher_t = self.harness_text("decisions.jev_policy.experiment.criteria_templates.higher")
        rotate_t = self.harness_text("decisions.jev_policy.experiment.criteria_templates.rotate_s")
        explore = self.harness.get("tool_guidance.decisions.jev_policy.experiment.criteria")
        crit = {}
        active_knobs = ["aggression", "combat_range", "orbit", "greed", "fear", "telegraph_fear", "keep_distance"]
        for k in active_knobs:
            _lo, _hi, _d, _kind, meaning = KNOBS[k]
            crit[f"{k}:lower"] = lower_t.format(knob=k, meaning=meaning)
            crit[f"{k}:higher"] = higher_t.format(knob=k, meaning=meaning)
        for v in ("0", "1", "2", "4"):
            crit[f"rotate_s:{v}"] = rotate_t.format(value=v, meaning=KNOBS["rotate_s"][4])
        crit["explore:0"] = explore["explore:0"]
        crit["explore:1"] = explore["explore:1"]
        tested = self._change_evidence()
        lastrun = kb["runs"][-1] if kb["runs"] else None
        brief = (self.extra_context() if self.extra_context else [])[:int(mem["world_brief"])]
        state = {
            "goal": self.decision_goal("jev_policy"),
            "best_run": f"best score {kb.get('best_score', 0):.0f} ({_mmss(kb['best_time'])}, Lv{kb['best_level']}) with policy {self._policy_diff(kb['best_policy'])}",
            "last_run": f"{_mmss(lastrun['time'])}, Lv{lastrun.get('level')}, {lastrun.get('kills', 0)} kills, hurt most by {lastrun.get('cause')}" if lastrun else "none yet",
            "changes_tested_so_far": [f"{c}: {e['times_tested']} runs, avg {e['avg_survival_s']}s" for c, e in tested.items()],
            "changes_never_tested": [c for c in crit if c not in tested][:30],
            "what_i_have_learned": (brief + self.top_lessons(int(mem["lessons"])))[:int(mem["learned_cap"])],
        }
        base = self.harness.get("tool_guidance.decisions.jev_policy.base")
        experiment = self.harness.get("tool_guidance.decisions.jev_policy.experiment")
        q = {"base": {"type": base["type"], "instructions": base["instructions"], "criteria": dict(base["criteria"])},
             "experiment": {"type": experiment["type"], "criteria": crit, "instructions": experiment["instructions"]}}
        res = self.jev(state, q, self.jev_timeout("jev_policy"))
        if not (res and "answers" in res):
            return None
        a = res["answers"]
        base_name = self._sample(a.get("base", {})) or "best"
        base = last if base_name == "last" else kb["best_policy"]
        pol = dict(DEFAULT_POLICY)
        pol.update(base)
        exp = self._sample(a.get("experiment", {}))
        if ":" in exp:
            k, v = exp.split(":", 1)
            if k in KNOBS:
                if v in ("lower", "higher"):
                    pol[k] = apply_step(k, float(pol[k]), -1 if v == "lower" else 1)
                else:
                    try:
                        pol[k] = float(v)
                    except ValueError:
                        pass
        return pol, f"from {base_name}: {exp or 'no change'}", "jev"

    def _change_evidence(self) -> dict[str, Any]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.kb["runs"]:
            why = r.get("why") or ""
            if ":" not in why:
                continue
            ch = why.split(": ", 1)[-1]
            e = out.setdefault(ch, {"times_tested": 0, "survival_s": []})
            e["times_tested"] += 1
            e["survival_s"].append(round(r["time"]))
        for e in out.values():
            e["avg_survival_s"] = round(sum(e["survival_s"]) / len(e["survival_s"]))
            e["survival_s"] = e["survival_s"][-5:]
        return out

    def _fallback_policy(self) -> tuple[dict[str, float], str, str]:
        pol = dict(DEFAULT_POLICY)
        pol.update(self.kb["best_policy"])
        core = ["aggression", "combat_range", "orbit", "greed", "fear", "telegraph_fear", "keep_distance"]
        k = random.choice(core)
        step = random.choice([-1, 1])
        pol[k] = apply_step(k, float(pol[k]), step)
        return pol, f"(Jev offline) tweak {k} {'up' if step > 0 else 'down'}", "fallback"

    @staticmethod
    def _sample(ans: dict[str, Any]) -> str:
        probs = ans.get("probabilities") or {}
        if probs:
            keys, ws = zip(*[(k, max(0.0, float(v))) for k, v in probs.items()])
            if sum(ws) > 0:
                return random.choices(keys, weights=ws, k=1)[0]
        return ans.get("choice", "")

    @staticmethod
    def _policy_diff(pol: dict[str, Any]) -> str:
        d = {k: v for k, v in pol.items() if k in DEFAULT_POLICY and float(v) != float(DEFAULT_POLICY[k])}
        return json.dumps(d) if d else "(defaults)"

    def start_run(self, info: dict[str, Any]) -> dict[str, Any]:
        run_id = str(info.get("client_run_id") or f"r{int(time.time() * 1000)}")
        plan = None
        if self.journal:
            # A player decides how to play the next run while thinking about the last one -> no Jev wait here.
            plan = self.journal.current_plan()
            pol, src = dict(plan["policy"]), f"plan:{plan.get('source')}"
            q = plan.get("question_text") or "no particular question"
            why = f"❓ {q} | 🎮 {plan.get('stance_label')} | 🃏 {plan.get('build_text')}"
        elif self.policy_pin and isinstance(self.policy_pin.get("policy"), dict):
            # A harness candidate is being probed: no new knob experiment this run.
            pol, why, src = dict(self.policy_pin["policy"]), str(self.policy_pin.get("why") or "harness probe"), "harness_probe"
        else:
            pol, why, src = self._jev_policy() or self._fallback_policy()
        self.kb["pending"][run_id] = {"policy": pol, "why": why, "source": src, "started": time.time(),
                                      "partner": info.get("partner"), "plan": plan, "bot_build": info.get("bot_build")}
        for rid in list(self.kb["pending"]):
            if time.time() - self.kb["pending"][rid]["started"] > 3 * 3600:
                del self.kb["pending"][rid]
        self.save()
        out = {"run_id": run_id, "run_number": self.kb["total_runs"] + 1, "policy": pol, "experiment": why,
               "hypothesis": why, "lessons": self.top_lessons(self.memory_int("run_start", "lessons")), "best_time": self.kb["best_time"],
               "best_score": self.kb.get("best_score", 0), "source": src}
        if plan:
            out["plan"] = self.journal.brief(plan)
            self.journal.save()
        return out

    # ------------------------------------------------------------------ mid-run: Jev adjusts combat & evasion posture
    def checkpoint(self, d: dict[str, Any]) -> dict[str, Any]:
        t = float(d.get("t") or 0)
        out = {"verdict": "ok", "posture": "unchanged", "temp_adjust": {}, "source": "none"}
        if not self.jev:
            return out
        state = {
            "goal": self.decision_goal("checkpoint"),
            "time": _mmss(t),
            "me_now": {k: d.get(k) for k in ("level", "hp_pct", "kills", "enemies", "dmg_per_min")},
            "my_lessons": self.top_lessons(self.memory_int("checkpoint", "lessons")),
        }

        def _posture(name: str) -> dict[str, Any]:
            spec = self.harness.get(f"tool_guidance.decisions.checkpoint.{name}")
            return {"type": spec["type"], "instructions": spec["instructions"], "criteria": dict(spec["criteria"])}

        q = {"aggression_now": _posture("aggression_now"), "fear_now": _posture("fear_now"), "greed_now": _posture("greed_now")}
        res = self.jev(state, q, self.jev_timeout("checkpoint"))
        if res and "answers" in res:
            mult = {"lower": 0.75, "same": 1.0, "higher": 1.35}
            ag = self._sample(res["answers"].get("aggression_now", {})) or "same"
            f = self._sample(res["answers"].get("fear_now", {})) or "same"
            g = self._sample(res["answers"].get("greed_now", {})) or "same"
            out.update({
                "temp_adjust": {
                    "aggression_mult": mult.get(ag, 1.0),
                    "evasion_weight_mult": mult.get(f, 1.0),
                    "pickup_mult": mult.get(g, 1.0),
                },
                "posture": f"aggression {ag}, fear {f}, greed {g}",
                "source": "jev",
            })
        return out

    # ------------------------------------------------------------------ end of run: power scoring & Jev reflection
    def end_run(self, rec: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        if not persist:
            import copy
            saved = copy.deepcopy(self.kb)
            saved_j = copy.deepcopy(self.journal.data) if self.journal else None
            try:
                return self._end_run(rec, False)
            finally:
                self.kb = saved
                if self.journal:
                    self.journal.data = saved_j
        return self._end_run(rec, True)

    def _end_run(self, rec: dict[str, Any], persist: bool) -> dict[str, Any]:
        kb = self.kb
        run_id = rec.get("run_id") or f"r{int(time.time() * 1000)}"
        trial = kb["pending"].pop(run_id, None) or {"policy": rec.get("policy") or kb["best_policy"], "why": "untracked", "source": "unknown"}
        t, level = float(rec.get("time") or 0), int(rec.get("level") or 1)
        tl = rec.get("timeline") or []
        kills = int(rec.get("kills") or (tl[-1].get("kills", 0) if tl else 0))
        boss_kills = int(rec.get("boss_kills") or (tl[-1].get("boss_kills", 0) if tl else 0))
        if not boss_kills and "world_deltas" in rec:
            boss_kills = sum(int(obs.get("n") or 1) for k, obs in rec.get("world_deltas", {}).get("kills", {}).items() if k.startswith("boss:"))
        cause, phase = str(rec.get("death_cause") or "unknown"), _phase(t)

        score, eff_t, kpm = compute_power_score(t, level, kills, tl, boss_kills=boss_kills)
        baseline = self._baseline_score()
        run_adv = (score - baseline) / 4.0

        # Aggregate per-move observed DPS & kills (exclude runs with multi-move Bond fusion and short taps)
        has_bond = any("Vínculo" in str(m.get("text", "")) for m in (rec.get("messages") or []) if isinstance(m, dict))
        for m in rec.get("moves") or []:
            mid = m.get("id")
            if mid:
                st = kb.setdefault("move_stats", {}).setdefault(mid, {"kills": 0, "combat_sec": 0.0, "dmg": 0.0, "runs": 0, "tested_runs": 0})
                st["runs"] += 1
                csec = float(m.get("combat_sec") or 0)
                if csec >= 2.5 and not has_bond:
                    st["tested_runs"] += 1
                    st["kills"] += int(m.get("kills") or 0)
                    st["combat_sec"] = round(st["combat_sec"] + csec, 1)
                    st["dmg"] = round(st["dmg"] + float(m.get("dmg") or 0), 1)

        all_tls = [r.get("timeline") for r in kb.get("runs", []) if r.get("timeline")] + ([tl] if tl else [])

        # Credit assignment to cards using within-run post-pick kill-rate deltas + overall power advantage
        g = kb.setdefault("card_global", {"n": 0.0, "adv_sum": 0.0})
        had_evo = False
        for p in rec.get("picks") or []:
            k = canonical_card_key(p)
            if not k or k.startswith("item:"):
                continue
            tp = float(p.get("t") or 0)
            exp_base = expected_kpm_at(tp, all_tls)
            kra, krd = pick_kill_rate(tp, tl, exp_base)
            eff_run_adv = run_adv * 0.25 if had_evo and not k.startswith("evo:") else run_adv
            pick_adv = (0.30 * eff_run_adv + 0.70 * krd) if kra > 0 else eff_run_adv
            st = kb["card_stats"].setdefault(k, {"n": 0.0, "adv_sum": 0.0, "kra_sum": 0.0, "krd_sum": 0.0, "title": p.get("title", k)})
            st["n"] = round(st["n"] + 1.0, 2)
            st["adv_sum"] = round(st["adv_sum"] + pick_adv, 2)
            st["kra_sum"] = round(st["kra_sum"] + kra, 2)
            st["krd_sum"] = round(st["krd_sum"] + krd, 2)
            g["n"] = round(g["n"] + 1.0, 2)
            g["adv_sum"] = round(g["adv_sum"] + pick_adv, 2)
            if k.startswith("evo:"):
                had_evo = True

        reflection = {"diagnosis": cause, "verdict": "n/a", "source": "none"}
        if self.journal:
            reflection = self.journal.reflect(rec, kb["total_runs"] + 1, score, trial)
        elif self.jev:
            prev = kb["runs"][-1] if kb["runs"] else None
            mem = self.harness.get("memory.decisions.end_run_no_journal")
            state = {
                "goal": self.decision_goal("end_run_no_journal"),
                "this_run": {
                    "survived": _mmss(t), "level": level, "kills": kills, "boss_kills": boss_kills, "kills_per_min": kpm,
                    "power_score": score, "hurt_most_by": cause, "damage_by_source": rec.get("damage_sources"),
                    "policy": trial["policy"], "why_this_policy": trial.get("why"),
                    "picks": [canonical_card_key(p) for p in rec.get("picks") or []][:30],
                    "timeline_tail": tl[-6:], "on_screen_text": (rec.get("messages") or [])[-8:],
                },
                "previous_run": {"survived": _mmss(prev["time"]), "kills": prev.get("kills", 0), "power": prev.get("power_score", 0)} if prev else None,
                "history": self.history_table(int(mem["history"])),
                "learned": (self.extra_context() if self.extra_context else [])[:int(mem["world_brief"])],
            }

            def _end_q(name: str) -> dict[str, Any]:
                spec = self.harness.get(f"tool_guidance.decisions.end_run_no_journal.{name}")
                return {"type": spec["type"], "instructions": spec["instructions"], "criteria": dict(spec["criteria"])}

            q = {"failure_diagnosis": _end_q("failure_diagnosis"), "policy_verdict": _end_q("policy_verdict")}
            res = self.jev(state, q, self.jev_timeout("end_run_no_journal"))
            if res and "answers" in res:
                a = res["answers"]
                reflection = {
                    "diagnosis": a.get("failure_diagnosis", {}).get("choice") or cause,
                    "verdict": a.get("policy_verdict", {}).get("choice") or "unclear",
                    "source": "jev",
                }

        kb["total_runs"] += 1
        run_no = kb["total_runs"]
        record = score > kb.get("best_score", 0.0)
        if record:
            kb["best_score"] = score
            kb["best_run_id"] = run_id
            kb["best_policy"] = dict(trial["policy"])
            kb["best_timeline"] = [{"t": p.get("t"), "level": p.get("level"), "hp_pct": p.get("hp_pct"), "kills": p.get("kills")} for p in tl]
        kb["best_time"] = max(kb["best_time"], round(t, 1))
        kb["best_level"] = max(kb["best_level"], level)

        kb["runs"].append({
            "run": run_no, "run_id": run_id, "time": round(t, 1), "level": level, "kills": kills,
            "boss_kills": boss_kills,
            "power_score": score, "cause": cause, "phase": phase, "diagnosis": reflection["diagnosis"],
            "verdict": reflection["verdict"], "policy": trial["policy"], "why": trial.get("why"),
            "partner": rec.get("partner"), "ts": time.time(),
        })
        kb["runs"] = kb["runs"][-MAX_RUNS:]

        if reflection["source"] == "jev":
            self._set_lesson(f"verdict:{run_no}", f"Run #{run_no} ({_mmss(t)}, {kills} kills, Lv{level}): policy '{trial.get('why')}' -> {reflection['verdict']}; ended by {reflection['diagnosis']}", "jev_reflection", 0.6, [run_no])
            old = sorted([k for k in kb["lessons"] if k.startswith("verdict:")], key=lambda k: int(k.split(":")[1]))
            for k in old[:-6]:
                del kb["lessons"][k]

        scored = sorted([(k, *self.card_value(k)) for k in kb["card_stats"]], key=lambda s: s[1])
        for k in [k for k in kb["lessons"] if k.startswith("card:")]:
            del kb["lessons"][k]
        for key, val, n, kra, krd in [s for s in scored if s[2] >= 2][:2] + [s for s in scored if s[2] >= 2][-3:]:
            self._set_lesson(f"card:{key}", f"'{kb['card_stats'][key].get('title', key)}': power {val:+.0f}, post-pick kill rate {kra:.0f}/min ({krd:+.0f}/min) (n={n})",
                             "card_statistics", min(1.0, n / 6), [run_no])

        b_str = f", {boss_kills} boss{'es' if boss_kills > 1 else ''}" if boss_kills > 0 else ""
        lesson = f"Run #{run_no} {_mmss(t)} Lv{level} ({kills} kills{b_str}, power {score:.0f}), hurt most by {cause}. Policy: {trial.get('why')} -> {reflection['verdict']}."
        next_plan = None
        if self.journal:
            lesson = reflection.get("diary") or lesson
            next_plan = self.journal.plan_next(use_jev=True)
        if persist:
            (self.runs_dir / f"run_{run_no:04d}_{run_id}.json").write_text(
                json.dumps({**rec, "run_number": run_no, "trial": trial, "reflection": reflection, "power_score": score}, indent=1, ensure_ascii=False), encoding="utf-8")
            self.save()
            if self.journal:
                self.journal.save()
                self.journal.write_markdown()

        out = {
            "status": "learned", "run": run_no, "lesson": lesson, "record": record, "diagnosis": reflection["diagnosis"],
            "verdict": reflection["verdict"], "decision": "new_best_power" if record else "recorded", "best_params": kb["best_policy"],
            "best_time_seconds": kb["best_time"], "best_score": kb.get("best_score", 0), "total_runs": run_no,
            "lessons": self.top_lessons(self.memory_int("end_run_response", "lessons")), "source": reflection["source"],
        }
        if next_plan:
            out.update({"focus_verdict": reflection.get("focus_verdict"), "new_questions": reflection.get("new_questions", []),
                        "next_plan": self.journal.brief(next_plan)})
        return out

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict[str, Any]:
        kb = self.kb
        return {
            "mode": "power_learning", "total_runs": kb["total_runs"], "best_time": kb["best_time"],
            "best_score": kb.get("best_score", 0), "best_level": kb["best_level"],
            "best_policy": kb["best_policy"], "best_params": kb["best_policy"], "lessons": self.top_lessons(10), "recent_runs": self.history_table(10),
            "top_cards": sorted([
                {"key": k, "title": v.get("title"), "value": round(self.card_value(k)[0], 1), "n": self.card_value(k)[1],
                 "kills_per_min_after": round(self.card_value(k)[2], 1), "delta_kpm": round(self.card_value(k)[3], 1)}
                for k, v in kb["card_stats"].items()
            ], key=lambda x: -x["value"])[:15],
        }

    def report_markdown(self) -> str:
        kb = self.kb
        lines = [
            "# PokéSurvivor x Jev — Power & Evolution Learning Report", "",
            f"- Runs: **{kb['total_runs']}** | Best time: **{_mmss(kb['best_time'])}** | Best score: **{kb.get('best_score', 0):.0f}** | Best level: **{kb['best_level']}**",
            f"- Best policy: `{json.dumps(kb['best_policy'])}`", "",
            "## Learning curve (Power & Combat Progress)", "",
            "| Run | Time | Lv | Kills | Bosses | Power | Cause | Jev Policy Tested | Verdict |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in kb["runs"][-35:]:
            lines.append(f"| {r['run']} | {_mmss(r['time'])} | {r['level']} | {r.get('kills', 0)} | {r.get('boss_kills', 0)} | {r.get('power_score', 0):.0f} | {r.get('cause')} | {r.get('why')} | {r.get('verdict')} |")
        lines += ["", "## Lessons (Evidence-Backed)", ""] + [f"- {x}" for x in self.top_lessons(12)]
        if self.extra_context:
            lines += ["", "## World Model (Learned Opportunities & Dangers)", ""] + [f"- {x}" for x in self.extra_context()]
        lines += ["", "## Top Cards by Power & Kill-Rate Attribution", "",
                  "| Card | Power Value | Post-Pick Kills/Min | Delta Kills/Min | Picks |",
                  "|---|---|---|---|---|"]
        scored = sorted([(k, *self.card_value(k)) for k in kb["card_stats"]], key=lambda s: -s[1])
        for key, val, n, kra, krd in scored[:15]:
            title = kb["card_stats"][key].get("title", key)
            lines.append(f"| {title} (`{key}`) | {val:+.1f} | {kra:.1f}/min | {krd:+.1f}/min | {n} |")
        return "\n".join(lines) + "\n"
