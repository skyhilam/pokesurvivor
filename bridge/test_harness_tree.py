#!/usr/bin/env python3
"""Loader and pure-mode checks for the editable harness tree.

    python3 bridge/test_harness_tree.py
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

from harness_tree import HarnessTree, load_forbidden, validate_tree

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "bridge" / "harness" / "base"
GOAL = "clear 31:00 by becoming strong enough to wipe dense waves and bosses"

REQUIRED = (
    "harness.json",
    "systemprompt.md",
    "guardrails.md",
    "memory.json",
    "tool_guidance.json",
    "skills.json",
    "plugins.json",
)

# Static prompt text copied from the decision call sites. Placeholders below are
# the same {name} spellings those f-strings already use.
VERBATIM = (
    "Choose the card that will make me strongest, judging only from the card text, my plan for this run, "
    "my beliefs and hunches, and what I have observed after picking cards before. "
    "Focus on finishing one damaging move's upgrades to trigger its evolution before spreading upgrades thin across other moves, and pick its paired stat once.",
    " [EARLY SURVIVAL: I have only 1 damaging attack. I urgently need a 2nd damaging attack to survive early enemy swarms before picking utility stats.]",
    " | (already picked this stat once this run; prioritise levelling moves to trigger evolution)",
    " | MY PLAN FOR THIS RUN WANTS THIS CARD",
    " | (0-damage support buff \u2014 do not prioritise over damaging attacks)",
    " | TOP PRIORITY: FINISH UPGRADING MY MAIN ATTACK (now Lv.{lvl}) TO TRIGGER EVOLUTION!",
    " | UPGRADES MY EQUIPPED ATTACK (now Lv.{lvl}) TOWARD EVOLUTION",
    " | MY JOURNAL SAYS THIS STAT PAIRS WITH MY EQUIPPED '{m_id}' FOR EVOLUTION (not picked yet)",
    " | NEED EARLY ATTACK (I only have 1 attack and need wave-clearing DPS)",
    "replace these cards with random new ones (uses 1 reroll)",
    "Keep my held item or swap it, judging only from the text and my experience.",
    "Pick move to fuse into the concurrent multi-cast group.",
    "Bond move {name} (Lvl {lvl})",
    "Start from which policy?",
    "the policy of my highest-scoring run",
    "the policy of my last run",
    "Pick the ONE change to test in the next run to become stronger and survive longer.",
    "lower '{knob}' ({meaning})",
    "raise '{knob}' ({meaning})",
    "set rotate_s={value} ({meaning})",
    "stop investigating unknown things",
    "investigate unknown things",
    "For the next minute, should I push closer into combat range to kill enemies faster, or back off?",
    "For the next minute, steer away from dangerous contact...",
    "For the next minute, sweep up XP gems and pickups...",
    "back off / safer",
    "maintain current attack distance",
    "push closer to kill faster",
    "From what I observed, what ended this run?",
    "my offensive power and kill rate fell behind the enemy swarm",
    "my offensive power fell behind the enemy swarm",
    "Compared with my previous runs, did this run's policy change help me become stronger and progress further?",
    "Compared with my recent runs, did this run's play style and build help me get stronger and last longer?",
    "it helped",
    "it made things worse",
    "too noisy to tell",
    "Should I go to this {key} now? Decide from my own experience with it, my curiosity, my question for this run, and the risk right now.",
    "go to it / touch it now",
    "leave it for now",
    "Choose my partner for the next run using only the shown stats and my own results with each partner.",
    "The menu has a gacha that uses them.",
    "Use the tickets now?",
    "use the tickets now",
    "keep them for later",
    "Pick one permanent upgrade to buy now (or save), judging only from the description and my results.",
    "save coins for later (I already chose to save {streak} times in a row; coins do nothing until spent)",
    "never bought - I don't know what it really does for me",
    "What should my card-picking plan be next run?",
    "Which play style should I use next run?",
    "Like a curious player: which ONE question do I most want to answer next run?",
    "Which partner should I play next run to survive longest toward 31:00 (or to test my partner question if I chose one)? Judge from shown stats and my average/best survival records.",
    "keep my current partner",
    "yes - the evidence answers it well enough",
    "partly - I need more runs to be sure",
    "I did not really get to test it this run",
    "My question this run was: '{ask}'. Does the evidence answer it?",
    "clear 31:00; become strong enough to wipe dense waves and bosses",
    "clear 31:00; I learn the game like a player - by asking questions and testing them",
)


def test_base_loads_and_validates() -> None:
    tree = HarnessTree.load(BASE)
    validate_tree(tree.files)
    assert tree.get("harness.id") == "base"
    assert tree.get("harness.version") == 1
    assert tree.get("harness.inject.guardrails") is False
    assert tree.get("harness.unsupported.subagents")
    assert tree.get("harness.unsupported.scripts")
    assert "subagents" not in tree.files and "scripts" not in tree.files
    assert not any(name.startswith(("subagents", "scripts")) for name in tree.files)
    for name in REQUIRED:
        assert name in tree.files and tree.files[name].strip()
    assert tree.files["systemprompt.md"].strip() == GOAL
    assert tree.files["guardrails.md"].startswith("inject: false\n")

    levelup = tree.get("memory.decisions.handle_levelup")
    assert levelup["lessons"] == 6 and levelup["beliefs"] == 6 and levelup["card_rules"] == 5
    assert tree.get("memory.decisions.handle_item.lessons") == 4
    assert tree.get("memory.decisions.checkpoint.lessons") == 4
    assert tree.get("memory.decisions.upgrade.lessons") == 5
    assert tree.get("memory.decisions.run_start.lessons") == 5
    assert tree.get("memory.decisions.end_run_response.lessons") == 5
    policy = tree.get("memory.decisions.jev_policy")
    assert policy["lessons"] == 4 and policy["world_brief"] == 10 and policy["learned_cap"] == 8
    assert tree.get("memory.shared.extra_context.k") == 10
    assert tree.get("memory.decisions.end_run_no_journal.world_brief") == 10
    assert tree.get("memory.decisions.engage.world_brief") == 8
    assert tree.get("memory.decisions.journal_reflect.beliefs") == 6
    assert tree.get("memory.decisions.plan_next.beliefs") == 8

    timeouts = tree.get("plugins.jev_timeout_s")
    assert timeouts["handle_levelup"] == 4.0
    assert timeouts["jev_policy"] == 8.0
    assert timeouts["checkpoint"] == 3.5
    assert timeouts["journal_reflect"] == 6.0
    assert timeouts["end_run_no_journal"] == 6.0
    assert timeouts["engage"] == 2.5
    assert timeouts["gacha"] == 3.0
    assert timeouts["default"] == 6.0

    shortcuts = tree.get("skills.handle_levelup.shortcuts")
    for name in ("learned_evolution", "on_screen_evolves_hint", "learned_combo_pair", "need_early_attack"):
        assert shortcuts[name]["enabled"] is True
    assert shortcuts["need_early_attack"]["max_level"] == 5
    assert shortcuts["need_early_attack"]["damaging_moves_below"] == 2
    weights = [item["value"] for item in tree.get("skills.handle_levelup.fallback_score.weights")]
    assert weights == [15, -25, 50, 40, 12, -20, 35]

    blob = tree.files["tool_guidance.json"]
    for phrase in VERBATIM:
        assert phrase in blob, phrase
    assert GOAL in tree.files["systemprompt.md"]

    old = os.environ.get("JEV_HARNESS_DIR")
    try:
        os.environ["JEV_HARNESS_DIR"] = str(BASE)
        assert HarnessTree.load().fingerprint() == tree.fingerprint()
        os.environ["JEV_HARNESS_DIR"] = "bridge/harness/base"
        assert HarnessTree.load().directory.resolve() == BASE.resolve()
        os.environ.pop("JEV_HARNESS_DIR", None)
        assert HarnessTree.load().directory.resolve() == BASE.resolve()
    finally:
        if old is None:
            os.environ.pop("JEV_HARNESS_DIR", None)
        else:
            os.environ["JEV_HARNESS_DIR"] = old
    print("PASS base loads and validates")


def test_fingerprint_stable_until_one_character_changes() -> None:
    tree = HarnessTree.load(BASE)
    first = tree.fingerprint()
    assert first == tree.fingerprint()
    assert first == HarnessTree.load(BASE).fingerprint()
    assert len(first) == 64 and re.fullmatch(r"[0-9a-f]{64}", first)

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "base"
        shutil.copytree(BASE, copy)
        target = copy / "systemprompt.md"
        target.write_text(target.read_text(encoding="utf-8").replace("c", "C", 1), encoding="utf-8")
        changed = HarnessTree.load(copy)
        assert changed.fingerprint() != first
        assert changed.files["systemprompt.md"] != tree.files["systemprompt.md"]
    print("PASS fingerprint stable until one character changes")


def _literal_forbidden() -> tuple[str, str]:
    for pattern, why in load_forbidden().items():
        if pattern == re.escape(pattern):
            return pattern, why
    raise AssertionError("expected a literal forbidden phrase")


def test_rejects_tree_listing_every_problem() -> None:
    tree = HarnessTree.load(BASE)
    literal, why = _literal_forbidden()

    for name in REQUIRED:
        broken = dict(tree.files)
        del broken[name]
        try:
            validate_tree(broken)
        except ValueError as exc:
            assert name in str(exc), name
        else:
            raise AssertionError(f"missing {name} was accepted")

    bad = dict(tree.files)
    del bad["systemprompt.md"]
    del bad["skills.json"]
    bad["memory.json"] = "{not json"
    bad["guardrails.md"] = tree.files["guardrails.md"] + "\n" + literal + "\n"
    bad["plugins.json"] = "[" + bad["plugins.json"] + "]"
    try:
        validate_tree(bad)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("bad tree was accepted")
    for needle in ("systemprompt.md", "skills.json", "memory.json", "plugins.json", why):
        assert needle in message, needle
    assert message.count("\n- ") >= 5

    forbidden_only = dict(tree.files)
    forbidden_only["systemprompt.md"] = literal + "\n"
    try:
        validate_tree(forbidden_only)
    except ValueError as exc:
        assert why in str(exc)
    else:
        raise AssertionError("forbidden tree was accepted")
    print("PASS invalid trees list every problem")


def test_text_formats_placeholders() -> None:
    tree = HarnessTree.load(BASE)
    assert tree.text("systemprompt", "").strip() == GOAL
    anno = tree.text(
        "tool_guidance",
        "decisions.handle_levelup.card_selection.annotations.top_priority_evolution",
        lvl=3,
    )
    assert anno == " | TOP PRIORITY: FINISH UPGRADING MY MAIN ATTACK (now Lv.3) TO TRIGGER EVOLUTION!"
    pair = tree.text(
        "tool_guidance",
        "decisions.handle_levelup.card_selection.annotations.journal_stat_pair",
        m_id="ember",
    )
    assert pair == " | MY JOURNAL SAYS THIS STAT PAIRS WITH MY EQUIPPED 'ember' FOR EVOLUTION (not picked yet)"
    engage = tree.text("tool_guidance", "decisions.engage.engage.instructions", key="shrine")
    assert engage == (
        "Should I go to this shrine now? Decide from my own experience with it, my curiosity, "
        "my question for this run, and the risk right now."
    )
    bond = tree.text("tool_guidance", "decisions.handle_bond.bond_pick.criteria.move", name="Tackle", lvl=2)
    assert bond == "Bond move Tackle (Lvl 2)"
    try:
        tree.text("tool_guidance", "decisions.missing")
    except KeyError:
        pass
    else:
        raise AssertionError("missing template returned a value")
    print("PASS text formats placeholders")


def main() -> None:
    test_base_loads_and_validates()
    test_fingerprint_stable_until_one_character_changes()
    test_rejects_tree_listing_every_problem()
    test_text_formats_placeholders()
    print("ALL PASS")


if __name__ == "__main__":
    main()
