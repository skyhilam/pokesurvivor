#!/usr/bin/env python3
"""Sequential Propose -> Probe -> Compose loop over the harness component tree.

One variable at a time:

- Propose: a fixed edit grammar produces candidates from the current harness
  (general: memory counts, which memories are injected, Jev timeouts, the
  guardrails switch; decision: level-up shortcuts, fallback weights). Every
  candidate passes harness_tree.validate_tree before it can be offered. Jev
  picks one with a choice question whose criteria carry the trajectory summary
  and the library's lessons; without Jev a deterministic fallback picks the
  least-tried edit the library does not oppose.
- Probe: the candidate tree is written atomically to logs/harness/candidates/<id>/
  and used for the next run. Server-side, run_id -> harness id + fingerprint is
  remembered and returned by /run/start.
- Compose: after HARNESS_K candidates have enough runs each, they are compared
  with the parent harness's recent runs (survival seconds, power score, death
  cause). Better ones are adopted (non-conflicting adopted edits are merged),
  worse ones rolled back. Every 4th run re-tests the current harness.

The library (logs/harness/library.json + logs/HARNESS.md) keeps every adopted
harness and evidence-backed lessons per edit. This file does not import harness2.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from harness_tree import HarnessTree, resolve_harness_dir, validate_tree
from learning import compute_power_score
from trajectory import decisions_for_run, read_decisions, render_trajectory, summarize_run, summary_line

BASE_ID = "base"
RETEST_EVERY = 4
RECENT_N = 4
MARGIN = 0.02
K_STEP = 2
K_MAX = 20
K_RESTORE = 4
TIMEOUT_MIN = 1.0
TIMEOUT_MAX = 20.0
OFFER_MAX = 12
HISTORY_MAX = 200

K_FIELDS = ("lessons", "beliefs", "card_rules", "world_brief", "recent_runs", "history", "last_diary", "learned_cap")
SOURCE_FIELDS = {
    "top_lessons": ("lessons",),
    "top_beliefs": ("beliefs",),
    "card_rules": ("card_rules",),
    "world_brief": ("world_brief",),
    "runs_tail": ("recent_runs",),
    "history_table": ("recent_runs", "history"),
    "diary_tail": ("last_diary",),
}
TIMEOUT_FACTORS = {"x0.75": 0.75, "x1.5": 1.5}
WEIGHT_FACTORS = {"+25%": 1.25, "-25%": 0.75}


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _parse(files: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, text in files.items():
        if name.endswith(".json"):
            try:
                out[name] = json.loads(text)
            except json.JSONDecodeError:
                out[name] = None
        else:
            out[name] = text
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _mode(xs: list[str]) -> str:
    if not xs:
        return "n/a"
    return max(sorted(set(xs)), key=xs.count)


# ---------------------------------------------------------------- edit grammar
class Edit:
    """One modification from the fixed grammar. id is the stable key used in the library."""

    __slots__ = ("id", "kind", "target", "desc")

    def __init__(self, edit_id: str, kind: str, target: str, desc: str) -> None:
        self.id = edit_id
        self.kind = kind
        self.target = target
        self.desc = desc

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "target": self.target, "desc": self.desc}


def _sync_inject(decision: dict[str, Any], field: str, value: int) -> None:
    inject = decision.get("inject")
    if not isinstance(inject, dict):
        return
    for entry in inject.values():
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("source") or "")
        if src == "extra_context_plus_lessons":
            if field in ("lessons", "world_brief"):
                entry[field] = value
            elif field == "learned_cap":
                entry["cap"] = value
            continue
        if field in SOURCE_FIELDS.get(src, ()) and "k" in entry:
            entry["k"] = value


def enumerate_edits(files: dict[str, str], base_files: Optional[dict[str, str]] = None) -> list[Edit]:
    """Every grammar edit that is applicable to this tree (no-ops are not listed)."""
    data = _parse(files)
    base = _parse(base_files) if base_files else {}
    edits: list[Edit] = []

    memory = data.get("memory.json") or {}
    base_memory = base.get("memory.json") or {}
    shared_k = ((memory.get("shared") or {}).get("extra_context") or {}).get("k")
    if isinstance(shared_k, int):
        target = "memory.shared.extra_context"
        if shared_k + K_STEP <= K_MAX:
            edits.append(Edit(f"memory.k:shared.extra_context:+{K_STEP}", "general", target,
                              f"remember {K_STEP} more world-brief lines in every question ({shared_k} -> {shared_k + K_STEP})"))
        if shared_k - K_STEP >= 1:
            edits.append(Edit(f"memory.k:shared.extra_context:-{K_STEP}", "general", target,
                              f"remember {K_STEP} fewer world-brief lines in every question ({shared_k} -> {shared_k - K_STEP})"))
    for decision, spec in sorted((memory.get("decisions") or {}).items()):
        if not isinstance(spec, dict):
            continue
        for field in K_FIELDS:
            value = spec.get(field)
            if not isinstance(value, int):
                continue
            target = f"memory.{decision}.{field}"
            if value > 0:
                if value + K_STEP <= K_MAX:
                    edits.append(Edit(f"memory.k:{decision}.{field}:+{K_STEP}", "general", target,
                                      f"inject {K_STEP} more {field} into '{decision}' ({value} -> {value + K_STEP})"))
                if value - K_STEP >= 1:
                    edits.append(Edit(f"memory.k:{decision}.{field}:-{K_STEP}", "general", target,
                                      f"inject {K_STEP} fewer {field} into '{decision}' ({value} -> {value - K_STEP})"))
                edits.append(Edit(f"memory.inject:{decision}.{field}:off", "general", target,
                                  f"stop injecting {field} into '{decision}' ({value} -> 0)"))
            else:
                restore = ((base_memory.get("decisions") or {}).get(decision) or {}).get(field)
                restore = restore if isinstance(restore, int) and restore > 0 else K_RESTORE
                edits.append(Edit(f"memory.inject:{decision}.{field}:on", "general", target,
                                  f"inject {field} into '{decision}' again (0 -> {restore})"))

    plugins = data.get("plugins.json") or {}
    for name, value in sorted((plugins.get("jev_timeout_s") or {}).items()):
        if not isinstance(value, (int, float)):
            continue
        for label, factor in TIMEOUT_FACTORS.items():
            new = round(min(TIMEOUT_MAX, max(TIMEOUT_MIN, float(value) * factor)), 2)
            if abs(new - float(value)) < 1e-9:
                continue
            edits.append(Edit(f"timeout:{name}:{label}", "general", f"plugins.{name}",
                              f"wait {'longer' if factor > 1 else 'less'} for Jev on '{name}' ({value}s -> {new}s)"))

    harness = data.get("harness.json") or {}
    inject = harness.get("inject") if isinstance(harness.get("inject"), dict) else {}
    guard = bool(inject.get("guardrails"))
    edits.append(Edit(f"guardrails:{'off' if guard else 'on'}", "general", "harness.guardrails",
                      "stop injecting the guardrails text into every Jev question" if guard
                      else "inject the guardrails text into every Jev question"))

    skills = data.get("skills.json") or {}
    levelup = skills.get("handle_levelup") if isinstance(skills.get("handle_levelup"), dict) else {}
    for name, spec in sorted((levelup.get("shortcuts") or {}).items()):
        if not isinstance(spec, dict):
            continue
        enabled = bool(spec.get("enabled"))
        edits.append(Edit(f"skills.shortcut:{name}:{'off' if enabled else 'on'}", "decision", f"skills.shortcut.{name}",
                          f"{'disable' if enabled else 'enable'} the level-up shortcut '{name}'"))
    weights = (levelup.get("fallback_score") or {}).get("weights")
    if isinstance(weights, list):
        for item in weights:
            if not isinstance(item, dict) or "id" not in item:
                continue
            value = item.get("value")
            if not isinstance(value, (int, float)) or value == 0:
                continue
            for label, factor in WEIGHT_FACTORS.items():
                edits.append(Edit(f"skills.weight:{item['id']}:{label}", "decision", f"skills.weight.{item['id']}",
                                  f"fallback card score weight '{item['id']}' {label} ({value} -> {round(float(value) * factor, 1)})"))
    return edits


def apply_edit(edit_id: str, files: dict[str, str], base_files: Optional[dict[str, str]] = None) -> Optional[dict[str, str]]:
    """New file map with the edit applied, or None when it is not applicable to this tree."""
    data = _parse(files)
    base = _parse(base_files) if base_files else {}
    out = dict(files)
    kind, _, rest = edit_id.partition(":")

    if kind in ("memory.k", "memory.inject"):
        memory = copy.deepcopy(data.get("memory.json"))
        if not isinstance(memory, dict):
            return None
        path, _, arg = rest.rpartition(":")
        if path == "shared.extra_context":
            node = (memory.get("shared") or {}).get("extra_context")
            if not isinstance(node, dict) or not isinstance(node.get("k"), int) or kind != "memory.k":
                return None
            try:
                new = node["k"] + int(arg)
            except ValueError:
                return None
            if new < 1 or new > K_MAX:
                return None
            node["k"] = new
        else:
            decision, _, field = path.partition(".")
            spec = (memory.get("decisions") or {}).get(decision)
            if not isinstance(spec, dict) or field not in K_FIELDS or not isinstance(spec.get(field), int):
                return None
            cur = spec[field]
            if kind == "memory.k":
                try:
                    new = cur + int(arg)
                except ValueError:
                    return None
                if cur <= 0 or new < 1 or new > K_MAX:
                    return None
            elif arg == "off":
                if cur <= 0:
                    return None
                new = 0
            elif arg == "on":
                if cur > 0:
                    return None
                restore = ((base.get("memory.json") or {}).get("decisions") or {}).get(decision, {}).get(field)
                new = restore if isinstance(restore, int) and restore > 0 else K_RESTORE
            else:
                return None
            spec[field] = new
            _sync_inject(spec, field, new)
        out["memory.json"] = _dump(memory)
        return out

    if kind == "timeout":
        plugins = copy.deepcopy(data.get("plugins.json"))
        name, _, label = rest.rpartition(":")
        factor = TIMEOUT_FACTORS.get(label)
        if not isinstance(plugins, dict) or factor is None:
            return None
        timeouts = plugins.get("jev_timeout_s")
        if not isinstance(timeouts, dict) or not isinstance(timeouts.get(name), (int, float)):
            return None
        new = round(min(TIMEOUT_MAX, max(TIMEOUT_MIN, float(timeouts[name]) * factor)), 2)
        if abs(new - float(timeouts[name])) < 1e-9:
            return None
        timeouts[name] = new
        out["plugins.json"] = _dump(plugins)
        return out

    if kind == "guardrails":
        harness = copy.deepcopy(data.get("harness.json"))
        if not isinstance(harness, dict) or rest not in ("on", "off"):
            return None
        inject = harness.setdefault("inject", {})
        if not isinstance(inject, dict):
            return None
        want = rest == "on"
        if bool(inject.get("guardrails")) == want:
            return None
        inject["guardrails"] = want
        out["harness.json"] = _dump(harness)
        guard = files.get("guardrails.md", "")
        lines = guard.split("\n")
        if lines and lines[0].startswith("inject:"):
            lines[0] = f"inject: {'true' if want else 'false'}"
            out["guardrails.md"] = "\n".join(lines)
        return out

    if kind in ("skills.shortcut", "skills.weight"):
        skills = copy.deepcopy(data.get("skills.json"))
        if not isinstance(skills, dict):
            return None
        levelup = skills.get("handle_levelup")
        if not isinstance(levelup, dict):
            return None
        name, _, arg = rest.rpartition(":")
        if kind == "skills.shortcut":
            spec = (levelup.get("shortcuts") or {}).get(name)
            if not isinstance(spec, dict) or arg not in ("on", "off"):
                return None
            want = arg == "on"
            if bool(spec.get("enabled")) == want:
                return None
            spec["enabled"] = want
        else:
            factor = WEIGHT_FACTORS.get(arg)
            weights = (levelup.get("fallback_score") or {}).get("weights")
            if factor is None or not isinstance(weights, list):
                return None
            item = next((w for w in weights if isinstance(w, dict) and w.get("id") == name), None)
            if item is None or not isinstance(item.get("value"), (int, float)) or item["value"] == 0:
                return None
            item["value"] = round(float(item["value"]) * factor, 1)
        out["skills.json"] = _dump(skills)
        return out

    return None


def edit_target(edit_id: str) -> str:
    kind, _, rest = edit_id.partition(":")
    if kind in ("memory.k", "memory.inject"):
        return "memory." + rest.rpartition(":")[0]
    if kind == "timeout":
        return "plugins." + rest.rpartition(":")[0]
    if kind == "guardrails":
        return "harness.guardrails"
    if kind in ("skills.shortcut", "skills.weight"):
        return kind + "." + rest.rpartition(":")[0]
    return edit_id


# ---------------------------------------------------------------- library
def default_library() -> dict[str, Any]:
    return {
        "version": 1,
        "current": BASE_ID,
        "next": BASE_ID,
        "seq": {"c": 0, "h": 0},
        "runs_seen": 0,
        "harnesses": {},
        "candidates": {},
        "round": {"parent": BASE_ID, "candidates": []},
        "assignments": {},
        "lessons": {},
        "history": [],
    }


class HarnessRecursion:
    def __init__(self, log_dir: Path, engine: Any, enabled: Optional[bool] = None, k: Optional[int] = None,
                 samples: Optional[int] = None, retest_every: int = RETEST_EVERY, recent_n: int = RECENT_N,
                 base_dir: Optional[Path] = None, now: Callable[[], float] = time.time,
                 offer_max: int = OFFER_MAX) -> None:
        self.log_dir = Path(log_dir)
        self.engine = engine
        if enabled is None:
            enabled = os.environ.get("JEV_HARNESS_RECURSION", "1").strip() != "0"
        self.enabled = bool(enabled)
        self.k = max(1, int(os.environ.get("HARNESS_K", "1") if k is None else k))
        self.samples = max(1, int(os.environ.get("HARNESS_SAMPLES", "1") if samples is None else samples))
        self.retest_every = max(2, int(retest_every))
        self.recent_n = max(1, int(recent_n))
        self.offer_max = max(2, int(offer_max))
        self.base_dir = Path(base_dir) if base_dir is not None else resolve_harness_dir()
        self.dir = self.log_dir / "harness"
        self.candidates_dir = self.dir / "candidates"
        self.adopted_dir = self.dir / "adopted"
        self.lib_path = self.dir / "library.json"
        self.md_path = self.log_dir / "HARNESS.md"
        self.decision_log = self.log_dir / "jev_decisions.jsonl"
        self.lock = threading.Lock()
        self.now = now
        self.lib = self._load()
        self.active_id = self.lib.get("current") or BASE_ID
        self._base_files: Optional[dict[str, str]] = None
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.candidates_dir.mkdir(parents=True, exist_ok=True)
            self.adopted_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_base()

    # ------------------------------------------------------------ persistence
    def _load(self) -> dict[str, Any]:
        lib = default_library()
        if self.lib_path.is_file():
            try:
                raw = json.loads(self.lib_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    lib.update(raw)
            except Exception as exc:  # noqa: BLE001
                print(f"[Harness] library load failed: {exc}")
        for key, value in default_library().items():
            lib.setdefault(key, copy.deepcopy(value))
        return lib

    def save(self) -> None:
        with self.lock:
            self._save_locked()

    def _save_locked(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.lib_path.with_suffix(".tmp")
        tmp.write_text(_dump(self.lib), encoding="utf-8")
        os.replace(tmp, self.lib_path)

    def _ensure_base(self) -> None:
        if BASE_ID in self.lib["harnesses"]:
            return
        tree = HarnessTree.load(self.base_dir)
        self.lib["harnesses"][BASE_ID] = {
            "id": BASE_ID, "parent": None, "edits": [], "status": "current", "adopted": True,
            "runs": [], "avg_survival": 0.0, "conclusion": "starting point", "created": self.now(),
            "fingerprint": tree.fingerprint(),
        }
        self.save()

    def base_files(self) -> dict[str, str]:
        if self._base_files is None:
            self._base_files = dict(HarnessTree.load(self.base_dir).files)
        return self._base_files

    # ------------------------------------------------------------ trees on disk
    def tree_dir(self, harness_id: str) -> Path:
        if harness_id == BASE_ID:
            return self.base_dir
        cand = self.candidates_dir / harness_id
        if cand.is_dir():
            return cand
        return self.adopted_dir / harness_id

    def load_tree(self, harness_id: str) -> HarnessTree:
        return HarnessTree.load(self.tree_dir(harness_id))

    def _write_tree_locked(self, target: Path, files: dict[str, str]) -> None:
        """Write a whole tree under a temp name, then rename it into place (atomic on POSIX)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp_{target.name}_{os.getpid()}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for rel, text in files.items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        os.replace(tmp, target)

    def record(self, harness_id: str) -> Optional[dict[str, Any]]:
        return self.lib["harnesses"].get(harness_id) or self.lib["candidates"].get(harness_id)

    # ------------------------------------------------------------ activation
    def activate(self, harness_id: str) -> str:
        """Load the harness tree into the engine so the next run uses it."""
        if not self.enabled:
            return self.active_id
        directory = self.tree_dir(harness_id)
        if not directory.is_dir():
            harness_id = self.lib.get("current") or BASE_ID
            directory = self.tree_dir(harness_id)
        self.engine.reload_harness(directory)
        self.active_id = harness_id
        return harness_id

    def activate_next(self) -> Optional[str]:
        if not self.enabled:
            return None
        hid = self.activate(self.lib.get("next") or self.lib.get("current") or BASE_ID)
        if hid != self.lib.get("next"):
            self.lib["next"] = hid
            self.save()
        self._apply_pin(hid)
        return hid

    def on_run_start(self, run_id: str) -> Optional[str]:
        """Remember which harness (and which exact bytes) this run plays with."""
        if not self.enabled:
            return None
        hid = self.active_id
        fingerprint = self.engine.harness.fingerprint()
        with self.lock:
            self.lib["assignments"][str(run_id)] = {"harness_id": hid, "fingerprint": fingerprint, "ts": self.now(), "done": False}
            if len(self.lib["assignments"]) > 2000:
                for key in list(self.lib["assignments"])[:-2000]:
                    del self.lib["assignments"][key]
            self._save_locked()
        return hid

    # ------------------------------------------------------------ after a run
    @staticmethod
    def _signature(rec: dict[str, Any]) -> tuple:
        return (round(float(rec.get("time") or 0), 1), int(rec.get("level") or 0), int(rec.get("kills") or 0), str(rec.get("partner") or ""))

    def _all_run_entries(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for table in (self.lib["harnesses"], self.lib["candidates"]):
            for rec in table.values():
                out.extend(rec.get("runs") or [])
        return out

    def is_duplicate(self, rec: dict[str, Any]) -> bool:
        run_id = str(rec.get("run_id") or "")
        entries = self._all_run_entries()
        if run_id and any(str(e.get("run_id")) == run_id for e in entries):
            return True
        sig = self._signature(rec)
        return sig[0] > 0 and any(e.get("signature") and tuple(e["signature"]) == sig for e in entries)

    def _read_run_file(self, run_id: str) -> Optional[dict[str, Any]]:
        runs_dir = getattr(self.engine, "runs_dir", None)
        if not runs_dir:
            return None
        for path in sorted(Path(runs_dir).glob(f"run_*_{run_id}.json"), reverse=True):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
        return None

    def after_run(self, rec: dict[str, Any], trial: Optional[dict[str, Any]] = None,
                  result: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """Record the run for the harness it used, compose when due, and prepare the next run."""
        if not self.enabled:
            return None
        run_id = str(rec.get("run_id") or "")
        if self.is_duplicate(rec):
            return {"status": "duplicate", "run_id": run_id}
        if trial is None:
            saved = self._read_run_file(run_id) if run_id else None
            trial = (saved or {}).get("trial") if saved else None
        trial = trial or {}
        plan = trial.get("plan") if isinstance(trial.get("plan"), dict) else {}
        assignment = self.lib["assignments"].get(run_id) or {}
        hid = assignment.get("harness_id") or self.active_id
        if self.record(hid) is None:
            hid = self.lib.get("current") or BASE_ID

        t = float(rec.get("time") or 0)
        level = int(rec.get("level") or 1)
        tl = rec.get("timeline") or []
        kills = int(rec.get("kills") or (tl[-1].get("kills", 0) if tl else 0))
        boss_kills = int(rec.get("boss_kills") or (tl[-1].get("boss_kills", 0) if tl else 0))
        score, _, _ = compute_power_score(t, level, kills, tl, boss_kills=boss_kills)
        entry = {
            "run_id": run_id, "run_number": (result or {}).get("run") or getattr(self.engine, "kb", {}).get("total_runs"),
            "time": round(t, 1), "power": score, "level": level, "kills": kills, "cause": str(rec.get("death_cause") or "unknown"),
            "stance": plan.get("stance"), "build_id": plan.get("build_id"), "build_text": plan.get("build_text"),
            "prefer": plan.get("prefer"), "policy": trial.get("policy") or plan.get("policy"),
            "signature": list(self._signature(rec)), "ts": self.now(),
        }
        with self.lock:
            target = self.record(hid)
            target["runs"] = (target.get("runs") or []) + [entry]
            target["runs"] = target["runs"][-60:]
            target["avg_survival"] = round(_mean([float(r["time"]) for r in target["runs"]]), 1)
            if run_id in self.lib["assignments"]:
                self.lib["assignments"][run_id]["done"] = True
            self.lib["runs_seen"] = int(self.lib.get("runs_seen") or 0) + 1
            composed = self._compose_locked()
            next_id, reason = self._schedule_locked(rec, run_id)
            self.lib["next"] = next_id
            self._save_locked()
        self.activate(next_id)
        self._apply_pin(next_id)
        self.save()
        self.write_markdown()
        return {"status": "recorded", "run_id": run_id, "harness_used": hid, "next": next_id, "reason": reason,
                "composed": composed, "pinned": self._is_candidate(next_id)}

    def _is_candidate(self, harness_id: str) -> bool:
        return harness_id in self.lib["candidates"] and self.lib["candidates"][harness_id].get("status") == "testing"

    # ------------------------------------------------------------ one variable at a time
    def _apply_pin(self, next_id: str) -> None:
        """While a candidate is probed, keep knobs, play style and build from the parent's latest run."""
        engine = self.engine
        journal = getattr(engine, "journal", None)
        if not self._is_candidate(next_id):
            engine.policy_pin = None
            if journal is not None and isinstance(journal.data.get("next_plan"), dict):
                if journal.data["next_plan"].pop("harness_probe", None) is not None:
                    journal.save()
            return
        cand = self.lib["candidates"][next_id]
        parent = self.record(cand.get("parent") or self.lib["current"]) or {}
        ref = (parent.get("runs") or [{}])[-1]
        policy = ref.get("policy")
        why = f"harness probe {next_id}: {', '.join(cand.get('edits') or [])}"
        engine.policy_pin = {"policy": dict(policy), "why": why} if isinstance(policy, dict) else None
        if journal is None:
            return
        plan = journal.data.get("next_plan")
        if not isinstance(plan, dict):
            plan = journal.plan_next(use_jev=False)
        if ref.get("stance"):
            plan["stance"] = ref["stance"]
            try:
                from journal import STANCES  # noqa: WPS433
                plan["stance_label"] = STANCES.get(ref["stance"], {}).get("label", plan.get("stance_label"))
            except Exception:  # noqa: BLE001
                pass
            plan["stance_trial"] = False
        if ref.get("build_id"):
            plan["build_id"] = ref["build_id"]
            if ref.get("build_text"):
                plan["build_text"] = ref["build_text"]
            if ref.get("prefer") is not None:
                plan["prefer"] = list(ref["prefer"])
        if isinstance(policy, dict):
            plan["policy"] = dict(policy)
        plan["harness_probe"] = next_id
        journal.data["next_plan"] = plan
        journal.save()

    # ------------------------------------------------------------ schedule
    def _schedule_locked(self, rec: dict[str, Any], run_id: str) -> tuple[str, str]:
        current = self.lib["current"]
        n = int(self.lib.get("runs_seen") or 0)
        if n > 0 and (n + 1) % self.retest_every == 0:
            return current, "retest current harness"
        rnd = self.lib["round"]
        if rnd.get("parent") != current:
            rnd["parent"], rnd["candidates"] = current, []
        for cid in rnd["candidates"]:
            cand = self.lib["candidates"].get(cid)
            if cand and cand.get("status") == "testing" and len(cand.get("runs") or []) < self.samples:
                return cid, "continue probing candidate"
        if len(rnd["candidates"]) < self.k:
            if not (self.record(current) or {}).get("runs"):
                return current, "need a baseline run on the current harness"
            cid = self._propose_locked(rec, run_id)
            if cid:
                return cid, "probe new candidate"
            return current, "no applicable candidate"
        return current, "waiting for compose"

    # ------------------------------------------------------------ propose
    def _lesson(self, edit_id: str) -> dict[str, Any]:
        return self.lib["lessons"].get(edit_id) or {"tried": 0, "helped": 0, "hurt": 0, "delta_sum": 0.0}

    def _opposed(self, edit_id: str) -> bool:
        les = self._lesson(edit_id)
        return int(les.get("hurt", 0)) > int(les.get("helped", 0))

    def _record_text(self, edit_id: str) -> str:
        tmpl = self.engine.harness.get("tool_guidance.decisions.propose_edit.edit.criteria_templates") or {}
        les = self._lesson(edit_id)
        tried = int(les.get("tried", 0))
        if not tried:
            return str(tmpl.get("never") or "never tried")
        return str(tmpl.get("record") or "tried {tried}x: helped {helped}, hurt {hurt}, avg survival change {avg_delta:+.0f}s").format(
            tried=tried, helped=int(les.get("helped", 0)), hurt=int(les.get("hurt", 0)),
            avg_delta=float(les.get("delta_sum", 0.0)) / tried)

    def candidate_edits(self, parent_id: Optional[str] = None) -> list[tuple[Edit, dict[str, str]]]:
        """Every grammar edit that applies to the parent and whose result passes validate_tree."""
        parent_id = parent_id or self.lib["current"]
        files = dict(self.load_tree(parent_id).files)
        out: list[tuple[Edit, dict[str, str]]] = []
        for edit in enumerate_edits(files, self.base_files()):
            new = apply_edit(edit.id, files, self.base_files())
            if new is None:
                continue
            try:
                validate_tree(new)
            except ValueError:
                continue
            out.append((edit, new))
        return out

    def _offers(self, valid: list[tuple[Edit, dict[str, str]]], round_no: int) -> list[tuple[Edit, dict[str, str]]]:
        tried_round = {e for cid in self.lib["round"]["candidates"] for e in (self.lib["candidates"].get(cid) or {}).get("edits", [])}

        def key(item: tuple[Edit, dict[str, str]]) -> tuple:
            edit = item[0]
            h = hashlib.sha1(f"{edit.id}|{round_no}".encode("utf-8")).hexdigest()
            return (edit.id in tried_round, int(self._lesson(edit.id).get("tried", 0)), self._opposed(edit.id), h)

        general = sorted([v for v in valid if v[0].kind == "general"], key=key)
        decision = sorted([v for v in valid if v[0].kind == "decision"], key=key)
        half = self.offer_max // 2
        picked = general[:half] + decision[:half]
        rest = sorted([v for v in valid if v not in picked], key=key)
        picked += rest[:self.offer_max - len(picked)]
        return picked

    def fallback_choice(self, offers: list[Edit]) -> Optional[Edit]:
        """Least tried edit the library does not oppose; else least tried overall. Deterministic."""
        if not offers:
            return None
        allowed = [e for e in offers if not self._opposed(e.id)] or list(offers)
        return min(allowed, key=lambda e: (int(self._lesson(e.id).get("tried", 0)), offers.index(e)))

    def _trajectory(self, rec: dict[str, Any], run_id: str) -> str:
        chars = self.engine.harness.get("memory.decisions.propose_edit.trajectory_chars") or 12000
        decisions = decisions_for_run(read_decisions(self.decision_log), run_id) if run_id else []
        harness_id = (self.lib["assignments"].get(run_id) or {}).get("harness_id") or self.active_id
        return render_trajectory(rec, decisions, max_chars=int(chars), harness_id=harness_id)

    def library_lessons(self, k: int = 8) -> list[str]:
        rows = sorted(self.lib["lessons"].items(), key=lambda kv: (-int(kv[1].get("tried", 0)), kv[0]))
        out = []
        for edit_id, les in rows[:k]:
            tried = int(les.get("tried", 0)) or 1
            out.append(f"{edit_id}: tried {tried}x, helped {int(les.get('helped', 0))}, hurt {int(les.get('hurt', 0))}, "
                       f"avg survival change {float(les.get('delta_sum', 0.0)) / tried:+.0f}s")
        return out

    def _propose_locked(self, rec: dict[str, Any], run_id: str) -> Optional[str]:
        parent_id = self.lib["current"]
        valid = self.candidate_edits(parent_id)
        if not valid:
            return None
        round_no = int(self.lib["seq"]["c"]) + 1
        offers = self._offers(valid, round_no)
        by_id = {e.id: (e, files) for e, files in offers}
        summary = summarize_run(rec)
        summary_text = summary_line(rec)
        harness = self.engine.harness
        spec = harness.get("tool_guidance.decisions.propose_edit.edit") or {}
        option_t = str((spec.get("criteria_templates") or {}).get("option") or "{desc} | my record with this change: {record}")
        criteria = {e.id: option_t.format(desc=e.desc, record=self._record_text(e.id)) for e, _ in offers}
        instructions = str(spec.get("instructions") or "Pick the ONE change to my own harness to try in the next run: {summary}")
        try:
            instructions = instructions.format(summary=summary_text)
        except (KeyError, IndexError):
            pass
        parent = self.record(parent_id) or {}
        state = {
            "goal": harness.get("tool_guidance.decisions.propose_edit.state_goal"),
            "last_run": summary,
            "trajectory": self._trajectory(rec, run_id),
            "harness_now": {"id": parent_id, "edits": parent.get("edits") or [], "runs": len(parent.get("runs") or []),
                            "avg_survival_s": parent.get("avg_survival", 0.0)},
            "harness_lessons": self.library_lessons(int(harness.get("memory.decisions.propose_edit.lessons") or 8)),
            "candidates_this_round": [self.lib["candidates"][c]["edits"] for c in self.lib["round"]["candidates"] if c in self.lib["candidates"]],
        }
        questions = {"edit": {"type": str(spec.get("type") or "choice"), "instructions": instructions, "criteria": criteria}}
        chosen: Optional[Edit] = None
        source = "fallback"
        jev = getattr(self.engine, "jev", None)
        if jev:
            try:
                res = jev(state, questions, self.engine.jev_timeout("propose_edit"))
            except Exception as exc:  # noqa: BLE001
                print(f"[Harness] propose call failed: {exc}")
                res = None
            if res and isinstance(res.get("answers"), dict):
                ans = res["answers"].get("edit") or {}
                pick = ans.get("choice")
                if not pick and hasattr(self.engine, "_sample"):
                    pick = self.engine._sample(ans)
                if pick in by_id:
                    chosen, source = by_id[pick][0], "jev"
        if chosen is None:
            chosen = self.fallback_choice([e for e, _ in offers])
            if chosen is None:
                return None
        files = by_id[chosen.id][1]
        self.lib["seq"]["c"] = round_no
        cid = f"c{round_no:04d}"
        self._write_tree_locked(self.candidates_dir / cid, files)
        tree = HarnessTree.load(self.candidates_dir / cid)
        self.lib["candidates"][cid] = {
            "id": cid, "parent": parent_id, "edits": [chosen.id], "edit_desc": [chosen.desc], "kind": chosen.kind,
            "status": "testing", "runs": [], "avg_survival": 0.0, "conclusion": None, "created": self.now(),
            "fingerprint": tree.fingerprint(), "source": source, "offered": list(criteria),
        }
        self.lib["round"]["candidates"].append(cid)
        self.lib["history"].append({"ts": self.now(), "event": "propose", "candidate": cid, "edit": chosen.id, "source": source})
        self.lib["history"] = self.lib["history"][-HISTORY_MAX:]
        return cid

    # ------------------------------------------------------------ compose
    def _compare(self, cand_runs: list[dict[str, Any]], parent_runs: list[dict[str, Any]]) -> dict[str, Any]:
        t_c = _mean([float(r["time"]) for r in cand_runs])
        t_p = _mean([float(r["time"]) for r in parent_runs])
        p_c = _mean([float(r.get("power", 0)) for r in cand_runs])
        p_p = _mean([float(r.get("power", 0)) for r in parent_runs])
        cause_c = _mode([str(r.get("cause")) for r in cand_runs])
        cause_p = _mode([str(r.get("cause")) for r in parent_runs])
        better = t_c > t_p * (1 + MARGIN) and p_c >= p_p * (1 - MARGIN)
        worse = not better
        verdict = "better" if better else "worse"
        conclusion = (f"{verdict}: survival {t_c:.0f}s vs parent {t_p:.0f}s ({t_c - t_p:+.0f}s), "
                      f"power {p_c:.0f} vs {p_p:.0f} ({p_c - p_p:+.0f}), died mostly to {cause_c} vs {cause_p}")
        return {"better": better, "worse": worse, "delta_t": round(t_c - t_p, 1), "delta_p": round(p_c - p_p, 1),
                "cause_cand": cause_c, "cause_parent": cause_p, "conclusion": conclusion}

    def _compose_locked(self) -> Optional[dict[str, Any]]:
        rnd = self.lib["round"]
        parent_id = rnd.get("parent") or self.lib["current"]
        cids = [c for c in rnd.get("candidates") or [] if c in self.lib["candidates"]]
        if len(cids) < self.k:
            return None
        cands = [self.lib["candidates"][c] for c in cids]
        if any(c.get("status") == "testing" and len(c.get("runs") or []) < self.samples for c in cands):
            return None
        parent = self.record(parent_id) or {}
        parent_runs = (parent.get("runs") or [])[-self.recent_n:]
        if not parent_runs:
            return None
        verdicts: dict[str, dict[str, Any]] = {}
        for cand in cands:
            if cand.get("status") != "testing":
                continue
            v = self._compare(cand.get("runs") or [], parent_runs)
            verdicts[cand["id"]] = v
            cand["conclusion"] = v["conclusion"]
            for edit_id in cand.get("edits") or []:
                les = self.lib["lessons"].setdefault(edit_id, {"tried": 0, "helped": 0, "hurt": 0, "delta_sum": 0.0, "last": ""})
                les["tried"] = int(les.get("tried", 0)) + 1
                les["helped" if v["better"] else "hurt"] = int(les.get("helped" if v["better"] else "hurt", 0)) + 1
                les["delta_sum"] = round(float(les.get("delta_sum", 0.0)) + float(v["delta_t"]), 1)
                les["last"] = v["conclusion"]
        adopt = sorted([c for c in cands if verdicts.get(c["id"], {}).get("better")], key=lambda c: -verdicts[c["id"]]["delta_t"])
        adopted_id: Optional[str] = None
        applied: list[str] = []
        skipped: list[str] = []
        if adopt:
            files = dict(self.load_tree(parent_id).files)
            targets: set[str] = set()
            for cand in adopt:
                for edit_id in cand.get("edits") or []:
                    target = edit_target(edit_id)
                    if target in targets:
                        skipped.append(edit_id)
                        continue
                    new = apply_edit(edit_id, files, self.base_files())
                    if new is None:
                        skipped.append(edit_id)
                        continue
                    try:
                        validate_tree(new)
                    except ValueError:
                        skipped.append(edit_id)
                        continue
                    files = new
                    targets.add(target)
                    applied.append(edit_id)
            if applied:
                self.lib["seq"]["h"] = int(self.lib["seq"]["h"]) + 1
                adopted_id = f"h{self.lib['seq']['h']:04d}"
                self._write_tree_locked(self.adopted_dir / adopted_id, files)
                tree = HarnessTree.load(self.adopted_dir / adopted_id)
                self.lib["harnesses"][adopted_id] = {
                    "id": adopted_id, "parent": parent_id, "edits": applied, "status": "current", "adopted": True,
                    "runs": [], "avg_survival": 0.0, "created": self.now(), "fingerprint": tree.fingerprint(),
                    "conclusion": "; ".join(verdicts[c["id"]]["conclusion"] for c in adopt),
                    "from_candidates": [c["id"] for c in adopt],
                }
                if parent_id in self.lib["harnesses"]:
                    self.lib["harnesses"][parent_id]["status"] = "superseded"
                self.lib["current"] = adopted_id
        for cand in cands:
            v = verdicts.get(cand["id"])
            if not v:
                continue
            if v["better"] and adopted_id and any(e in applied for e in cand.get("edits") or []):
                cand["status"] = "adopted"
                cand["adopted_into"] = adopted_id
            elif v["better"]:
                cand["status"] = "rolled_back"
                cand["conclusion"] = (cand.get("conclusion") or "") + " (edit conflicted with another adopted edit; not merged)"
            else:
                cand["status"] = "rolled_back"
        rnd["parent"] = self.lib["current"]
        rnd["candidates"] = []
        event = {"ts": self.now(), "event": "compose", "parent": parent_id, "adopted": adopted_id, "applied": applied,
                 "skipped": skipped, "verdicts": {cid: v["conclusion"] for cid, v in verdicts.items()}}
        self.lib["history"].append(event)
        self.lib["history"] = self.lib["history"][-HISTORY_MAX:]
        return event

    # ------------------------------------------------------------ reporting
    def status(self) -> dict[str, Any]:
        current = self.lib.get("current") or BASE_ID
        cur = self.record(current) or {}
        rnd = self.lib.get("round") or {}
        return {
            "enabled": self.enabled,
            "current": {"id": current, "edits": cur.get("edits") or [], "runs": len(cur.get("runs") or []),
                        "avg_survival_s": cur.get("avg_survival", 0.0), "fingerprint": cur.get("fingerprint")},
            "active": self.active_id,
            "next": self.lib.get("next"),
            "k": self.k, "samples": self.samples, "retest_every": self.retest_every,
            "round": {"parent": rnd.get("parent"), "candidates": list(rnd.get("candidates") or [])},
            "candidates": [
                {k: v for k, v in c.items() if k != "offered"}
                for c in sorted(self.lib["candidates"].values(), key=lambda c: c.get("created", 0))[-20:]
            ],
            "lessons": self.library_lessons(20),
        }

    def write_markdown(self) -> None:
        lib = self.lib
        cur = lib.get("current") or BASE_ID
        L = ["# 🧬 Harness 改進紀錄 (Harness²)", "",
             "> 每局只改一個變數：Propose（由固定語法生成候選）→ Probe（下一局試）→ Compose（同父 harness 比較存活、power、死因，好就採用，差就回滾）。", "",
             f"- 現行 harness：**{cur}**（{len((self.record(cur) or {}).get('runs') or [])} 局，平均存活 {(self.record(cur) or {}).get('avg_survival', 0.0)}s）",
             f"- 下一局用：**{lib.get('next')}** | 每輪候選數 K={self.k} | 每個候選試 {self.samples} 局 | 每 {self.retest_every} 局重測現行", "",
             "## 📜 採用過嘅 harness", "", "| id | parent | 修改 | 局數 | 平均存活 | 狀態 | 結論 |", "|---|---|---|---|---|---|---|"]
        for h in sorted(lib["harnesses"].values(), key=lambda h: h.get("created", 0)):
            L.append(f"| {h['id']} | {h.get('parent') or '-'} | {', '.join(h.get('edits') or []) or '-'} | {len(h.get('runs') or [])} | "
                     f"{h.get('avg_survival', 0.0)}s | {h.get('status')} | {h.get('conclusion') or '-'} |")
        L += ["", "## 🧪 候選", "", "| id | parent | 修改 | 來源 | 局數 | 平均存活 | 狀態 | 結論 |", "|---|---|---|---|---|---|---|---|"]
        for c in sorted(lib["candidates"].values(), key=lambda c: c.get("created", 0))[-30:]:
            L.append(f"| {c['id']} | {c.get('parent')} | {', '.join(c.get('edits') or [])} | {c.get('source')} | {len(c.get('runs') or [])} | "
                     f"{c.get('avg_survival', 0.0)}s | {c.get('status')} | {c.get('conclusion') or '-'} |")
        L += ["", "## 📚 教訓（有證據）", ""]
        lessons = self.library_lessons(30)
        L += [f"- {x}" for x in lessons] or ["- 未有"]
        L += ["", "## 🕑 最近事件", ""]
        for ev in lib["history"][-15:]:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ev.get("ts") or 0)))
            if ev.get("event") == "propose":
                L.append(f"- {when} propose {ev.get('candidate')} ← {ev.get('edit')} ({ev.get('source')})")
            else:
                L.append(f"- {when} compose parent={ev.get('parent')} adopted={ev.get('adopted') or 'none'} applied={ev.get('applied')} "
                         f"verdicts={ev.get('verdicts')}")
        try:
            self.md_path.parent.mkdir(parents=True, exist_ok=True)
            self.md_path.write_text("\n".join(L) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[Harness] markdown write failed: {exc}")
