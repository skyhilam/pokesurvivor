#!/usr/bin/env python3
"""
Auto-Iteration Observer (report-only)
=====================================
Runs after every run (via jev_bridge hook) or as a background watcher.

It used to be a hand-written rule engine ("3 deaths to circles -> telegraph_fear = 2.4",
a human list of "support buff" moves, forced card penalties). That is not how a player
learns, and it broke pure-learning mode. It now ONLY OBSERVES and reports:

- death-cause clusters over recent runs
- per-move measured damage / kills (no tiers, no penalties)
- performance trend

Decisions about what to do with these observations belong to Jev via the Player Journal
(bridge/journal.py), which turns repeated deaths into questions to investigate.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


class AutoIterSupervisor:
    def __init__(self, log_dir: Path = LOG_DIR) -> None:
        self.log_dir = Path(log_dir)
        self.kb_path = self.log_dir / "knowledge.json"
        self.report_path = self.log_dir / "AUTO_ITERATION.md"
        self.last_audited_run = 0

    def load_kb(self) -> dict[str, Any]:
        if not self.kb_path.exists():
            return {}
        try:
            return json.loads(self.kb_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Auto-Iter] Failed to load KB: {e}", file=sys.stderr)
            return {}

    def audit_and_tune(self, kb: Optional[dict[str, Any]] = None, engine_ref: Any = None) -> dict[str, Any]:
        """Observe only. Never modifies the knowledge base (name kept for compatibility)."""
        if kb is None:
            kb = self.load_kb()
        if not kb:
            return {"status": "no_kb", "interventions": []}
        runs = kb.get("runs", [])
        total_runs = int(kb.get("total_runs", 0))
        if not runs:
            return {"status": "no_runs", "total_runs": total_runs, "interventions": []}

        observations: list[str] = []
        recent = runs[-8:]
        causes: dict[str, int] = {}
        for r in recent:
            c = str(r.get("cause") or "unknown")
            causes[c] = causes.get(c, 0) + 1
        top = sorted(causes.items(), key=lambda kv: -kv[1])
        if top and top[0][1] >= 3:
            observations.append(f"{top[0][1]} of my last {len(recent)} runs ended by '{top[0][0]}'")

        moves = []
        for mid, st in (kb.get("move_stats") or {}).items():
            sec = float(st.get("combat_sec", 0))
            if sec >= 15:
                moves.append({"id": mid, "dps": round(float(st.get("dmg", 0)) / sec, 1), "kills": int(st.get("kills", 0)),
                              "combat_sec": round(sec, 1), "runs": int(st.get("runs", 0))})
        moves.sort(key=lambda m: -m["dps"])

        trend = {}
        if len(runs) >= 10:
            a, b = runs[-10:-5], runs[-5:]
            trend = {"prev5_avg_time": round(sum(r.get("time", 0) for r in a) / 5, 1),
                     "last5_avg_time": round(sum(r.get("time", 0) for r in b) / 5, 1)}

        diag = {"audited_run": total_runs, "timestamp": time.time(), "status": "OBSERVED",
                "observations": observations, "interventions": [], "death_causes": top, "moves": moves, "trend": trend}
        self.last_audited_run = total_runs
        self._generate_report(kb, diag)
        return diag

    def _generate_report(self, kb: dict[str, Any], diag: dict[str, Any]) -> None:
        best = float(kb.get("best_time", 0.0))
        L = ["# PokéSurvivor Observer Report (report-only)", "",
             "> 呢個報告只係觀察，唔會再自動改參數。點應對由 Jev 透過玩家日記決定（見 `JOURNAL.md`）。", "",
             f"- Runs recorded: **{len(kb.get('runs', []))}** (counter {kb.get('total_runs', 0)})",
             f"- Best time: **{int(best) // 60:02d}:{int(best) % 60:02d}** | Best power: **{float(kb.get('best_score', 0)):.0f}**",
             f"- Audited: `{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(diag['timestamp']))}`", "",
             "## Observations", ""]
        L += [f"- 👀 {o}" for o in diag["observations"]] or ["- nothing unusual"]
        L += ["", "## Recent death causes", "", "| Cause | Runs (last 8) |", "|---|---|"]
        L += [f"| `{c}` | {n} |" for c, n in diag["death_causes"]]
        if diag["trend"]:
            L += ["", f"Trend: avg survival {diag['trend']['prev5_avg_time']}s → {diag['trend']['last5_avg_time']}s"]
        if diag["moves"]:
            L += ["", "## Measured move output (no tiers, no penalties)", "",
                  "| Move | Damage/s while fighting | Kills | Combat time | Runs |", "|---|---|---|---|---|"]
            L += [f"| `{m['id']}` | {m['dps']} | {m['kills']} | {m['combat_sec']}s | {m['runs']} |" for m in diag["moves"]]
            L += ["", "> Buff moves (attack/speed/defence) deal no direct damage; their value shows up in the run results, not here."]
        try:
            self.report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"[Auto-Iter] Failed to write report: {e}", file=sys.stderr)


def run_watchdog_loop(poll_interval: float = 12.0) -> None:
    sup = AutoIterSupervisor()
    print(f"[Auto-Iter Observer] watching {LOG_DIR} every {poll_interval}s...")
    last = 0
    while True:
        try:
            kb = sup.load_kb()
            cur = int(kb.get("total_runs", 0)) if kb else 0
            if cur > last:
                res = sup.audit_and_tune(kb)
                last = cur
                print(f"[Auto-Iter Observer] run #{cur}: {len(res.get('observations', []))} observations")
        except Exception as e:
            print(f"[Auto-Iter Observer] error: {e}", file=sys.stderr)
        time.sleep(poll_interval)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        run_watchdog_loop()
    else:
        print(json.dumps(AutoIterSupervisor().audit_and_tune(), indent=2, ensure_ascii=False))
