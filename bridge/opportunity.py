#!/usr/bin/env python3
"""
Pure-learning world model (NO knowledge from the game's source code)
====================================================================
Everything here is learned from the bot's own observations:

- danger    : per visible thing (enemy species, circle/line telegraph, projectile,
              terrain, map object) and distance bin -> damage rate measured when near it.
- pickups   : per pickup icon -> what changed when it was collected (HP, XP, coins, tickets...).
- kills     : per enemy species -> what was gained right after one died near me.
- objects   : per map-object / portal label -> outcomes of deliberately investigating it.
- partners / upgrades : results of meta choices between runs.

Visible labels only ("big golden chest", "purple portal") - no rewards, rules or formulas.
Unknown things get forced curiosity trials; afterwards Jev decides using experience only.
"""

from __future__ import annotations

import math
import time
from typing import Any

N_BINS = 4  # distance-from-edge bins: overlap, 0-15px, 15-35px, 35-70px
REWARD_KEYS = ("hp", "level", "xp", "bonds", "coins", "t1", "t10", "items")


class WorldModel:
    def __init__(self, engine) -> None:
        self.engine = engine
        kb = engine.kb
        for k in ("danger", "pickups", "kills", "objects", "partners", "upgrades_hist", "discoveries"):
            kb.setdefault(k, {})

    # ------------------------------------------------------------------ merging page deltas
    def merge(self, deltas: dict[str, Any]) -> None:
        kb = self.engine.kb
        for key, bins in (deltas.get("danger") or {}).items():
            cur = kb["danger"].setdefault(key, [[0.0, 0.0] for _ in range(N_BINS)])
            for i, (exp_s, dmg) in enumerate(bins[:N_BINS]):
                cur[i][0] = round(cur[i][0] + float(exp_s), 2)
                cur[i][1] = round(cur[i][1] + float(dmg), 4)
        for table in ("pickups", "kills"):
            for key, obs in (deltas.get(table) or {}).items():
                cur = kb[table].setdefault(key, {"n": 0, "sum": {}})
                cur["n"] += int(obs.get("n") or 0)
                for rk, v in (obs.get("sum") or {}).items():
                    cur["sum"][rk] = round(cur["sum"].get(rk, 0.0) + float(v or 0), 4)
        terr = kb.setdefault("terrain", {})
        for k, v in (deltas.get("terrain") or {}).items():
            terr[k] = round(terr.get(k, 0) + float(v or 0), 2)
        for key in deltas.get("seen") or []:
            if key not in kb["discoveries"]:
                kb["discoveries"][key] = {"first_seen": time.time(), "run": kb["total_runs"] + 1}

    def snapshot(self) -> dict[str, Any]:
        kb = self.engine.kb
        return {"danger": kb["danger"], "pickups": kb["pickups"], "kills": kb["kills"], "terrain": kb.get("terrain", {}),
                "objects": {k: self.object_value(k) for k in kb["objects"]}}

    # ------------------------------------------------------------------ summaries for Jev
    def danger_rate(self, key: str, b: int) -> tuple[float, float]:
        cur = self.engine.kb["danger"].get(key)
        if not cur:
            return 0.0, 0.0
        exp_s, dmg = cur[b]
        return dmg / (exp_s + 2.0), exp_s

    def brief(self, k: int = 10) -> list[str]:
        kb = self.engine.kb
        out = []
        ranked = sorted(kb["danger"], key=lambda key: -self.danger_rate(key, 0)[0])
        for key in ranked[:5]:
            r0, e0 = self.danger_rate(key, 0)
            r1, _ = self.danger_rate(key, 1)
            if e0 > 1:
                out.append(f"danger {key}: touching it costs ~{r0:.0%} max HP/s, at 0-15px {r1:.0%}/s (observed {e0:.0f}s)")
        for table, label in (("pickups", "collecting"), ("kills", "killing")):
            for key, obs in sorted(kb[table].items(), key=lambda x: -x[1]["n"])[:4]:
                if obs["n"]:
                    avg = {rk: round(v / obs["n"], 3) for rk, v in obs["sum"].items() if abs(v) > 1e-6}
                    out.append(f"{label} {key}: avg gain {avg} (n={obs['n']})")
        terr = kb.get("terrain") or {}
        if terr.get("liquid_n", 0) >= 15 and terr.get("land_n", 0) >= 30:
            ratio = (terr["liquid_v"] / terr["liquid_n"]) / (terr["land_v"] / terr["land_n"])
            out.append(f"terrain liquid: I move at {ratio:.0%} of my land speed in it (observed {int(terr['liquid_n'])} frames)")
        for key in kb["objects"]:
            out.append(self.object_line(key))
        return out[:k]

    # ------------------------------------------------------------------ objects (curiosity + trial and error)
    def _obj(self, key: str) -> dict[str, Any]:
        return self.engine.kb["objects"].setdefault(key, {"seen": 0, "tried": 0, "positive": 0, "died": 0,
                                                          "sum": {}, "hp_cost": 0.0, "time": 0.0, "messages": []})

    def object_value(self, key: str) -> dict[str, Any]:
        o = self._obj(key)
        n = o["tried"]
        return {"tried": n, "positive_rate": round(o["positive"] / n, 2) if n else None,
                "avg_gain": {rk: round(v / n, 3) for rk, v in o["sum"].items()} if n else {},
                "avg_hp_cost": round(o["hp_cost"] / n, 3) if n else None, "died": o["died"],
                "messages": o.get("messages", [])[:5]}

    def object_line(self, key: str) -> str:
        v = self.object_value(key)
        if not v["tried"]:
            return f"object '{key}': never investigated"
        msg_str = f", on-screen text when touched: {v['messages']}" if v.get("messages") else ""
        return (f"object '{key}': investigated {v['tried']}x, something good happened {v['positive_rate']:.0%}, "
                f"avg gain {v['avg_gain']}, avg HP cost {v['avg_hp_cost']:.0%}, died {v['died']}x{msg_str}")

    def decide(self, d: dict[str, Any]) -> dict[str, Any]:
        """Jev decides whether to go look at / use a thing, from my experience only (no forced rules)."""
        key = str(d.get("type") or "unknown")
        hp = float(d.get("hp_pct") or 1.0)
        level = int(d.get("level") or 1)
        o = self._obj(key)
        o["seen"] += 1
        explore = bool(d.get("explore", True))
        v = self.object_value(key)
        ag = v.get("avg_gain") or {}
        hp_cost = float(v.get("avg_hp_cost") or 0)
        msgs = v.get("messages") or []
        has_buff_msg = ((v.get("positive_rate") or 0) >= 0.35 and hp_cost < 0.06
                        and any("Poder" in m or "%" in m or "durante" in m for m in msgs))
        has_special_reward = (ag.get("bonds", 0) >= 0.08 or ag.get("items", 0) >= 0.15
                              or ag.get("t10", 0) >= 0.02 or has_buff_msg)

        # High-HP-cost trial/arena object that grants bonds/special text: wait until strong enough, then engage!
        if v["tried"] >= 3 and hp_cost >= 0.20 and (ag.get("bonds", 0) > 0 or any("Vínculo" in m or "grieta" in m for m in msgs)):
            if level >= 11 and hp >= 0.60:
                return {"type": key, "engage": True, "source": "experience_ev",
                        "reason": f"strong enough (Lv.{level}, HP {hp:.0%}) for high-cost bond/arena object"}
            return {"type": key, "engage": False, "source": "wait_stronger",
                    "reason": f"wait until Lv>=11 and HP>=60% for high-cost object (now Lv.{level}, HP {hp:.0%})"}

        # If already tried >= 3 times and learned to be a net hazard with negligible rewards, skip
        if v["tried"] >= 3 and not has_special_reward and (v["positive_rate"] or 0) <= 0.25 and hp_cost >= 0.04:
            return {"type": key, "engage": False, "source": "learned_hazard", "reason": "learned to be harmful"}

        # If tried >= 3 times and learned to offer only HP (no items, bonds, tickets, levels, or buff text) and current HP is high, skip
        if v["tried"] >= 3 and hp >= 0.85:
            has_non_hp = (has_special_reward or ag.get("t1", 0) >= 0.05 or ag.get("level", 0) > 0.1)
            if not has_non_hp:
                return {"type": key, "engage": False, "source": "hp_full", "reason": "HP already high (>=85%) for healing-only object"}

        # Curiosity: a player tries a new thing a few times before judging it. After that, my own results decide.
        journal = getattr(self.engine, "journal", None)
        plan = (journal.data.get("next_plan") or {}) if journal else {}
        focus_q = journal.data["questions"].get(plan.get("focus_question") or "") if journal else None
        is_my_question = bool(focus_q and focus_q.get("about") == key)
        good_so_far = (v.get("positive_rate") or 0) >= 0.40
        engage = hp >= 0.35 and ((explore and v["tried"] < 3) or good_so_far or is_my_question)
        source = "my_question" if is_my_question else "curiosity" if v["tried"] < 3 else "experience"

        # Clear-cut by my own record? (value measured with my current value weights; HP cost I observed)
        w = self.engine.kb.get("best_policy") or {}
        ev = (w.get("w_level", 40) * ag.get("level", 0) + w.get("w_xp", 30) * ag.get("xp", 0)
              + w.get("w_coins", 0.3) * ag.get("coins", 0) + w.get("w_bonds", 25) * ag.get("bonds", 0)
              + w.get("w_items", 45) * ag.get("items", 0)
              + w.get("w_tickets", 15) * (ag.get("t1", 0) + 10 * ag.get("t10", 0)) + w.get("w_hp", 35) * ag.get("hp", 0)
              + (18.0 if has_buff_msg else 0.0))
        clearly_good = (v["tried"] >= 5 and (v["positive_rate"] or 0) >= 0.40 and ev > 10
                        and v["died"] / v["tried"] < 0.15 and hp - 1.5 * hp_cost >= 0.25)
        if clearly_good:
            return {"type": key, "engage": True, "source": "experience_ev",
                    "reason": f"my record: {v['positive_rate']:.0%} good, value {ev:.0f}, HP cost {hp_cost:.0%}"}

        # Proven dud? (tried >= 5 times, negligible positive rate, low EV, not the focus question)
        clearly_dud = (v["tried"] >= 5 and not has_special_reward and (v["positive_rate"] or 0) <= 0.20 and ev <= 5 and not is_my_question)
        if clearly_dud:
            return {"type": key, "engage": False, "source": "learned_dud",
                    "reason": f"my record: {v['positive_rate']:.0%} good, value {ev:.0f}, negligible reward"}

        jev = self.engine.jev
        if jev:
            state = {
                "goal": "clear 31:00 by becoming strong enough to wipe dense waves and bosses",
                "thing": key,
                "my_experience_with_it": self.object_line(key),
                "is_this_my_question_this_run": is_my_question,
                "my_question_this_run": plan.get("question_text"),
                "my_policy_curiosity": "on" if explore else "off",
                "now": {
                    "hp_pct": round(hp, 2), "level": d.get("level"), "distance_px": round(float(d.get("distance") or 0)),
                    "things_near_me": d.get("enemies_near"), "time": d.get("t")
                },
                "recent_on_screen_text": d.get("messages") or [],
                "what_i_know": self.brief(8)
            }
            q = {
                "engage": {
                    "type": "choice",
                    "instructions": f"Should I go to this {key} now? Decide from my own experience with it, my curiosity, my question for this run, and the risk right now.",
                    "criteria": {
                        "engage": "go to it / touch it now",
                        "skip": "leave it for now"
                    }
                }
            }
            res = jev(state, q, 2.5)
            if res and "answers" in res:
                ans = res["answers"].get("engage", {})
                if v["tried"] >= 2:
                    ch = ans.get("choice") or ("engage" if engage else "skip")
                else:
                    from learning import LearningEngine
                    ch = LearningEngine._sample(ans) or ("engage" if engage else "skip")
                if ch in ("engage", "skip"):
                    engage, source = ch == "engage", "jev"
        if is_my_question and hp >= 0.35:
            engage, source = True, "my_question"
        return {"type": key, "engage": engage, "source": source, "reason": "never tried" if v["tried"] == 0 else ""}

    def outcome(self, d: dict[str, Any]) -> dict[str, Any]:
        key = str(d.get("type") or "unknown")
        # Ignore non-interactions (timed out walking across the map or run ended)
        if d.get("reached") is False:
            return {"type": key, "recorded": False, "reason": "not reached"}
        o = self._obj(key)
        o["tried"] += 1
        gains = d.get("reward") or {}
        msgs = d.get("messages") or []
        hp_cost = max(0.0, float(d.get("hp_cost") or 0))
        why = str(d.get("why") or "")
        positive = (any(float(gains.get(k) or 0) > 0 for k in ("items", "level", "bonds", "coins", "t1", "t10"))
                    or float(gains.get("hp") or 0) > 0.05
                    or (why == "it changed when I touched it" and hp_cost < 0.05 and bool(msgs))
                    or (why == "came back from the other place" and any("Vínculo" in m for m in msgs)))
        if positive and not d.get("died"):
            o["positive"] += 1
        if d.get("died"):
            o["died"] += 1
        for rk, v_val in gains.items():
            o["sum"][rk] = round(o["sum"].get(rk, 0.0) + float(v_val or 0), 4)
        for m in msgs:
            if m and m not in o.setdefault("messages", []):
                o["messages"].append(m)
        o["messages"] = o.get("messages", [])[:10]
        o["hp_cost"] = round(o["hp_cost"] + hp_cost, 3)
        o["time"] = round(o["time"] + float(d.get("time_cost") or 0), 1)
        line = self.object_line(key)
        self.engine._set_lesson(f"obj:{key}", line, "observation", min(1.0, o["tried"] / 5), [self.engine.kb["total_runs"] + 1])
        self.engine.save()
        return {"type": key, "recorded": True, "evidence": line}

    # ------------------------------------------------------------------ meta
    def record_partner_result(self, partner: str, t: float) -> None:
        if partner:
            p = self.engine.kb["partners"].setdefault(partner, {"runs": 0, "time_sum": 0.0, "best": 0.0})
            p["runs"] += 1
            p["time_sum"] = round(p["time_sum"] + t, 1)
            p["best"] = max(p["best"], round(t, 1))

    def decide_meta(self, d: dict[str, Any]) -> dict[str, Any]:
        """Partner choice from the run plan (or visible dex stats + own results); gacha with curiosity."""
        owned = d.get("owned") or []
        cur = d.get("current") or {}
        tickets = d.get("tickets") or {}
        stats = self.engine.kb["partners"]
        choice, source = cur.get("dex"), "keep"
        jev = self.engine.jev
        journal = getattr(self.engine, "journal", None)
        if journal:
            journal.note_meta(owned, tickets)
        plan = (journal.data.get("next_plan") or {}) if journal else {}
        owned_dex = {o.get("dex") for o in owned}
        if journal and plan.get("partner_dex") is not None and plan["partner_dex"] in owned_dex:
            choice, source = plan["partner_dex"], "plan"
        elif jev and len(owned) > 1:
            crit = {"keep": f"keep {cur.get('name')} (my results: {stats.get(cur.get('name'), 'none')})"}
            for p in owned[:12]:
                if p.get("dex") != cur.get("dex"):
                    crit[f"dex_{p['dex']}"] = (f"{p.get('name')} - shown stats hp {p.get('hp')} atk {p.get('atk')} spd {p.get('spd')}, "
                                               f"stars {p.get('rarity')}; my results: {stats.get(p.get('name'), 'never used')}")
            q = {"partner": {"type": "choice", "criteria": crit, "instructions":
                 "Choose my partner for the next run using only the shown stats and my own results with each partner."}}
            res = jev({"recent_runs": self.engine.kb["runs"][-6:]}, q, 4.0)
            if res and "answers" in res:
                ch = res["answers"].get("partner", {}).get("choice", "")
                if ch.startswith("dex_"):
                    try:
                        choice, source = int(ch[4:]), "jev"
                    except ValueError:
                        pass
                elif ch == "keep":
                    source = "jev"
        n_tickets = int(tickets.get("t1") or 0) + int(tickets.get("t10") or 0)
        pull = n_tickets > 0
        pulls_before = int(journal.data["meta_seen"].get("gacha_pulls", 0)) if journal else 0
        if pull and journal and pulls_before == 0:
            source_pull = "curiosity"   # a player tries a new button at least once
        elif pull and int(tickets.get("t1") or 0) + 10 * int(tickets.get("t10") or 0) >= 10:
            source_pull = "hoard_limit"  # unused tickets teach me nothing
        elif pull and jev:
            ms = journal.data["meta_seen"] if journal else {}
            used = sorted(n for n, p in stats.items() if p.get("runs"))
            q = {"gacha": {"type": "choice",
                           "instructions": (f"I have {n_tickets} tickets {tickets}. The menu has a gacha that uses them. "
                                            f"I have pulled {pulls_before} times before and gained these partners from it: "
                                            f"{[p.get('name') for p in ms.get('partners_gained', [])]}. "
                                            f"I own {len(ms.get('owned') or [])} partners and have only ever played {used}. Use the tickets now?"),
                           "criteria": {"pull": "use the tickets now", "keep": "keep them for later"}}}
            res = jev({"partners_owned": len(owned), "my_partner_results": stats}, q, 3.0)
            pull = bool(res and "answers" in res and res["answers"].get("gacha", {}).get("choice") == "pull")
            source_pull = "jev"
        else:
            source_pull = "none"
        if pull and journal:
            journal.note_pull()
        return {"partner_dex": choice, "partner_source": source, "pull": pull, "pull_source": source_pull}

    def decide_upgrade(self, d: dict[str, Any]) -> dict[str, Any]:
        """Options = visible upgrade rows (name, description, level, cost). Returns one id or 'save'."""
        opts = [o for o in (d.get("options") or []) if o.get("affordable")]
        if not opts:
            return {"buy": None, "source": "none_affordable"}
        hist = self.engine.kb["upgrades_hist"]
        jev = self.engine.jev
        journal = getattr(self.engine, "journal", None)
        streak = int(journal.data["meta_seen"].get("save_streak", 0)) if journal else 0
        crit = {"save": f"save coins for later (I already chose to save {streak} times in a row; coins do nothing until spent)"}
        if streak >= 3:
            crit.pop("save")   # hoarding forever teaches me nothing: try something
        for o in opts:
            h = hist.get(o["id"], {})
            seen = (f"runs after buying: {h.get('runs_after', 0)}, avg survival change {h.get('avg_delta', 'unknown')}"
                    if h.get("bought") else "never bought - I don't know what it really does for me")
            crit[o["id"]] = (f"{o.get('name')}: {o.get('desc')} (level {o.get('level')}/{o.get('max')}, cost {o.get('cost')}); {seen}")
        choice, source = opts[0]["id"], "fallback_first"
        if jev:
            q = {"upgrade": {"type": "choice", "criteria": crit, "instructions":
                 "Pick one permanent upgrade to buy now (or save), judging only from the description and my results."}}
            res = jev({"coins": d.get("coins"), "recent_runs": self.engine.kb["runs"][-6:],
                       "lessons": self.engine.top_lessons(5)}, q, 4.0)
            if res and "answers" in res:
                ch = res["answers"].get("upgrade", {}).get("choice", "")
                if ch in crit:
                    choice, source = (None if ch == "save" else ch), "jev"
        if choice:
            h = hist.setdefault(choice, {"bought": 0, "bought_at_run": []})
            h["bought"] += 1
            h["bought_at_run"].append(self.engine.kb["total_runs"])
        if journal:
            journal.data["meta_seen"]["save_streak"] = 0 if choice else streak + 1
            journal.save()
        return {"buy": choice, "source": source}

    def update_upgrade_effects(self) -> None:
        """Did runs get longer after buying each upgrade? (pure observation)"""
        runs = self.engine.kb["runs"]
        for uid, h in self.engine.kb["upgrades_hist"].items():
            if not h.get("bought_at_run"):
                continue
            first = h["bought_at_run"][0]
            before = [r["time"] for r in runs if r["run"] <= first][-5:]
            after = [r["time"] for r in runs if r["run"] > first]
            h["runs_after"] = len(after)
            if before and after:
                h["avg_delta"] = round(sum(after[-5:]) / len(after[-5:]) - sum(before) / len(before), 1)
