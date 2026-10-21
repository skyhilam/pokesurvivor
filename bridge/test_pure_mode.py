#!/usr/bin/env python3
"""
Pure-mode guard: the live bot (bridge + extension) must not load or embed knowledge
taken from the game's source code. It has to discover the game by playing.

Patterns live in bridge/pure_forbidden.json (pattern -> reason), shared with
harness_tree.validate_tree. Live Python reads that file instead of embedding the
phrases. Markdown and JSON under bridge/harness/ are checked too.

    python3 bridge/test_pure_mode.py
"""
import re
import sys
from pathlib import Path

from harness_tree import load_forbidden

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "bridge" / "harness"
LIVE = [p for p in (ROOT / "bridge").glob("*.py") if not p.name.startswith(("test_", "cleanup_"))] + \
       list((ROOT / "extension").glob("*.js"))
if HARNESS.is_dir():
    LIVE += [p for p in HARNESS.rglob("*") if p.is_file() and p.suffix in {".md", ".json"}]

FORBIDDEN = load_forbidden()

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
