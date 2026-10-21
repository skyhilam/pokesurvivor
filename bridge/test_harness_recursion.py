#!/usr/bin/env python3
"""Harness² loop tests with a fake Jev and synthetic run records (offline, no network).

    python3 bridge/test_harness_recursion.py

Covers: every grammar candidate passes validate_tree with no forbidden phrase;
a candidate that lengthens survival is adopted and one that shortens it is
rolled back (non-conflicting adopted edits merge, conflicting ones do not);
the library round-trips through library.json; knobs, play style and build are
pinned while a candidate is probed; the deterministic fallback runs when Jev is
unavailable; duplicate runs are ignored; trajectories render and truncate; the
bridge exposes harness_id, GET /harness and GET /harness/library, and
JEV_HARNESS_RECURSION=0 leaves the PR1 behaviour untouched.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_recursion import (  # noqa: E402
    BASE_ID, HarnessRecursion, apply_edit, edit_target, enumerate_edits,
)
from harness_tree import HarnessTree, load_forbidden, validate_tree  # noqa: E402
from journal import Journal, STANCES  # noqa: E402
from learning import DEFAULT_POLICY, LearningEngine  # noqa: E402
from opportunity import WorldModel  # noqa: E402
from trajectory import MIN_CHARS, decisions_for_run, render_trajectory, summarize_run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "bridge" / "harness" / "base"
OK = True


def check(name: str, cond: bool, extra: str = "") -> None:
    global OK
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  | ' + extra) if extra else ''}")
    OK = OK and bool(cond)


# ---------------------------------------------------------------- helpers
class FakeJev:
    """Answers choice questions. For the harness 'edit' question it picks the next
    wanted edit id from a queue when offered, else the first option."""

    def __init__(self, wanted: list[str] | None = None) -> None:
        self.wanted = list(wanted or [])
        self.calls: list[tuple[dict, dict, float]] = []

    def __call__(self, state: dict, questions: dict, timeout: float) -> dict:
        self.calls.append((state, questions, timeout))
        answers = {}
        for name, q in questions.items():
            keys = list(q["criteria"])
            pick = keys[0]
            if name == "edit" and self.wanted:
                want = self.wanted[0]
                if want in keys:
                    pick = want
                    self.wanted.pop(0)
            answers[name] = {"choice": pick}
        return {"answers": answers}


def make(jev=None, journal: bool = True, **kw):
    d = Path(tempfile.mkdtemp())
    eng = LearningEngine(d, jev=jev)
    if journal:
        eng.journal = Journal(d, eng, WorldModel(eng))
    kw.setdefault("k", 1)
    kw.setdefault("samples", 1)
    kw.setdefault("offer_max", 1000)
    rec = HarnessRecursion(d, eng, enabled=True, **kw)
    rec.activate_next()
    return d, eng, rec


def run_rec(run_id: str, t: float, **kw) -> dict:
    rec = {"run_id": run_id, "time": t, "level": int(t / 30) + 1, "kills": int(t), "partner": "Dragonite",
           "death_cause": "enemy:Rattata", "damage_sources": {"enemy:Rattata": 80, "mark:circle": 20},
           "picks": [{"t": 12, "key": "up:gust", "title": "Gust Nv.1 > 2", "offered": ["up:gust", "stat:power"]}],
           "timeline": [{"t": 10, "level": 2, "kills": 5, "enemies": 3, "hp_pct": 0.9},
                        {"t": 20, "level": 3, "kills": 12, "enemies": 6, "hp_pct": 0.7}],
           "messages": [{"t": 5, "text": "hello"}], "world_deltas": {"seen": []}, "opportunities": [],
           "moves": [{"id": "gust", "kills": 10, "dmg": 500, "combat_sec": 30}]}
    rec.update(kw)
    return rec


def play(eng, rec_loop, i: int, t: float, **kw) -> tuple[dict, dict]:
    """One run through start_run -> on_run_start -> end_run -> after_run."""
    s = eng.start_run({"client_run_id": f"r{i}", "partner": "Dragonite"})
    hid = rec_loop.on_run_start(s["run_id"])
    trial = json.loads(json.dumps(eng.kb["pending"][s["run_id"]]))
    r = run_rec(s["run_id"], t, **kw)
    res = eng.end_run(r)
    out = rec_loop.after_run(r, trial=trial, result=res)
    out["harness_used"] = hid
    return s, out


# ---------------------------------------------------------------- 1. grammar + validation
def test_candidates_valid() -> None:
    base_files = dict(HarnessTree.load(BASE).files)
    edits = enumerate_edits(base_files, base_files)
    kinds = {e.kind for e in edits}
    check("grammar lists general and decision edits", kinds == {"general", "decision"}, str(kinds))
    ids = [e.id for e in edits]
    check("edit ids are unique", len(ids) == len(set(ids)))
    for needle in ("memory.k:handle_levelup.lessons:+2", "memory.inject:handle_levelup.lessons:off",
                   "timeout:handle_levelup:x1.5", "guardrails:on", "skills.shortcut:learned_combo_pair:off",
                   "skills.weight:unseen:+25%"):
        check(f"grammar offers {needle}", needle in ids)

    d, eng, rec = make(FakeJev())
    valid = rec.candidate_edits(BASE_ID)
    check("at least 20 candidates are applicable to the base tree", len(valid) >= 20, str(len(valid)))
    forbidden = load_forbidden()
    bad = []
    for edit, files in valid:
        try:
            validate_tree(files)
        except ValueError as exc:
            bad.append(f"{edit.id}: {exc}")
            continue
        for name, text in files.items():
            for pat, why in forbidden.items():
                if re.search(pat, text):
                    bad.append(f"{edit.id}: {name}: {why}")
        if files == dict(HarnessTree.load(BASE).files):
            bad.append(f"{edit.id}: no-op")
    check("every candidate passes validate_tree, has no forbidden phrase and changes the tree", not bad, "; ".join(bad[:3]))

    # grammar semantics
    out = apply_edit("memory.k:handle_levelup.lessons:+2", base_files, base_files)
    mem = json.loads(out["memory.json"])
    check("+2 raises the count and the matching inject entry",
          mem["decisions"]["handle_levelup"]["lessons"] == 8 and mem["decisions"]["handle_levelup"]["inject"]["my_lessons"]["k"] == 8)
    out2 = apply_edit("memory.inject:handle_levelup.lessons:off", out, base_files)
    mem2 = json.loads(out2["memory.json"])
    check("off sets the count to 0", mem2["decisions"]["handle_levelup"]["lessons"] == 0
          and mem2["decisions"]["handle_levelup"]["inject"]["my_lessons"]["k"] == 0)
    out3 = apply_edit("memory.inject:handle_levelup.lessons:on", out2, base_files)
    check("on restores the base count", json.loads(out3["memory.json"])["decisions"]["handle_levelup"]["lessons"] == 6)
    check("on is a no-op when already injected", apply_edit("memory.inject:handle_levelup.lessons:on", base_files, base_files) is None)
    g = apply_edit("guardrails:on", base_files, base_files)
    check("guardrails:on flips harness.json and the md header",
          json.loads(g["harness.json"])["inject"]["guardrails"] is True and g["guardrails.md"].startswith("inject: true\n"))
    t = apply_edit("timeout:handle_levelup:x1.5", base_files, base_files)
    check("timeout x1.5", json.loads(t["plugins.json"])["jev_timeout_s"]["handle_levelup"] == 6.0)
    sk = apply_edit("skills.shortcut:learned_combo_pair:off", base_files, base_files)
    check("shortcut off", json.loads(sk["skills.json"])["handle_levelup"]["shortcuts"]["learned_combo_pair"]["enabled"] is False)
    w = apply_edit("skills.weight:zero_damage:-25%", base_files, base_files)
    weights = {x["id"]: x["value"] for x in json.loads(w["skills.json"])["handle_levelup"]["fallback_score"]["weights"]}
    check("weight -25% keeps sign", weights["zero_damage"] == -18.8, str(weights["zero_damage"]))
    check("edits on the same field conflict", edit_target("memory.k:engage.world_brief:+2") == edit_target("memory.k:engage.world_brief:-2")
          and edit_target("timeout:engage:x1.5") != edit_target("memory.k:engage.world_brief:-2"))
    check("unknown edit is rejected", apply_edit("memory.k:nope.lessons:+2", base_files, base_files) is None
          and apply_edit("bogus:thing", base_files, base_files) is None)


# ---------------------------------------------------------------- 2. adopt / roll back / merge
def test_adopt_and_rollback() -> None:
    jev = FakeJev(["timeout:handle_levelup:x1.5", "memory.k:handle_levelup.lessons:+2"])
    d, eng, rec = make(jev)
    s0, o0 = play(eng, rec, 0, 100)
    check("first run plays the base harness", o0["harness_used"] == BASE_ID and o0["next"].startswith("c"), str(o0))
    cand = rec.lib["candidates"][o0["next"]]
    check("Jev chose the candidate from the offered edits", cand["source"] == "jev" and cand["edits"] == ["timeout:handle_levelup:x1.5"], str(cand["edits"]))
    check("candidate tree written to logs/harness/candidates/<id>/",
          (d / "harness" / "candidates" / cand["id"] / "plugins.json").is_file()
          and not any(p.name.startswith(".tmp_") for p in (d / "harness" / "candidates").iterdir()))
    check("engine now plays the candidate tree", eng.harness.fingerprint() == cand["fingerprint"] and eng.jev_timeout("handle_levelup") == 6.0)
    state, questions, _ = jev.calls[-1]
    crit = questions["edit"]["criteria"]
    check("proposal question is type=choice over validated edits", questions["edit"]["type"] == "choice" and len(crit) >= 20)
    check("criteria carry the library record per edit", all("my record with this change" in v for v in crit.values()))
    check("state carries the trajectory, its summary and the library lessons",
          "[ACTION]" in state["trajectory"] and "[ERROR]" in state["trajectory"]
          and state["last_run"]["survived"] == "01:40" and "harness_lessons" in state
          and "survived 01:40" in questions["edit"]["instructions"])

    s1, o1 = play(eng, rec, 1, 160)   # candidate survives longer -> adopted
    check("longer-surviving candidate is adopted", o1["composed"] and o1["composed"]["adopted"] == "h0001"
          and rec.lib["current"] == "h0001" and rec.lib["candidates"][cand["id"]]["status"] == "adopted", str(o1["composed"]))
    check("adopted harness keeps the edit and is loaded", rec.lib["harnesses"]["h0001"]["edits"] == ["timeout:handle_levelup:x1.5"]
          and (d / "harness" / "adopted" / "h0001" / "plugins.json").is_file()
          and eng.harness.fingerprint() == rec.lib["harnesses"]["h0001"]["fingerprint"])
    les = rec.lib["lessons"]["timeout:handle_levelup:x1.5"]
    check("library lesson records the help", les["tried"] == 1 and les["helped"] == 1 and les["delta_sum"] == 60.0, str(les))

    s2, o2 = play(eng, rec, 2, 150)   # baseline run on h0001
    s3, o3 = play(eng, rec, 3, 155)   # run 4 -> retest of current
    check("every 4th run re-tests the current harness", o2["next"] == "h0001" and o2["reason"] == "retest current harness"
          and o3["harness_used"] == "h0001", f"{o2['reason']} / {o3['harness_used']}")
    check("after the retest a new candidate is probed", o3["next"].startswith("c") and o3["pinned"], str(o3))
    c2 = rec.lib["candidates"][o3["next"]]
    check("second candidate edits the current harness, not base", c2["parent"] == "h0001" and c2["edits"] == ["memory.k:handle_levelup.lessons:+2"])
    s4, o4 = play(eng, rec, 4, 90)    # shorter -> rolled back
    check("shorter-surviving candidate is rolled back", rec.lib["candidates"][c2["id"]]["status"] == "rolled_back"
          and rec.lib["current"] == "h0001" and not o4["composed"]["adopted"]
          and "worse" in rec.lib["candidates"][c2["id"]]["conclusion"], rec.lib["candidates"][c2["id"]]["conclusion"])
    check("rollback keeps h0001 as the parent of what comes next",
          rec.load_tree("h0001").get("memory.decisions.handle_levelup.lessons") == 6
          and rec.lib["candidates"][o4["next"]]["parent"] == "h0001" and rec.lib["round"]["parent"] == "h0001"
          and o4["next"] not in (c2["id"], cand["id"]), str(o4))
    les2 = rec.lib["lessons"]["memory.k:handle_levelup.lessons:+2"]
    check("library lesson records the hurt", les2["tried"] == 1 and les2["hurt"] == 1 and les2["delta_sum"] < 0, str(les2))
    md = (d / "HARNESS.md").read_text(encoding="utf-8")
    check("HARNESS.md lists harnesses, candidates and lessons", "h0001" in md and c2["id"] in md and "timeout:handle_levelup:x1.5" in md)
    check("death cause is part of the conclusion", "died mostly to" in rec.lib["harnesses"]["h0001"]["conclusion"])


def test_merge_non_conflicting() -> None:
    jev = FakeJev(["timeout:engage:x1.5", "memory.k:engage.world_brief:+2"])
    d, eng, rec = make(jev, k=2)
    play(eng, rec, 0, 100)
    play(eng, rec, 1, 150)
    _, o = play(eng, rec, 2, 160)
    comp = o["composed"]
    check("K=2: two better candidates are merged into one adopted harness",
          comp and comp["adopted"] == "h0001" and sorted(comp["applied"]) == ["memory.k:engage.world_brief:+2", "timeout:engage:x1.5"], str(comp))
    tree = rec.load_tree("h0001")
    check("merged tree carries both edits", tree.get("plugins.jev_timeout_s.engage") == 3.75 and tree.get("memory.decisions.engage.world_brief") == 10)

    jev = FakeJev(["memory.k:engage.world_brief:+2", "memory.k:engage.world_brief:-2"])
    d, eng, rec = make(jev, k=2)
    play(eng, rec, 0, 100)
    play(eng, rec, 1, 180)
    _, o = play(eng, rec, 2, 150)
    comp = o["composed"]
    check("conflicting edits: only the bigger improvement is merged",
          comp and comp["applied"] == ["memory.k:engage.world_brief:+2"] and comp["skipped"] == ["memory.k:engage.world_brief:-2"], str(comp))
    statuses = {c["edits"][0]: c["status"] for c in rec.lib["candidates"].values()}
    check("conflicting better candidate is not adopted", statuses["memory.k:engage.world_brief:-2"] == "rolled_back"
          and statuses["memory.k:engage.world_brief:+2"] == "adopted", str(statuses))


def test_samples_wait() -> None:
    d, eng, rec = make(FakeJev(), samples=2)
    _, o0 = play(eng, rec, 0, 100)
    _, o1 = play(eng, rec, 1, 200)
    check("not enough samples -> keep probing the same candidate", o0["next"] == o1["next"] and o1["reason"] == "continue probing candidate"
          and not o1["composed"], str(o1))
    _, o2 = play(eng, rec, 2, 210)
    check("compose happens once the candidate has its samples", o2["composed"] and o2["composed"]["adopted"] == "h0001", str(o2["composed"]))


# ---------------------------------------------------------------- 3. library persistence
def test_library_roundtrip() -> None:
    d, eng, rec = make(FakeJev())
    for i, t in enumerate([100, 140, 120, 130, 90]):
        play(eng, rec, i, t)
    before = json.loads(json.dumps(rec.lib, sort_keys=True))
    saved = json.loads((d / "harness" / "library.json").read_text(encoding="utf-8"))
    check("library.json equals the in-memory library", saved == before)
    eng2 = LearningEngine(d, jev=None)
    eng2.journal = Journal(d, eng2, WorldModel(eng2))
    rec2 = HarnessRecursion(d, eng2, enabled=True, k=1, samples=1)
    check("library reloads identically", json.loads(json.dumps(rec2.lib, sort_keys=True)) == before)
    nxt = rec2.activate_next()
    check("restart resumes the scheduled harness", nxt == before["next"] and eng2.harness.fingerprint() == rec2.record(nxt)["fingerprint"])
    check("adopted and candidate trees reload from disk", all(rec2.load_tree(h).fingerprint() == v["fingerprint"]
                                                           for h, v in list(rec2.lib["harnesses"].items()) + list(rec2.lib["candidates"].items())))


# ---------------------------------------------------------------- 4. one variable at a time
def test_pinned_while_probing() -> None:
    d, eng, rec = make(FakeJev())
    s0, o0 = play(eng, rec, 0, 100)
    ref = rec.lib["harnesses"][BASE_ID]["runs"][-1]
    plan = eng.journal.data["next_plan"]
    check("probe pins play style and build to the parent's last run", o0["pinned"] and plan["harness_probe"] == o0["next"]
          and plan["stance"] == ref["stance"] and plan["build_id"] == ref["build_id"], f"{plan['stance']}=={ref['stance']} {plan['build_id']}=={ref['build_id']}")
    s1 = eng.start_run({"client_run_id": "probe", "partner": "Dragonite"})
    check("knobs are unchanged for the probe run", s1["policy"] == ref["policy"] and s1["plan"]["stance"] == ref["stance"])
    check("stance label stays consistent", plan["stance_label"] == STANCES[plan["stance"]]["label"])
    r = run_rec("probe", 130)
    res = eng.end_run(r)
    out = rec.after_run(r, trial={"policy": s1["policy"], "plan": s1.get("plan", {}) | {"stance": plan["stance"], "build_id": plan["build_id"]}}, result=res)
    check("after the probe the pin is lifted", not out["pinned"] and "harness_probe" not in eng.journal.data["next_plan"] and eng.policy_pin is None)

    # no-journal engine: LearningEngine must not start a new knob experiment while probing
    d, eng, rec = make(FakeJev(), journal=False)
    s0 = eng.start_run({"client_run_id": "n0", "partner": "Dragonite"})
    rec.on_run_start("n0")
    r = run_rec("n0", 100)
    res = eng.end_run(r)
    out = rec.after_run(r, trial={"policy": s0["policy"], "why": s0["experiment"]}, result=res)
    s1 = eng.start_run({"client_run_id": "n1", "partner": "Dragonite"})
    check("no-journal engine keeps the knobs while probing", out["pinned"] and s1["policy"] == s0["policy"] and s1["source"] == "harness_probe", s1["source"])
    rec.on_run_start("n1")
    r = run_rec("n1", 90)
    res = eng.end_run(r)
    out = rec.after_run(r, trial={"policy": s1["policy"], "why": s1["experiment"]}, result=res)
    check("K=1: the next candidate is probed right after a rollback", out["pinned"] and rec.lib["current"] == BASE_ID)
    s2 = eng.start_run({"client_run_id": "n2", "partner": "Dragonite"})
    rec.on_run_start("n2")
    r = run_rec("n2", 95)
    res = eng.end_run(r)
    out = rec.after_run(r, trial={"policy": s2["policy"], "why": s2["experiment"]}, result=res)
    s3 = eng.start_run({"client_run_id": "n3", "partner": "Dragonite"})
    check("knob experiments resume on the re-test run", not out["pinned"] and out["reason"] == "retest current harness"
          and s3["source"] != "harness_probe" and eng.policy_pin is None, f"{out['reason']} / {s3['source']}")


# ---------------------------------------------------------------- 5. fallback without Jev
def test_fallback_without_jev() -> None:
    d, eng, rec = make(None)
    valid = [e for e, _ in rec.candidate_edits(BASE_ID)]
    keep = "memory.k:checkpoint.lessons:+2"
    for e in valid:
        rec.lib["lessons"][e.id] = {"tried": 1, "helped": 0, "hurt": 1, "delta_sum": -5.0}
    rec.lib["lessons"][keep] = {"tried": 1, "helped": 1, "hurt": 0, "delta_sum": 5.0}
    rec.save()
    _, o0 = play(eng, rec, 0, 100)
    cand = rec.lib["candidates"][o0["next"]]
    check("Jev unavailable -> deterministic fallback picks the edit the library does not oppose",
          cand["source"] == "fallback" and cand["edits"] == [keep], str(cand["edits"]))
    _, o1 = play(eng, rec, 1, 130)
    check("fallback-chosen candidate goes through the same compose", o1["composed"]["adopted"] == "h0001")

    d, eng, rec = make(None)
    valid = [e for e, _ in rec.candidate_edits(BASE_ID)]
    for n, e in enumerate(valid):
        rec.lib["lessons"][e.id] = {"tried": 3 + (n % 5), "helped": 0, "hurt": 1, "delta_sum": -5.0}
    least = "timeout:gacha:x0.75"
    rec.lib["lessons"][least] = {"tried": 1, "helped": 0, "hurt": 1, "delta_sum": -1.0}
    _, o0 = play(eng, rec, 0, 100)
    check("all opposed -> least tried edit", rec.lib["candidates"][o0["next"]]["edits"] == [least], str(rec.lib["candidates"][o0["next"]]["edits"]))

    class Flaky(FakeJev):
        def __call__(self, state, questions, timeout):
            if "edit" in questions:
                raise RuntimeError("boom")
            return super().__call__(state, questions, timeout)

    d, eng, rec = make(Flaky())
    _, o0 = play(eng, rec, 0, 100)
    check("Jev error -> fallback", rec.lib["candidates"][o0["next"]]["source"] == "fallback")


# ---------------------------------------------------------------- 6. duplicates
def test_duplicate_ignored() -> None:
    d, eng, rec = make(FakeJev())
    s0, o0 = play(eng, rec, 0, 100)
    n_runs = len(rec.lib["harnesses"][BASE_ID]["runs"])
    seen = rec.lib["runs_seen"]
    r = run_rec("r0", 100)
    again = rec.after_run(r)
    check("same run_id is ignored by after_run", again["status"] == "duplicate" and rec.lib["runs_seen"] == seen
          and len(rec.lib["harnesses"][BASE_ID]["runs"]) == n_runs)
    again = rec.after_run({**r, "run_id": "other"})
    check("same result under another id is ignored", again["status"] == "duplicate" and rec.lib["runs_seen"] == seen)
    check("engine dedupe agrees", eng.is_duplicate(r))
    before = json.loads(json.dumps(rec.lib, sort_keys=True))
    rec.after_run(r)
    check("duplicate leaves the library untouched", json.loads(json.dumps(rec.lib, sort_keys=True)) == before)


# ---------------------------------------------------------------- 7. trajectory
def test_trajectory() -> None:
    r = run_rec("t1", 321, opportunities=[{"type": "object:purple portal", "t": 60, "why": "it changed when I touched it", "reward": {"level": 1}, "hp_cost": 0.1},
                                          {"type": "object:trap", "t": 300, "why": "it hurt", "reward": {}, "hp_cost": 0.5, "died": True}])
    r["trial"] = {"plan": {"stance": "boss_hunter", "build_id": "free", "question_text": "what is the portal?"}}
    lines = [
        {"event": "run_start", "run_id": "t0", "run_number": 1},
        {"event": "levelup", "time": 3, "decision": {"action": "pick", "card": "Other", "source": "jev"}, "cards": ["Other"]},
        {"event": "run_end", "run": 1},
        {"event": "run_start", "run_id": "t1", "run_number": 2, "experiment": "x", "harness_id": "c0001"},
        {"event": "levelup", "time": 12, "decision": {"action": "pick", "card": "Gust Nv.1 > 2", "source": "jev"}, "cards": ["Gust Nv.1 > 2", "Power Upgrade"]},
        {"event": "item", "time": 40, "decision": {"action": "keep", "source": "experience_fallback", "current": "berry", "offered": "scope"}},
        {"event": "checkpoint", "t": 60, "posture": "aggression same", "source": "jev"},
        {"event": "opportunity", "t": 60, "type": "object:purple portal", "engage": True, "source": "jev", "reason": ""},
        {"event": "levelup", "time": 70, "decision": {"action": "pick", "index": 0, "source": "error_no_cards"}, "cards": []},
        {"event": "run_end", "run": 2},
        {"event": "run_start", "run_id": "t2", "run_number": 3},
        {"event": "levelup", "time": 5, "decision": {"action": "pick", "card": "Nope", "source": "jev"}, "cards": ["Nope"]},
    ]
    mine = decisions_for_run(lines, "t1")
    check("decisions of one run are isolated", [x.get("event") for x in mine] == ["run_start", "levelup", "item", "checkpoint", "opportunity", "levelup", "run_end"]
          and all("Nope" not in json.dumps(x) and "Other" not in json.dumps(x) for x in mine))
    text = render_trajectory(r, mine, harness_id="c0001")
    check("trajectory has ACTION / OBSERVATION / ERROR lines in time order",
          "[ACTION] t=00:12 levelup: pick 'Gust Nv.1 > 2' (jev)" in text
          and "[OBSERVATION] t=00:10 level 2 kills 5" in text
          and "[ERROR] t=01:10 levelup: error_no_cards" in text
          and "[ERROR] t=05:00 object:trap" in text
          and "[ERROR] died at 05:21 to enemy:Rattata" in text
          and text.index("t=00:10") < text.index("t=00:12") < text.index("t=01:00")
          and "harness c0001" in text and "style=boss_hunter" in text)
    s = summarize_run(r)
    check("summary carries survival, cause and worst damage sources", s["survived"] == "05:21" and s["death_cause"] == "enemy:Rattata"
          and s["hurt_most_by"][0].startswith("enemy:Rattata"))
    big = run_rec("big", 1800, timeline=[{"t": i, "level": i // 30, "kills": i, "enemies": 9, "hp_pct": 0.5} for i in range(1, 1800)])
    full = render_trajectory(big, [], max_chars=200_000)
    check("long run renders fully when allowed", len(full) > 30_000 and "[truncated" not in full)
    cut = render_trajectory(big, [], max_chars=12_000)
    check("truncation respects the configured length and keeps head and tail", len(cut) <= 12_000 and "[truncated" in cut
          and cut.startswith("# run big") and cut.rstrip().endswith("cards picked"))
    tiny = render_trajectory(big, [], max_chars=500)
    check("truncation never goes below 10000 chars", MIN_CHARS == 10_000 and 9_000 < len(tiny) <= 10_000, str(len(tiny)))


# ---------------------------------------------------------------- 8. bridge integration (separate processes)
SNIPPET = r'''
import json, sys, threading, urllib.request
sys.path.insert(0, "BRIDGE_DIR")
import jev_bridge as jb
from http.server import ThreadingHTTPServer

enabled = jb.RECURSION.enabled
s = jb.handle_run_start({"client_run_id": "b1", "partner": "Dragonite", "bot_build": "t"})
rec = {"run_id": "b1", "time": 100, "level": 4, "kills": 100, "partner": "Dragonite", "death_cause": "enemy:Rattata",
       "damage_sources": {"enemy:Rattata": 10}, "picks": [], "timeline": [], "messages": [], "moves": [], "world_deltas": {}}
e1 = jb.handle_run_end(rec)
e2 = jb.handle_run_end(rec)
httpd = ThreadingHTTPServer(("127.0.0.1", 0), jb.BridgeHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
def get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))
h = get("/harness")
lib = get("/harness/library")
health = get("/health")
httpd.shutdown()
print("RESULT " + json.dumps({"enabled": enabled, "start_harness": s.get("harness_id"), "end1": e1.get("harness"), "end1_status": e1.get("status"),
                              "end2_status": e2.get("status"), "end2_harness": e2.get("harness"),
                              "get_harness": h[0], "harness_body": h[1], "lib_status": lib[0], "lib_keys": sorted(lib[1]),
                              "health_harness": health[1].get("harness"),
                              "assignment": (jb.RECURSION.lib.get("assignments") or {}).get("b1"),
                              "fingerprint": jb.ENGINE.harness.fingerprint()}))
'''


def _bridge(env_value: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env.update({"HOME": td, "JEV_LOG_DIR": str(Path(td) / "logs"), "TYPESAFE_API_KEY": "", "JEV_HARNESS_RECURSION": env_value})
        env.pop("JEV_HARNESS_DIR", None)
        res = subprocess.run([sys.executable, "-c", SNIPPET.replace("BRIDGE_DIR", str(Path(__file__).resolve().parent))],
                             capture_output=True, text=True, env=env, timeout=180)
        line = next((ln for ln in res.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if res.returncode != 0 or not line:
            print((res.stdout + res.stderr)[-1500:])
            return {}
        return json.loads(line[len("RESULT "):])


def test_bridge_integration() -> None:
    on = _bridge("1")
    check("bridge: /run/start returns harness_id and records run_id -> harness + fingerprint",
          bool(on) and on["enabled"] and on["start_harness"] == BASE_ID and on["assignment"]
          and on["assignment"]["harness_id"] == BASE_ID and len(on["assignment"]["fingerprint"]) == 64, str(on.get("assignment")))
    check("bridge: /run/end calls after_run and schedules a candidate",
          bool(on) and on["end1_status"] == "learned" and on["end1"]["status"] == "recorded" and str(on["end1"]["next"]).startswith("c")
          and on["fingerprint"] != on["assignment"]["fingerprint"], str(on.get("end1")))
    check("bridge: duplicate run is refused before after_run", bool(on) and on["end2_status"] == "duplicate" and on["end2_harness"] is None)
    check("bridge: GET /harness and /harness/library", bool(on) and on["get_harness"] == 200 and on["harness_body"]["current"]["id"] == BASE_ID
          and on["harness_body"]["next"] == on["end1"]["next"] and on["lib_status"] == 200
          and {"harnesses", "candidates", "lessons", "assignments"} <= set(on["lib_keys"]) and on["health_harness"]["enabled"] is True)
    off = _bridge("0")
    check("bridge: JEV_HARNESS_RECURSION=0 keeps PR1 behaviour",
          bool(off) and not off["enabled"] and off["start_harness"] is None and off["end1"] is None and off["end1_status"] == "learned"
          and off["assignment"] is None and off["fingerprint"] == HarnessTree.load(BASE).fingerprint()
          and off["get_harness"] == 200 and off["harness_body"]["enabled"] is False and off["health_harness"]["enabled"] is False, str(off)[:300])


def main() -> None:
    test_candidates_valid()
    test_adopt_and_rollback()
    test_merge_non_conflicting()
    test_samples_wait()
    test_library_roundtrip()
    test_pinned_while_probing()
    test_fallback_without_jev()
    test_duplicate_ignored()
    test_trajectory()
    test_bridge_integration()
    print("\nALL PASS" if OK else "\nSOME FAILED")
    sys.exit(0 if OK else 1)


if __name__ == "__main__":
    main()
