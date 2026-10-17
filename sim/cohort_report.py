#!/usr/bin/env python3
"""
Cohort report: compare bot performance across code versions using ONLY the
bot's own run records (logs/runs) and journal. Runs are split by the build
stamps written into each record (trial.bot_build / trial.plan.bridge_build);
unstamped runs are the pre-stamp baseline.

    python3 sim/cohort_report.py [last_n_old]

Prints, per cohort: survival, power, evolutions, focus testability,
build/stance mix, seconds spent inside ground markings per minute,
and stuck events per run (where recorded).
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_runs() -> list[dict]:
    runs = []
    for f in sorted((ROOT / "logs" / "runs").glob("*.json")):
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    runs.sort(key=lambda r: r.get("run_number", 0))
    return runs


def stamp(r: dict) -> tuple:
    t = r.get("trial") or {}
    p = t.get("plan") or {}
    return t.get("bot_build"), (p.get("bridge_build") or t.get("bridge_build"))


def inside_per_min(r: dict, key: str):
    bins = (((r.get("world_deltas") or {}).get("danger") or {}).get(key) or [])
    if not bins or not r.get("time"):
        return None
    return bins[0][0] / (r["time"] / 60)


def show(label: str, rs: list[dict]) -> None:
    if not rs:
        print(f"\n{label}: n=0")
        return
    ts = [r["time"] for r in rs]
    pw = [r.get("power_score") or 0 for r in rs]
    evo = [sum(1 for m in (r.get("moves") or []) if m.get("evolved")) for r in rs]
    foc = Counter((r.get("reflection") or {}).get("focus_verdict") for r in rs)
    judged = sum(v for k, v in foc.items() if k in ("answered", "keep_investigating", "could_not_test"))
    cant = foc.get("could_not_test", 0)
    print(f"\n{label}: n={len(rs)} mean={statistics.mean(ts):.0f}s med={statistics.median(ts):.0f}s max={max(ts):.0f}s "
          f"powMean={statistics.mean(pw):.0f} evo/run={statistics.mean(evo):.2f} 2+evo={sum(1 for e in evo if e >= 2)} "
          f"reach5m={sum(1 for t in ts if t >= 300)}")
    print(f"  deaths: {Counter(r.get('death_cause') for r in rs).most_common(4)}")
    print(f"  could_not_test: {cant}/{judged} judged ({(cant / judged * 100 if judged else 0):.0f}%)")
    print(f"  builds: {dict(Counter((((r.get('trial') or {}).get('plan') or {}).get('build_id')) for r in rs))}")
    print(f"  stances: {dict(Counter((((r.get('trial') or {}).get('plan') or {}).get('stance')) for r in rs))}"
          f" | stance_trial runs: {sum(1 for r in rs if (((r.get('trial') or {}).get('plan') or {}).get('stance_trial')))}")
    for key in ("mark:circle", "mark:line"):
        vals = [v for r in rs if (v := inside_per_min(r, key)) is not None]
        if vals:
            print(f"  inside {key}: {statistics.mean(vals):.1f}s/min (median {statistics.median(vals):.1f}, n={len(vals)})")
    stk = [r["stuck_events"] for r in rs if r.get("stuck_events") is not None]
    if stk:
        print(f"  stuck_events/run: mean={statistics.mean(stk):.1f} median={statistics.median(stk)} max={max(stk)}")


def main() -> None:
    runs = load_runs()
    new = [r for r in runs if stamp(r)[1]]
    old = [r for r in runs if not stamp(r)[1]]
    print(f"total run files={len(runs)} last=#{runs[-1].get('run_number') if runs else '-'} | bridge-stamped={len(new)}")
    print("stamp values:", Counter(stamp(r) for r in runs if any(stamp(r))).most_common(6) or "none yet")
    n_old = int(sys.argv[1]) if len(sys.argv) > 1 else max(len(new), 30)
    show(f"OLD (last {n_old} unstamped)", old[-n_old:])
    show("NEW (bridge-stamped)", new)
    bots = sorted({stamp(r)[0] for r in new if stamp(r)[0]})
    for b in bots:
        show(f"NEW bot_build={b}", [r for r in new if stamp(r)[0] == b])


if __name__ == "__main__":
    main()
