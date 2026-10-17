#!/usr/bin/env python3
"""
Does Jev itself learn a good policy? (synthetic game, hidden optimum)
=====================================================================
Survival depends on hidden "right" knob values that nobody tells Jev.
Each run Jev only sees its own history table (policy -> survival, what hurt most)
and picks the next policy.  We check the learning curve.

  python3 bridge/test_learning.py          # offline fallback (random tweaks), fast
  python3 bridge/test_learning.py --jev    # real TypeSafe Jev chooses every policy (~1 min)
"""
import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning import LearningEngine  # noqa: E402

HIDDEN = {"fear": 2.2, "keep_distance": 45, "greed": 0.6, "telegraph_fear": 1.8}
SCALE = {"fear": 1.5, "keep_distance": 50, "greed": 1.0, "telegraph_fear": 1.5}


def fake_game(pol):
    err = {k: (pol[k] - HIDDEN[k]) / SCALE[k] for k in HIDDEN}
    t = 1700 * math.exp(-1.2 * sum(e * e for e in err.values())) + random.gauss(0, 50)
    # what "hurt most" is observable and correlates with the worst knob (like real damage sources)
    worst = max(err, key=lambda k: abs(err[k]))
    cause = {"fear": "enemy:Rattata", "keep_distance": "enemy:Rattata", "greed": "enemy:Zubat", "telegraph_fear": "mark:circle"}[worst]
    if worst == "greed" and err["greed"] < 0:
        cause = "too_weak (few pickups)"
    return max(20.0, t), cause


def main():
    use_jev = "--jev" in sys.argv
    jev = None
    if use_jev:
        import jev_bridge as jb  # uses ~/.config/typesafe/api_key
        jev = lambda s, q, t: jb.call_typesafe_jev(s, q, timeout=t)
    random.seed(3)
    n_runs = 14 if use_jev else 60
    with tempfile.TemporaryDirectory() as d:
        eng = LearningEngine(Path(d), jev=jev)
        times = []
        for i in range(n_runs):
            s = eng.start_run({"partner": "test"})
            t, cause = fake_game(s["policy"])
            times.append(t)
            eng.end_run({"run_id": s["run_id"], "time": t, "level": int(t / 40), "death_cause": cause,
                         "damage_sources": {cause: 100}, "picks": [], "timeline": []})
            if use_jev:
                p = s["policy"]
                print(f"run {i + 1:2d}: {t:6.0f}s  fear {p['fear']:.2f} dist {p['keep_distance']:.0f} greed {p['greed']:.2f} "
                      f"tele {p['telegraph_fear']:.2f}  | {s['experiment'][:70]}")
        k = max(3, n_runs // 4)
        first, last = sum(times[:k]) / k, sum(times[-k:]) / k
        print(f"\navg survival first {k} runs: {first:.0f}s -> last {k} runs: {last:.0f}s  (best {max(times):.0f}s)")
        print("best policy found:", {x: eng.kb['best_policy'][x] for x in HIDDEN}, "| hidden optimum:", HIDDEN)
        assert last > first, "no improvement"
        print("OK: policy improved from its own history")


if __name__ == "__main__":
    main()
