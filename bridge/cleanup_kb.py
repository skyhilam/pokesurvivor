#!/usr/bin/env python3
"""
One-time knowledge-base cleanup
===============================
1. Back up logs/knowledge.json, logs/journal.json and logs/runs/ to archive/kb_backup_<ts>/
2. Find duplicate run records (same run_id, or identical time/level/kills/partner) - these were
   re-sent by the extension's offline queue and counted twice.
3. Subtract the duplicates' world observations (danger / pickups / kills / terrain) that were merged twice.
4. Remove duplicate run files + entries, recompute partner stats.
5. Remove lessons written by the old rule-based auto-iteration, reset its forced knob values,
   and rebuild card/move/best statistics from the remaining run files.

    python3 bridge/cleanup_kb.py           # dry run: only report
    python3 bridge/cleanup_kb.py --apply   # do it
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning import DEFAULT_POLICY, LearningEngine  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"


def subtract_deltas(kb: dict, wd: dict) -> None:
    for key, bins in (wd.get("danger") or {}).items():
        cur = kb.get("danger", {}).get(key)
        if not cur:
            continue
        for i, (exp_s, dmg) in enumerate(bins[:len(cur)]):
            cur[i][0] = round(max(0.0, cur[i][0] - float(exp_s)), 2)
            cur[i][1] = round(max(0.0, cur[i][1] - float(dmg)), 4)
    for table in ("pickups", "kills"):
        for key, obs in (wd.get(table) or {}).items():
            cur = kb.get(table, {}).get(key)
            if not cur:
                continue
            cur["n"] = max(0, int(cur.get("n", 0)) - int(obs.get("n") or 0))
            for rk, v in (obs.get("sum") or {}).items():
                cur["sum"][rk] = round(cur["sum"].get(rk, 0.0) - float(v or 0), 4)
    terr = kb.get("terrain") or {}
    for k, v in (wd.get("terrain") or {}).items():
        if k in terr:
            terr[k] = round(max(0.0, terr[k] - float(v or 0)), 2)


def main() -> None:
    apply = "--apply" in sys.argv
    eng = LearningEngine(LOG_DIR)
    kb = eng.kb
    files = sorted(eng.runs_dir.glob("run_*.json"))
    seen_ids, seen_sig, dups = set(), set(), []
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rid, sig = d.get("run_id"), LearningEngine.run_signature(d)
        if (rid and rid in seen_ids) or sig in seen_sig:
            dups.append((p, d))
            continue
        if rid:
            seen_ids.add(rid)
        seen_sig.add(sig)
    kept_runs, sigs = [], set()
    for r in kb.get("runs", []):
        s = LearningEngine.run_signature(r)
        if s in sigs:
            continue
        sigs.add(s)
        kept_runs.append(r)
    auto_lessons = [k for k in kb.get("lessons", {}) if k.startswith("auto_iter:")]

    print(f"run files: {len(files)} | duplicate files: {len(dups)}")
    print(f"kb run entries: {len(kb.get('runs', []))} -> {len(kept_runs)} unique")
    print(f"auto-iteration lessons to remove: {len(auto_lessons)}")
    for p, d in dups[:50]:
        print(f"  dup: {p.name}  t={d.get('time')} lv={d.get('level')} kills={d.get('kills')}")
    if not apply:
        print("\n(dry run) re-run with --apply to clean up")
        return

    backup = ROOT / "archive" / f"kb_backup_{time.strftime('%Y%m%d_%H%M%S')}"
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("knowledge.json", "journal.json"):
        if (LOG_DIR / name).is_file():
            shutil.copy2(LOG_DIR / name, backup / name)
    shutil.copytree(eng.runs_dir, backup / "runs")
    print(f"backup written to {backup}")

    for p, d in dups:
        subtract_deltas(kb, d.get("world_deltas") or {})
        p.unlink()
    kb["runs"] = kept_runs
    parts: dict[str, dict] = {}
    for r in kept_runs:
        name = r.get("partner")
        if not name:
            continue
        pp = parts.setdefault(name, {"runs": 0, "time_sum": 0.0, "best": 0.0})
        pp["runs"] += 1
        pp["time_sum"] = round(pp["time_sum"] + float(r.get("time") or 0), 1)
        pp["best"] = max(pp["best"], round(float(r.get("time") or 0), 1))
    kb["partners"] = parts
    for k in auto_lessons:
        del kb["lessons"][k]
    kb["best_policy"] = dict(DEFAULT_POLICY)
    kb["best_score"], kb["best_time"], kb["best_level"] = 0.0, 0.0, 0
    eng.rebuild_stats_from_runs()
    kb["cleanup"] = {"ts": time.time(), "duplicates_removed": len(dups), "backup": str(backup)}
    eng.save()
    print(f"done: {len(kept_runs)} unique runs, best {kb['best_time']}s Lv{kb['best_level']} score {kb['best_score']}")


if __name__ == "__main__":
    main()
