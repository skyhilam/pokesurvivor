#!/usr/bin/env python3
"""
Pure-mode guard: the live bot (bridge + extension) must not load or embed knowledge
taken from the game's source code. It has to discover the game by playing.

    python3 bridge/test_pure_mode.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = [p for p in (ROOT / "bridge").glob("*.py") if not p.name.startswith(("test_", "cleanup_"))] + \
       list((ROOT / "extension").glob("*.js"))

FORBIDDEN = {
    r"archive[/\\]assisted": "loads the archived source-assisted mode",
    r"game_kb_from_source": "loads facts extracted from the game's source code",
    r"Nv\.8": "hard-codes the max move level / evolution rule",
    r"SPECIAL_PICKUP_VALUES": "hand-written pickup values instead of measured ones",
    r"missingByGen": "gacha banner heuristic copied from the source-assisted version",
    r"is_valuable": "hand-written 'this object is valuable' keyword list",
    r"SUPPORT_BUFF_MOVES": "hand-written list of which moves are buffs",
    r"KNOWN_BUFF_MOVES": "hand-written list of which moves are buffs",
    r"provide items, tickets, and evolutions": "prompt claims about what map objects give",
}

bad = []
for p in LIVE:
    text = p.read_text(encoding="utf-8")
    for pat, why in FORBIDDEN.items():
        for m in re.finditer(pat, text):
            line = text[:m.start()].count("\n") + 1
            bad.append(f"{p.relative_to(ROOT)}:{line}: {why}")

for b in bad:
    print("FAIL ", b)
print(f"\nchecked {len(LIVE)} live files: " + ("ALL PASS (no source-derived knowledge)" if not bad else f"{len(bad)} violations"))
sys.exit(1 if bad else 0)
