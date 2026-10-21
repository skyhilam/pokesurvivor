#!/usr/bin/env python3
"""Render one run as an [ACTION]/[OBSERVATION]/[ERROR] trajectory.

Input is the run record the extension posts to /run/end (as saved under
logs/runs/run_XXXX_<id>.json) plus the lines of logs/jev_decisions.jsonl that
belong to that run. Nothing here knows game rules: every line is copied from
what the bot itself saw, picked, or was told by the bridge.

    python3 bridge/trajectory.py logs/runs/run_0001_abc.json [logs/jev_decisions.jsonl]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

MIN_CHARS = 10_000
DEFAULT_MAX_CHARS = 20_000
TRUNCATE_HEAD_SHARE = 0.6

_RUN_END_EVENTS = ("run_end", "run_end_duplicate")


def _mmss(sec: Any) -> str:
    try:
        s = max(0, int(float(sec or 0)))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60:02d}:{s % 60:02d}"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _text(m: Any) -> str:
    if isinstance(m, dict):
        return str(m.get("text") or m.get("msg") or "").strip()
    return str(m or "").strip()


def read_decisions(path: str | Path) -> list[dict[str, Any]]:
    """All JSON lines of a jev_decisions.jsonl file (bad lines are skipped)."""
    out: list[dict[str, Any]] = []
    p = Path(path)
    if not p.is_file():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
    return out


def decisions_for_run(entries: Iterable[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """Lines between the run_start carrying run_id and the next run end / run start."""
    run_id = str(run_id or "")
    if not run_id:
        return []
    collected: list[dict[str, Any]] = []
    inside = False
    for entry in entries:
        event = str(entry.get("event") or "")
        if event == "run_start":
            if inside:
                break
            inside = str(entry.get("run_id") or "") == run_id
            if inside:
                collected.append(entry)
            continue
        if not inside:
            continue
        collected.append(entry)
        if event in _RUN_END_EVENTS:
            break
    return collected


def summarize_run(rec: dict[str, Any]) -> dict[str, Any]:
    """Short numbers for a choice criterion: survived, level, kills, cause, worst damage sources."""
    dmg = rec.get("damage_sources") or {}
    if not isinstance(dmg, dict):
        dmg = {}
    top = sorted(dmg.items(), key=lambda kv: -_num(kv[1]))[:3]
    tl = rec.get("timeline") or []
    kills = rec.get("kills")
    if kills is None and tl:
        kills = tl[-1].get("kills", 0)
    return {
        "run_id": rec.get("run_id"),
        "survived": _mmss(rec.get("time")),
        "survived_s": round(_num(rec.get("time")), 1),
        "level": rec.get("level"),
        "kills": int(_num(kills)),
        "boss_kills": int(_num(rec.get("boss_kills"))),
        "partner": rec.get("partner"),
        "death_cause": rec.get("death_cause") or "unknown",
        "hurt_most_by": [f"{k} ({_num(v):.0f})" for k, v in top],
        "picks": len(rec.get("picks") or []),
        "items": len(rec.get("items") or []),
    }


def summary_line(rec: dict[str, Any]) -> str:
    s = summarize_run(rec)
    hurt = ", ".join(s["hurt_most_by"]) or "n/a"
    return (f"survived {s['survived']} Lv{s['level']} {s['kills']} kills"
            f"{(' ' + str(s['boss_kills']) + ' boss') if s['boss_kills'] else ''}, "
            f"died to {s['death_cause']}; hurt most by {hurt}; {s['picks']} cards picked")


def _decision_lines(decisions: list[dict[str, Any]]) -> list[tuple[float, int, str]]:
    lines: list[tuple[float, int, str]] = []
    for i, d in enumerate(decisions):
        event = str(d.get("event") or "")
        t = _num(d.get("time", d.get("t")))
        dec = d.get("decision") if isinstance(d.get("decision"), dict) else {}
        src = str(dec.get("source") or d.get("source") or "")
        if event == "run_start":
            lines.append((t, i, f"[ACTION] run start #{d.get('run_number')} policy={d.get('experiment')} harness={d.get('harness_id') or 'n/a'}"))
        elif event == "levelup":
            what = dec.get("card") if dec.get("action") == "pick" else dec.get("action")
            offered = d.get("cards") or []
            lines.append((t, i, f"[ACTION] t={_mmss(t)} levelup: {dec.get('action')} '{what}' ({src}); offered {offered}"))
        elif event == "item":
            lines.append((t, i, f"[ACTION] t={_mmss(t)} item: {dec.get('action')} (current {dec.get('current')}, offered {dec.get('offered')}) ({src})"))
        elif event == "bond":
            lines.append((t, i, f"[ACTION] t={_mmss(t)} bond: index {dec.get('index')} ({src})"))
        elif event == "checkpoint":
            lines.append((t, i, f"[ACTION] t={_mmss(t)} checkpoint: posture {d.get('posture')} ({d.get('source')})"))
        elif event == "opportunity":
            lines.append((t, i, f"[ACTION] t={_mmss(t)} opportunity {d.get('type')}: {'engage' if d.get('engage') else 'skip'} ({d.get('source')}) {d.get('reason') or ''}".rstrip()))
        elif event == "opportunity_outcome":
            lines.append((t, i, f"[OBSERVATION] t={_mmss(t)} outcome of {d.get('type')}: {d.get('why') or d.get('evidence') or ''} reward={d.get('reward')}"))
        elif event == "telemetry":
            data = d.get("data") or {}
            if isinstance(data, dict) and data.get("error"):
                lines.append((t, i, f"[ERROR] t={_mmss(t)} telemetry: {data.get('error')}"))
        elif event in _RUN_END_EVENTS:
            continue
        else:
            continue
        if src in ("bridge_error", "error_no_cards") or dec.get("error") or d.get("error"):
            lines.append((t, i, f"[ERROR] t={_mmss(t)} {event}: {dec.get('error') or d.get('error') or src}"))
    return lines


def render_trajectory(rec: dict[str, Any], decisions: list[dict[str, Any]] | None = None,
                      max_chars: int = DEFAULT_MAX_CHARS, harness_id: str | None = None) -> str:
    """One text block, oldest first, truncated in the middle to at most max_chars (never below MIN_CHARS)."""
    max_chars = max(MIN_CHARS, int(max_chars))
    decisions = decisions or []
    s = summarize_run(rec)
    plan = ((rec.get("trial") or {}).get("plan") or {}) if isinstance(rec.get("trial"), dict) else {}
    head = [
        f"# run {s['run_id'] or '?'} | partner {s['partner'] or '?'} | harness {harness_id or rec.get('harness_id') or 'n/a'}",
    ]
    if plan:
        head.append(f"[ACTION] plan: style={plan.get('stance')} build={plan.get('build_id')} question={plan.get('question_text')}")
    elif (rec.get("trial") or {}).get("why"):
        head.append(f"[ACTION] plan: {rec['trial'].get('why')}")

    events: list[tuple[float, int, str]] = []
    order = 0

    def add(t: float, line: str) -> None:
        nonlocal order
        order += 1
        events.append((t, order, line))

    for pt in rec.get("timeline") or []:
        if not isinstance(pt, dict):
            continue
        t = _num(pt.get("t"))
        hp = pt.get("hp_pct")
        hp_txt = f" hp {_num(hp) * 100:.0f}%" if hp is not None else ""
        add(t, f"[OBSERVATION] t={_mmss(t)} level {pt.get('level')} kills {pt.get('kills', 0)} enemies {pt.get('enemies', '?')}{hp_txt}")
    for p in rec.get("picks") or []:
        if not isinstance(p, dict):
            continue
        t = _num(p.get("t"))
        add(t, f"[ACTION] t={_mmss(t)} picked card '{p.get('title') or p.get('key')}' from {p.get('offered') or []}")
    for it in rec.get("items") or []:
        if isinstance(it, dict):
            t = _num(it.get("t"))
            add(t, f"[ACTION] t={_mmss(t)} item: {it.get('action') or 'took'} '{it.get('title') or it.get('id')}'")
        else:
            add(0.0, f"[ACTION] item: {it}")
    for m in rec.get("messages") or []:
        t = _num(m.get("t")) if isinstance(m, dict) else 0.0
        txt = _text(m)
        if txt:
            add(t, f"[OBSERVATION] t={_mmss(t)} screen said \"{txt[:120]}\"")
    for o in rec.get("opportunities") or []:
        if not isinstance(o, dict):
            continue
        t = _num(o.get("t"))
        add(t, f"[ACTION] t={_mmss(t)} went to {o.get('type')}")
        tag = "[ERROR]" if o.get("died") else "[OBSERVATION]"
        add(t + 0.001, f"{tag} t={_mmss(t)} {o.get('type')}: {o.get('why') or 'no result'}; reward {o.get('reward') or {}}; "
                       f"hp cost {_num(o.get('hp_cost')) * 100:.0f}%{'; died' if o.get('died') else ''}")
    for b in rec.get("bosses_defeated") or []:
        add(_num(rec.get("time")), f"[OBSERVATION] defeated boss {b}")
    for t, i, line in _decision_lines(decisions):
        events.append((t, 10_000 + i, line))

    events.sort(key=lambda e: (e[0], e[1]))
    body = [line for _, _, line in events]

    tail = []
    for m in rec.get("moves") or []:
        if isinstance(m, dict) and m.get("id"):
            tail.append(f"[OBSERVATION] move {m.get('id')}: {int(_num(m.get('kills')))} kills, dmg {_num(m.get('dmg')):.0f} over {_num(m.get('combat_sec')):.0f}s fighting")
    dmg = rec.get("damage_sources") or {}
    if isinstance(dmg, dict) and dmg:
        parts = ", ".join(f"{k} {_num(v):.0f}" for k, v in sorted(dmg.items(), key=lambda kv: -_num(kv[1]))[:8])
        tail.append(f"[OBSERVATION] damage taken by source: {parts}")
    tail.append(f"[ERROR] died at {s['survived']} to {s['death_cause']} (Lv{s['level']}, {s['kills']} kills)")
    tail.append(f"[OBSERVATION] summary: {summary_line(rec)}")

    text = "\n".join(head + body + tail)
    if len(text) <= max_chars:
        return text
    marker_len = 64
    keep = max_chars - marker_len
    head_n = int(keep * TRUNCATE_HEAD_SHARE)
    tail_n = keep - head_n
    cut = len(text) - head_n - tail_n
    marker = f"\n... [truncated {cut} chars] ...\n"
    out = text[:head_n] + marker + text[-tail_n:]
    return out[:max_chars] if len(out) > max_chars else out


def load_run_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rec = load_run_file(argv[1])
    decisions: list[dict[str, Any]] = []
    if len(argv) >= 3:
        decisions = decisions_for_run(read_decisions(argv[2]), str(rec.get("run_id") or ""))
    print(render_trajectory(rec, decisions))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
