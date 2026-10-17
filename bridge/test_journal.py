#!/usr/bin/env python3
"""
Player Journal tests (offline, no network)
    python3 bridge/test_journal.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from journal import Journal, STANCES  # noqa: E402
from learning import LearningEngine  # noqa: E402
from opportunity import WorldModel  # noqa: E402

OK = True


def check(name, cond, extra=""):
    global OK
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  | ' + extra) if extra else ''}")
    OK = OK and bool(cond)


def fake_jev_factory(log):
    """Answers like a deterministic player: always the FIRST option offered."""
    def jev(state, questions, timeout):
        log.append((state, questions))
        return {"answers": {k: {"choice": next(iter(q["criteria"]))} for k, q in questions.items()}}
    return jev


def make(jev=None):
    d = Path(tempfile.mkdtemp())
    eng = LearningEngine(d, jev=jev)
    world = WorldModel(eng)
    eng.journal = Journal(d, eng, world)
    return d, eng, world


def run_rec(run_id, t, **kw):
    rec = {"run_id": run_id, "time": t, "level": int(t / 30) + 1, "kills": int(t), "partner": "Dragonite",
           "death_cause": "enemy:Rattata", "damage_sources": {"enemy:Rattata": 80}, "picks": [], "timeline": [],
           "world_deltas": {"seen": []}, "messages": [], "opportunities": []}
    rec.update(kw)
    return rec


def main():
    # 1. first sighting -> question
    d, eng, world = make()
    j = eng.journal
    new = j.generate_from_run(run_rec("a", 100, world_deltas={"seen": ["object:purple portal", "enemy:Zubat"]}), 1)
    asks = [j.data["questions"][q]["ask"] for q in new]
    check("first sighting of an object becomes a question", any("purple portal" in a for a in asks), str(asks))
    check("ordinary enemies do not flood the question list", not any("Zubat" in a for a in asks))

    # 2. surprise: special card shows up -> question; explanation is MINED from all my runs
    picks1 = [{"key": "up:gust", "offered": ["up:gust", "stat:power"]},
              {"key": "stat:power", "offered": ["stat:power", "up:gust"]},
              {"key": "up:gust", "offered": ["up:gust", "new:tackle"]},
              {"key": "evo:gust-hurricane", "offered": ["evo:gust-hurricane", "up:gust"]}]
    new = j.generate_from_run(run_rec("b", 200, picks=picks1), 2)
    cardq = [j.data["questions"][q] for q in new if j.data["questions"][q]["kind"] == "card"]
    check("evolution card appearing for the first time becomes a question", len(cardq) == 1, cardq[0]["ask"] if cardq else "")
    # write 12 synthetic run records: the special card shows up only after 'up:gust' was picked 3 times
    import json, random as rnd
    rnd.seed(1)
    (d / "runs").mkdir(exist_ok=True)
    for i in range(12):
        n_gust = [1, 2, 3, 4][i % 4]
        picks = []
        for _ in range(n_gust):
            picks.append({"key": "up:gust", "offered": ["up:gust", "stat:power"]})
            picks.append({"key": rnd.choice(["stat:power", "new:tackle"]), "offered": ["stat:power", "new:tackle"]})
        if n_gust >= 3:
            picks.append({"key": "evo:gust-hurricane", "offered": ["evo:gust-hurricane", "up:gust"]})
        (d / "runs" / f"run_{i:04d}_s{i}.json").write_text(json.dumps({"run_id": f"s{i}", "picks": picks}))
    rules = j.mine_card_rules()
    r = next((r for r in rules if r["card"] == "evo:gust-hurricane"), None)
    check("mining finds the real condition from my own runs", r and r["after"] == "up:gust" and r["n"] == 3
          and r["necessary"] == 1.0 and r["reliable"] == 1.0, str(r))
    q = next(q for q in j.data["questions"].values() if q["about"] == "card:evo:gust-hurricane")
    check("a well-supported rule answers the card question", q["status"] == "answered")
    check("the rule becomes a build option to chase", any(k.startswith("rule_") for k in j._build_options()))

    # 3. untried partner + unused tickets -> questions; gacha tried once out of curiosity
    eng.kb["partners"] = {"Dragonite": {"runs": 90, "time_sum": 1, "best": 1}}
    res = world.decide_meta({"current": {"dex": 149, "name": "Dragonite"},
                             "owned": [{"dex": 149, "name": "Dragonite"}, {"dex": 4, "name": "Charmander"}],
                             "tickets": {"t1": 2, "t10": 0}})
    abouts = {q["about"] for q in j.data["questions"].values()}
    check("owned-but-never-played partner becomes a question", "partner:4" in abouts)
    check("never-used gacha becomes a question", "meta:gacha" in abouts)
    check("gacha is tried at least once (curiosity)", res["pull"] and res["pull_source"] == "curiosity", str(res))

    # 4. dedupe
    d, eng, world = make()
    s = eng.start_run({"partner": "Dragonite", "client_run_id": "c123"})
    check("client run id is used (no more untracked runs)", s["run_id"] == "c123")
    r1 = run_rec("c123", 321.5)
    eng.end_run(r1)
    check("same run sent twice is detected", eng.is_duplicate(r1))
    check("same result under another id is detected", eng.is_duplicate({**r1, "run_id": None}))
    check("a different run is not a duplicate", not eng.is_duplicate(run_rec("c999", 400)))

    # 5. start_run is instant (no Jev call) and returns a plan
    log = []
    d, eng, world = make(fake_jev_factory(log))
    eng.journal.generate_from_run(run_rec("x", 50, world_deltas={"seen": ["object:big golden chest"]}), 0)
    t0 = time.time()
    s = eng.start_run({"partner": "Dragonite", "client_run_id": "c1"})
    check("run start does not call Jev", len(log) == 0 and time.time() - t0 < 0.5, f"{(time.time() - t0) * 1000:.0f}ms")
    check("run start returns a plan with a question and a play style", s.get("plan") and s["plan"]["stance"] in STANCES
          and s["plan"]["question_text"], str(s.get("plan")))

    # 6. full cycle: reflection answers the question, plans the next run, writes the diary
    focus = eng.journal.data["next_plan"]["focus_question"]
    rec = run_rec("c1", 250, opportunities=[{"type": "object:big golden chest", "why": "it changed when I touched it",
                                             "reward": {"level": 1, "coins": 8}, "hp_cost": 0.1}],
                  world_deltas={"seen": ["object:big golden chest", "boss:Latios"]})
    out = eng.end_run(rec)
    q = eng.journal.data["questions"][focus]
    check("Jev judged my question from evidence", out.get("focus_verdict") == "answered" and q["status"] == "answered",
          f"{out.get('focus_verdict')} / {q['status']}")
    check("answer became a belief", q["answer"] in eng.journal.data["beliefs"])
    check("new things noticed this run became new questions", any("Latios" in x for x in out.get("new_questions", [])),
          str(out.get("new_questions")))
    check("next run is planned during reflection", out.get("next_plan") and out["next_plan"]["question_text"] != q["ask"],
          str(out.get("next_plan")))
    check("diary entry written", eng.journal.data["diary"] and "第" in eng.journal.data["diary"][-1]["text"],
          eng.journal.data["diary"][-1]["text"] if eng.journal.data["diary"] else "")
    check("JOURNAL.md written", (d / "JOURNAL.md").is_file() and "未解答" in (d / "JOURNAL.md").read_text(encoding="utf-8"))
    check("dry-run end does not change the journal", _dry_run_untouched(eng))

    # 7. the question I'm chasing must survive the open-question cap (bug seen in live run #141)
    d, eng, world = make()
    j = eng.journal
    low = j.add_question("low priority thing?", "pickup:low", "pickup", 0.05)
    j.data["next_plan"] = {"focus_question": low}
    for i in range(40):
        j.add_question(f"thing {i}?", f"object:t{i}", "object", 0.8)
    check("focus question is never trimmed", low in j.data["questions"],
          f"open={sum(1 for q in j.data['questions'].values() if q['status'] != 'answered')}")
    d, eng, world = make()
    j = eng.journal
    novel = j.generate_from_run(run_rec("n", 60, world_deltas={"seen": [f"boss:B{i}" for i in range(8)] + ["object:new rock"]}), 9)
    check("at most 3 first-sighting questions per run, objects first",
          len([q for q in novel if j.data["questions"][q]["kind"] in ("boss", "object")]) <= 3
          and any(j.data["questions"][q]["about"] == "object:new rock" for q in novel))

    # 8. my own record says an object is clearly worth it -> go (no Jev needed); clearly bad -> skip
    log = []
    d, eng, world = make(fake_jev_factory(log))
    eng.kb["objects"]["object:shiny box"] = {"seen": 10, "tried": 8, "positive": 6, "died": 0,
                                             "sum": {"level": 8.0, "xp": 8.0, "hp": -0.4}, "hp_cost": 1.2, "time": 50}
    res = world.decide({"type": "object:shiny box", "hp_pct": 0.9, "explore": True})
    check("object my record says is worth it is engaged without asking Jev", res["engage"] and res["source"] == "experience_ev"
          and not log, str(res))
    res = world.decide({"type": "object:shiny box", "hp_pct": 0.4, "explore": True})
    check("...but not when my HP can't afford its usual cost", res["source"] != "experience_ev", str(res))

    # 9. hoarding: tickets piling up get used; after 3 saves in a row 'save' is not offered
    res = world.decide_meta({"current": {"dex": 1, "name": "A"}, "owned": [], "tickets": {"t1": 3, "t10": 1}})
    eng.journal.data["meta_seen"]["gacha_pulls"] = 1
    res = world.decide_meta({"current": {"dex": 1, "name": "A"}, "owned": [], "tickets": {"t1": 3, "t10": 1}})
    check("10+ pulls' worth of tickets get used", res["pull"] and res["pull_source"] == "hoard_limit", str(res))
    eng.journal.data["meta_seen"]["save_streak"] = 3
    log.clear()
    world.decide_upgrade({"coins": 999, "options": [{"id": "hp", "name": "Vida", "desc": "+HP", "level": 0, "max": 5, "cost": 50, "affordable": True}]})
    offered = list(log[-1][1]["upgrade"]["criteria"]) if log else []
    check("after 3 saves in a row, 'save' is no longer an option", "save" not in offered, str(offered))

    # 10. one run is not enough to answer "how do I stop X"
    d, eng, world = make(fake_jev_factory([]))
    j = eng.journal
    qid = j.add_question("How do I stop circle from hurting me so much?", "cause:mark:circle", "cause", 0.8)
    j.data["cause_stance"] = {"mark:circle": {"careful_kiter": [1, 0.2]}}
    v = j._apply_focus_verdict(qid, j.data["questions"][qid], "answered", ["x"], 1)
    check("one run cannot answer a 'how do I stop X' question", v == "keep_investigating" and j.data["questions"][qid]["status"] == "open")
    j.data["cause_stance"] = {"mark:circle": {"careful_kiter": [3, 0.45], "treasure_hunter": [3, 1.2]}}
    v = j._apply_focus_verdict(qid, j.data["questions"][qid], "answered", ["x"], 2)
    ans = j.data["beliefs"].get(j.data["questions"][qid]["answer"] or "", {}).get("claim", "")
    check("with enough runs the answer names the play style that helps", v == "answered" and "小心走位" in ans, ans)

    # 11. topics my runs cannot test sink (even after regeneration); cause questions rotate play styles
    d, eng, world = make()
    j = eng.journal
    rare = j.add_question("What does the rare pickup do for me?", "pickup:rare", "pickup", 0.7)
    fresh = j.add_question("What does the common pickup do for me?", "pickup:common", "pickup", 0.7)
    for i in range(6):
        j._apply_focus_verdict(rare, j.data["questions"][rare], "could_not_test", [], i)
    j.data["questions"][rare]["priority"] = 0.7  # isolate testability from the older priority decay
    ranked = [qid for qid, _ in j.open_questions(8)]
    check("a topic my runs keep failing to test sinks below a fresh one",
          ranked.index(fresh) < ranked.index(rare), str(ranked))
    del j.data["questions"][rare]
    rare2 = j.add_question("What does the rare pickup do for me?", "pickup:rare", "pickup", 0.7)
    check("regenerated question inherits its topic's untestable history",
          j._q_score(j.data["questions"][rare2]) < j._q_score(j.data["questions"][fresh]))
    cq = j.add_question("How do I stop dying to circle?", "cause:mark:circle", "cause", 0.95)
    j.data["cause_stance"] = {"mark:circle": {"careful_kiter": [9, 2.0], "aggressive_farmer": [3, 0.5]}}
    j.data["next_plan"] = None
    plan = j.plan_next(use_jev=False)
    check("cause question becomes a balanced play-style trial",
          plan["focus_question"] == cq and plan["stance"] in ("treasure_hunter", "boss_hunter") and plan.get("stance_trial"),
          str({k: plan.get(k) for k in ("stance", "stance_trial")}))

    # 12. build choices must show my measured record per build, and be sampled like other choices
    import json as _json
    captured = []
    def prob_jev(state, questions, timeout):
        captured.append(questions)
        out = {}
        for k, q in questions.items():
            keys = list(q["criteria"])
            if k == "build_goal":
                try_key = next((x for x in keys if x.startswith("try_")), keys[0])
                out[k] = {"choice": keys[0], "probabilities": {try_key: 1.0}}
            else:
                out[k] = {"choice": keys[0]}
        return {"answers": out}
    d, eng, world = make(prob_jev)
    j = eng.journal
    (d / "runs").mkdir(exist_ok=True)
    for i, (tt, pw, evo) in enumerate([(200, 900, 1), (400, 2100, 2)]):
        (d / "runs" / f"run_{i:04d}_b{i}.json").write_text(_json.dumps({
            "run_id": f"b{i}", "time": tt, "power_score": pw,
            "moves": [{"id": "gust", "evolved": True}] * evo,
            "trial": {"plan": {"build_id": "free"}},
            "picks": [{"key": "stat:power", "offered": ["stat:power", "new:agility"]}],
        }))
    plan = j.plan_next(use_jev=True)
    crit = captured[-1]["build_goal"]["criteria"] if captured else {}
    check("build menu shows my measured record for a tried build",
          "my record:" in crit.get("free", "") and "2 runs" in crit.get("free", ""), crit.get("free", ""))
    check("build choice is sampled from Jev probabilities, not argmax",
          str(plan.get("build_id", "")).startswith("try_"), str(plan.get("build_id")))

    # 13. every run record says which bot/bridge build produced it
    import glob as _glob
    d, eng, world = make()
    eng.start_run({"partner": "Dragonite", "client_run_id": "v1", "bot_build": "testbuild-1"})
    eng.end_run(run_rec("v1", 200))
    files = sorted(_glob.glob(str(d / "runs" / "run_*.json")))
    rec = _json.loads(Path(files[-1]).read_text(encoding="utf-8")) if files else {}
    trial = rec.get("trial") or {}
    bb = (trial.get("plan") or {}).get("bridge_build") or ""
    check("run record stamps the extension build", trial.get("bot_build") == "testbuild-1", str(trial.get("bot_build")))
    check("run record stamps the bridge build", len(bb) >= 6 and bb == eng.journal.plan_next(use_jev=False).get("bridge_build"), bb)

    print("\nALL PASS" if OK else "\nSOME FAILED")
    sys.exit(0 if OK else 1)


def _dry_run_untouched(eng):
    import json
    before = json.dumps(eng.journal.data, sort_keys=True, default=str)
    eng.end_run(run_rec("dry", 999, world_deltas={"seen": ["object:weird statue"]}), persist=False)
    return before == json.dumps(eng.journal.data, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
