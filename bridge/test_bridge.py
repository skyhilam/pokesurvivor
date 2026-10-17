#!/usr/bin/env python3
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"

def test():
    # 1. Health
    req = urllib.request.Request(f"{BASE}/health")
    with urllib.request.urlopen(req, timeout=3) as resp:
        print("[Health]", resp.read().decode("utf-8"))

    # 2. Level Up with real Jev call
    payload = {
        "time": 320.5,
        "player": {
            "name": "Bulbasaur",
            "level": 8,
            "hpPct": 0.72,
            "moves": [{"id": "vine-whip", "name": "Latigazo", "lvl": 4}],
            "statsTaken": {"atk": 1},
            "bonds": 0
        },
        "cards": [
            {"id": "vine-whip", "type": "up", "title": "Latigazo (Nv. 5)", "desc": "+40% de daño", "tag": "Nv.4 > 5"},
            {"id": "atk", "type": "stat", "title": "Potencia", "desc": "+10% de daño", "tag": "Evoluciona Latigazo"},
            {"id": "rgn", "type": "stat", "title": "Síntesis", "desc": "+0.9 vida/s", "tag": "Mejora"}
        ],
        "rerolls": 1
    }
    t0 = time.time()
    req = urllib.request.Request(
        f"{BASE}/decide/levelup",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"[LevelUp Decision in {round((time.time()-t0)*1000)}ms]", resp.read().decode("utf-8"))

    # 3. Item
    item_payload = {
        "time": 450,
        "current": "quickclaw",
        "offered": "shellbell",
        "player": {"hpPct": 0.65}
    }
    req = urllib.request.Request(
        f"{BASE}/decide/item",
        data=json.dumps(item_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("[Item Decision]", resp.read().decode("utf-8"))

    # 4. Run lifecycle (dry run: does not touch the real knowledge base)
    def post(path, body, timeout=15):
        r = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode("utf-8"),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    t0 = time.time()
    cp = post("/run/checkpoint", {"t": 240, "level": 6, "hp_pct": 0.38, "kills": 120, "enemies": 60,
                                  "dmg_per_min": 0.55, "dmg_window": {"contact": 30}})
    print(f"[Checkpoint in {round((time.time()-t0)*1000)}ms]", cp["verdict"], cp["posture"], cp["source"])

    t0 = time.time()
    end = post("/run/end", {"dry_run": True, "run_id": "test", "time": 540.2, "level": 18,
                            "death_cause": "swarm_overrun", "damage_sources": {"contact": 150, "charger": 40},
                            "picks": [{"t": 30, "key": "stat:cd", "title": "Reflejos"}],
                            "timeline": [{"t": 60 * i, "level": 2 * i, "hp_pct": 0.8, "kills": 30 * i} for i in range(9)]})
    print(f"[Run-end reflection in {round((time.time()-t0)*1000)}ms]", end["lesson"], "| source:", end["source"])

    # 5. Knowledge summary
    with urllib.request.urlopen(f"{BASE}/knowledge", timeout=3) as resp:
        k = json.loads(resp.read().decode("utf-8"))
        params = k.get("best_params") or k.get("best_policy")
        print("[Knowledge] runs:", k["total_runs"], "best:", k["best_time"], "params:", params)

if __name__ == "__main__":
    test()
