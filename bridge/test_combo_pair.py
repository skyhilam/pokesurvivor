#!/usr/bin/env python3
"""
Combo-pair completion tests (offline, no network)
    python3 bridge/test_combo_pair.py

History being guarded: in 528 recorded runs the bot took the stat that
completes its own mined combos only ~1/3 of the times it was offered, and
every levelup showing an on-screen "<Stat> Evolves <Move>" hint crashed the
bridge handler (missing `import re`), so that combo path never executed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning import combo_pair_index  # noqa: E402

OK = True


def check(name, cond, extra=""):
    global OK
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  | ' + extra) if extra else ''}")
    OK = OK and bool(cond)


CARDS = [
    {"title": "Tackle Nv.1 > 2", "key": "up:tackle", "type": "up"},
    {"title": "Power Upgrade", "key": "stat:power", "type": "stat"},
    {"title": "Wing Attack New · Flying", "key": "new:wing-attack", "type": "new"},
]
COND = [{"after": "up:dragon-rage", "n": 4, "paired_with": "stat:power"}]

check("maxed main move + paired stat on screen -> completes combo",
      combo_pair_index(CARDS, {"dragon-rage": 5}, set(), COND) == 1)
check("move not upgraded enough yet -> no forced pick",
      combo_pair_index(CARDS, {"dragon-rage": 3}, set(), COND) is None)
check("paired stat already picked this run -> no forced pick",
      combo_pair_index(CARDS, {"dragon-rage": 5}, {"stat:power"}, COND) is None)
check("move not equipped -> no forced pick",
      combo_pair_index(CARDS, {"tackle": 5}, set(), COND) is None)
check("non-stat pairing is ignored",
      combo_pair_index(CARDS, {"dragon-rage": 5}, set(),
                       [{"after": "up:dragon-rage", "n": 4, "paired_with": "up:agility"}]) is None)

# --- bridge integration: import with isolated HOME/log dir and no API key, so
# module-level engine setup stays offline and Jev calls return None.
SNIPPET = r'''
import sys
sys.path.insert(0, "BRIDGE_DIR")
import jev_bridge as jb

jb.ENGINE.journal.data["beliefs"]["bx"] = {
    "claim": "test rule", "about": "card:evo:test", "kind": "card", "status": "supported",
    "confidence": 0.9, "for": [], "against": [], "origin": "test",
    "cond": {"mined": True, "after": "up:dragon-rage", "n": 4, "paired_with": "stat:power"},
    "updated": 0,
}

def call(moves, cards, picked_keys=None):
    return jb.handle_levelup({
        "time": 120, "rerolls": 0,
        "player": {"level": 12, "hpPct": 0.9, "kills": 100, "moves": moves},
        "cards": cards, "picked_keys": picked_keys or [], "picked_titles": [],
    })

moves = [{"id": "dragon-rage", "name": "Dragon Rage", "lvl": 5, "evolved": False}]
cards = [{"title": "Tackle Nv.1 > 2", "key": "up:tackle", "type": "up"},
         {"title": "Power Upgrade", "key": "stat:power", "type": "stat"},
         {"title": "Vigor Upgrade", "key": "stat:vigor", "type": "stat"}]
dec = call(moves, cards)
assert dec.get("source") == "learned_combo_pair" and dec.get("index") == 1, dec

# on-screen hint path (crashed with NameError: re before the fix)
hint_cards = [{"title": "Power Evolves Dragon Rage", "key": "stat:power", "type": "stat",
               "evolves_hint": "Dragon Rage"},
              {"title": "Vigor Upgrade", "key": "stat:vigor", "type": "stat"}]
dec2 = call(moves, hint_cards, picked_keys=["stat:power"])
assert dec2.get("source") == "on_screen_evolves_hint" and dec2.get("index") == 0, dec2

# already-evolved move must not trigger the combo finisher
dec3 = call([{"id": "dragon-rage", "name": "Dragon Rage", "lvl": 5, "evolved": True}], cards)
assert dec3.get("source") != "learned_combo_pair", dec3

print("BRIDGE_INTEGRATION_OK")
'''

with tempfile.TemporaryDirectory() as td:
    env = dict(os.environ)
    env.update({"HOME": td, "JEV_LOG_DIR": str(Path(td) / "logs"), "TYPESAFE_API_KEY": ""})
    bridge_dir = str(Path(__file__).resolve().parent)
    res = subprocess.run([sys.executable, "-c", SNIPPET.replace("BRIDGE_DIR", bridge_dir)],
                         capture_output=True, text=True, env=env, timeout=120)
    ok = res.returncode == 0 and "BRIDGE_INTEGRATION_OK" in res.stdout
    check("bridge: combo finisher + evolves-hint paths execute without crashing", ok,
          "" if ok else (res.stdout + res.stderr)[-600:])

print("\nALL PASS" if OK else "\nSOME FAILED")
sys.exit(0 if OK else 1)
