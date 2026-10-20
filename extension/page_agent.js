/**
 * PokéSurvivor Hybrid Autonomous Agent (MAIN World)
 * =================================================
 * Combines 60 FPS reactive potential-field reflex steering
 * with TypeSafe Jev macro-strategic decisions AND
 * an intelligent Auto Skill Weaving Engine (Digit1–Digit4).
 */

(function () {
  "use strict";

  if (window.__PS_JEV_AGENT_LOADED__) return;
  window.__PS_JEV_AGENT_LOADED__ = true;

  console.log("%c[PokéSurvivor Jev Bot] Initializing MAIN world agent with Auto Skill Weaving...", "color:#ffcb3d; font-weight:bold; font-size:14px;");

  const BOT_BUILD = "2026-10-19.1-weave-fix+telegraph-evasion"; // stamped into every run record; bump on behaviour changes
  let PS = null; // Captured internal game namespace (_0x5c92f0)
  let botEnabled = true;
  let autoPlayMenus = true;
  let skillWeavingEnabled = true;
  let lastSlotSwitchTime = 0;
  let weaveCount = 0;

  // ---- Self-learning state -------------------------------------------------
  // runParams  : params under test this run (chosen by the bridge's experiment planner)
  // tempAdjust : short-lived in-run correction from mid-run self-check
  // activeAdaptations = runParams ⊕ tempAdjust  (read by the reflex engine)
  const DEFAULT_PARAMS = {
    aggression: 1.5,
    combat_range: 110,
    orbit: 1.3,
    evasion_weight_mult: 1.0,
    kite_distance_boost: 0,
    heal_seek_threshold: 0.65,
    hazard_avoidance_weight: 1.0,
    pickup_mult: 1.8,
  };
  let runParams = { ...DEFAULT_PARAMS };
  let tempAdjust = { aggression_mult: 1.0, evasion_weight_mult: 1.0, pickup_mult: 1.0, kite_distance_boost: 0 };
  let activeAdaptations = { ...DEFAULT_PARAMS };
  let statPriorities = ["cd", "atk", "hp", "spd", "rgn", "def"];

  try {
    const cached = localStorage.getItem("ps_jev_best_params");
    if (cached) runParams = { ...runParams, ...JSON.parse(cached) };
  } catch (e) {}

  // Jev's per-run policy (combat, farming, survival, exploration)
  let policy = { aggression: 1.5, combat_range: 110, orbit: 1.3, greed: 1.8, fear: 1.0, telegraph_fear: 1.2, keep_distance: 0,
                 w_level: 40, w_xp: 30, w_hp: 35, w_coins: 0.3, w_tickets: 15, w_bonds: 25, rotate_s: 2, explore: 1 };
  function applyPolicy(p) {
    policy = { ...policy, ...p };
    runParams = { ...DEFAULT_PARAMS,
      aggression: p.aggression ?? runParams.aggression,
      combat_range: p.combat_range ?? runParams.combat_range,
      orbit: p.orbit ?? runParams.orbit,
      evasion_weight_mult: p.fear ?? runParams.evasion_weight_mult,
      hazard_avoidance_weight: p.telegraph_fear ?? runParams.hazard_avoidance_weight,
      kite_distance_boost: p.keep_distance ?? runParams.kite_distance_boost,
      pickup_mult: p.greed ?? runParams.pickup_mult };
    refreshAdaptations();
  }

  function refreshAdaptations() {
    activeAdaptations = {
      ...runParams,
      aggression: runParams.aggression * (tempAdjust.aggression_mult || 1),
      evasion_weight_mult: runParams.evasion_weight_mult * (tempAdjust.evasion_weight_mult || 1),
      pickup_mult: runParams.pickup_mult * (tempAdjust.pickup_mult || 1),
      kite_distance_boost: runParams.kite_distance_boost + (tempAdjust.kite_distance_boost || 0),
      heal_seek_threshold: tempAdjust.heal_seek_threshold || runParams.heal_seek_threshold,
    };
  }
  refreshAdaptations();

  let learnedSummary = null; // GET /knowledge
  let lastLessonText = "Awaiting first run...";
  let lastCheckpoint = null;
  let againClickScheduled = false;
  let overMenuTried = false;   // one menu detour per game-over (falls back to "play again")

  // Everything recorded about the current run (sent to /run/end)
  const runState = {
    active: false,
    starting: false,
    ending: false,
    runId: null,
    runNumber: 0,
    experiment: "-",
    hypothesis: "",
    timeline: [],
    picks: [],
    items: [],
    lastSampleT: -1,
    lastCheckpointT: 0,
    dmgAtLastSample: null,
    lastT: 0,
    lastLevel: 1,
    lastKills: 0,
  };

  let currentTactics = {
    stance: "farm_aggro",
    evasion_weight: 1.2,
    pickup_weight: 2.5,
    kite_distance: 120,
    source: "default"
  };

  const decisionHistory = [];
  let lastTacticsUpdate = 0;
  let levelUpPending = false;
  let itemPending = false;
  let bondPending = false;
  let reqCounter = 0;
  const pendingRequests = new Map();

  // Directional error accumulator for WASD error-diffusion modulation
  let accumErrX = 0;
  let accumErrY = 0;
  const activeKeys = { KeyW: false, KeyA: false, KeyS: false, KeyD: false };

  // Prevent background tab pausing
  try {
    Object.defineProperty(document, "hidden", { get: () => false, configurable: true });
    Object.defineProperty(document, "visibilityState", { get: () => "visible", configurable: true });
  } catch (e) {}

  // Intercept window postMessage responses from content_bridge.js
  window.addEventListener("message", (event) => {
    if (event.source !== window || !event.data || !event.data.ps_jev_res) return;
    const { reqId, response } = event.data;
    const resolver = pendingRequests.get(reqId);
    if (resolver) {
      pendingRequests.delete(reqId);
      resolver(response);
    }
  });

  function callJevBridge(type, payload, timeoutMs = 6000) {
    return new Promise((resolve) => {
      const reqId = ++reqCounter;
      const timer = setTimeout(() => {
        if (pendingRequests.has(reqId)) {
          pendingRequests.delete(reqId);
          resolve({ ok: false, error: "timeout", fallback: true });
        }
      }, timeoutMs);

      pendingRequests.set(reqId, (res) => {
        clearTimeout(timer);
        resolve(res);
      });

      window.postMessage({ ps_jev_req: true, reqId, type, payload }, "*");
    });
  }

  // -------------------------------------------------------------
  // Clean, non-intrusive capture of internal namespace `_0x5c92f0`
  // -------------------------------------------------------------
  Object.defineProperty(Object.prototype, "Flow", {
    configurable: true,
    set(val) {
      delete Object.prototype.Flow;
      Object.defineProperty(this, "Flow", {
        value: val,
        writable: true,
        enumerable: true,
        configurable: true,
      });
      if (this && this.Game && this.Player && this.EnemyMgr && !PS) {
        PS = this;
        window.__PS_CORE__ = this;
        console.log("%c[PokéSurvivor Jev Bot] Successfully captured internal game core!", "color:#5fe08a; font-weight:bold;");
        onGameCoreReady();
      }
    },
  });

  async function onGameCoreReady() {
    createHUD();
    requestAnimationFrame(botLoop);

    // Sync accumulated knowledge from bridge
    try {
      const memRes = await callJevBridge("JEV_MEMORY", {}, 3000);
      if (memRes && memRes.data && !memRes.data.error) {
        learnedSummary = memRes.data;
        if (learnedSummary.best_params) {
          runParams = { ...runParams, ...learnedSummary.best_params };
          refreshAdaptations();
        }
        if (learnedSummary.stat_priorities) statPriorities = learnedSummary.stat_priorities;
        if (learnedSummary.lessons && learnedSummary.lessons.length) lastLessonText = learnedSummary.lessons[0];
        console.log("%c[PokéSurvivor Jev Bot] Knowledge loaded:", "color:#5fe08a; font-weight:bold;", learnedSummary);
      }
    } catch (e) {}

    flushUnsentRuns();
    claimMissionsAndAchievements();
    // read on-screen announcements like a player (passive: original function still runs)
    if (PS.Spawner && typeof PS.Spawner.say === "function" && !PS.Spawner.__psCaptured) {
      const orig = PS.Spawner.say;
      PS.Spawner.say = function (text, ...rest) {
        try {
          const msgStr = String(text);
          const tNow = Math.round((PS.Game && PS.Game.st && PS.Game.st.time) || 0);
          screenMessages.push({ t: tNow, text: msgStr, ms: performance.now() });
          if (screenMessages.length > 60) screenMessages.shift();
          if (runState.active && !runState.firstItemAnnounced && (!runState.items || runState.items.length === 0) && /llevas|holding|equipped/i.test(msgStr)) {
            runState.firstItemAnnounced = 1;
          }
          if (runState.active && /vínculo|movimientos a la vez/i.test(msgStr)) {
            runState.bondsCount = (runState.bondsCount || 0) + 1;
          }
        } catch (e) {}
        return orig.call(this, text, ...rest);
      };
      PS.Spawner.__psCaptured = true;
    }
  }

  const initInterval = setInterval(() => {
    if (PS) {
      clearInterval(initInterval);
      return;
    }
    if (window.__PS_CORE__) {
      PS = window.__PS_CORE__;
      clearInterval(initInterval);
      onGameCoreReady();
    }
  }, 200);

  // -------------------------------------------------------------
  // WASD Keyboard Emulation
  // -------------------------------------------------------------
  const lastKeyDispatchT = {};
  function setKey(code, pressed) {
    const now = performance.now();
    if (activeKeys[code] === pressed) {
      if (!pressed || now - (lastKeyDispatchT[code] || 0) < 220) return;
    }
    activeKeys[code] = pressed;
    lastKeyDispatchT[code] = now;
    const eventType = pressed ? "keydown" : "keyup";
    const ev = new KeyboardEvent(eventType, {
      code,
      key: code.replace("Key", "").toLowerCase(),
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(ev);
  }

  function releaseAllKeys() {
    for (const code of Object.keys(activeKeys)) {
      if (activeKeys[code]) setKey(code, false);
      lastKeyDispatchT[code] = 0;
    }
    accumErrX = 0;
    accumErrY = 0;
  }

  function applyContinuousVectorWASD(vx, vy) {
    if (Math.hypot(vx, vy) < 0.05) {
      releaseAllKeys();
      return;
    }

    const targetX = vx + accumErrX;
    const targetY = vy + accumErrY;

    let dirX = 0;
    let dirY = 0;
    if (targetX > 0.35) dirX = 1;
    else if (targetX < -0.35) dirX = -1;

    if (targetY > 0.35) dirY = 1;
    else if (targetY < -0.35) dirY = -1;

    accumErrX = Math.max(-1, Math.min(1, targetX - dirX));
    accumErrY = Math.max(-1, Math.min(1, targetY - dirY));

    setKey("KeyA", dirX < 0);
    setKey("KeyD", dirX > 0);
    setKey("KeyW", dirY < 0);
    setKey("KeyS", dirY > 0);
  }

  // -------------------------------------------------------------
  // Intelligent Auto Skill Weaving Engine (Digit1–Digit4)
  // -------------------------------------------------------------
  function getSlotSummary(slot) {
    if (!slot || !slot.length) return "Empty";
    return slot.map((m) => m.name || m.id).join(" + ");
  }

  function switchSlot(targetIndex, fromBuffTap = false) {
    if (!PS || !PS.Game || !PS.Game.player) return;
    const player = PS.Game.player;
    if (player.dead) return;
    if (targetIndex < 0 || targetIndex >= player.slots.length) return;
    if (player.active === targetIndex) return;

    if (fromBuffTap) {
      prevActiveSlot = targetIndex;
    } else {
      prevActiveSlot = player.active || 0;
      lastSlotSwitchTime = performance.now();
    }

    // 1. Dispatch keyboard events so game registers Digit key
    const code = `Digit${targetIndex + 1}`;
    const key = String(targetIndex + 1);
    window.dispatchEvent(new KeyboardEvent("keydown", { code, key, bubbles: true }));
    window.dispatchEvent(new KeyboardEvent("keyup", { code, key, bubbles: true }));

    // 2. Also ensure player.setActive is updated
    if (typeof player.setActive === "function") {
      player.setActive(targetIndex);
    }

    weaveCount++;
    const slotDesc = getSlotSummary(player.slots[targetIndex]);
    recordDecision(`⚔️ Skill: [${targetIndex + 1}] ${slotDesc}`);
  }

  const slotStats = new Map(); // moveId -> { kills: 0, dmg: 0, combatSec: 0, lastCastT: 0 }
  let enemyHpMap = new WeakMap();
  let lastKillsObserved = 0;
  let prevActiveSlot = 0;
  let buffTapUntil = 0;
  let buffReturnSlot = 0;
  let lastSlotTickT = 0;
  let weaveBlockedUntil = 0;

  function updateSkillWeaving(player) {
    if (!player.slots || !player.slots.length) return;
    const now = performance.now();
    const dt = lastSlotTickT ? Math.min(0.2, (now - lastSlotTickT) / 1000) : 0.016;
    lastSlotTickT = now;

    const curKills = player.kills || 0;
    const dk = curKills > lastKillsObserved ? curKills - lastKillsObserved : 0;
    lastKillsObserved = curKills;

    // Observe HP lost by visible enemies this frame + whether any enemy is in weapon range (< 220px)
    let frameDmgDealt = 0;
    let nearEnemy = false;
    const px = player.x, py = player.y;
    for (const e of PS.EnemyMgr ? PS.EnemyMgr.all() : []) {
      const d = Math.hypot(e.x - px, e.y - py);
      if (!e.dead && d < 220) nearEnemy = true;
      const prevHp = enemyHpMap.get(e);
      const curHp = e.dead ? 0 : (e.hp || 0);
      if (prevHp !== undefined && curHp < prevHp && d < 360) {
        frameDmgDealt += (prevHp - curHp);
      }
      enemyHpMap.set(e, curHp);
    }

    // Reset measurement when a move evolves so its post-evolution DPS is measured cleanly
    for (const s of player.slots) {
      for (const m of (s || [])) {
        const st = slotStats.get(m.id);
        if (m.evolved && st && !st.wasEvolved) {
          st.dmg = 0; st.kills = 0; st.combatSec = 0; st.wasEvolved = true;
        }
      }
    }

    const activeSlotMoves = player.slots[player.active] || [];
    const activeTesting = activeSlotMoves.some((m) => ((slotStats.get(m.id) || {}).combatSec || 0) < 3.5);
    const graceMs = activeTesting ? 900 : 450;

    // In-flight projectiles in the first graceMs after switching away from a damaging slot (or during a brief buff tap) belong to prevActiveSlot
    const inPostSwitchGrace = (now - lastSlotSwitchTime) < graceMs || buffTapUntil > 0;
    const creditIdx = (inPostSwitchGrace && prevActiveSlot < player.slots.length) ? prevActiveSlot : (player.active || 0);
    const creditSlot = player.slots[creditIdx] || [];
    for (const m of creditSlot) {
      const st = slotStats.get(m.id) || { kills: 0, dmg: 0, combatSec: 0, lastCastT: 0, wasEvolved: !!m.evolved };
      // When 2 moves are fused in the same slot, do not credit damage/kills to a move already observed to be a 0-dmg support buff
      const knownZeroDmg = creditSlot.length > 1 && st.combatSec >= 3.5 && (st.dmg / st.combatSec) < 5;
      if (!knownZeroDmg) {
        if (dk > 0) st.kills += dk;
        if (frameDmgDealt > 0) st.dmg += frameDmgDealt;
      }
      slotStats.set(m.id, st);
    }

    if (!inPostSwitchGrace && nearEnemy) {
      const activeMoves = player.slots[player.active] || [];
      for (const m of activeMoves) {
        const st = slotStats.get(m.id) || { kills: 0, dmg: 0, combatSec: 0, lastCastT: 0, wasEvolved: !!m.evolved };
        st.combatSec += dt;
        slotStats.set(m.id, st);
      }
    }

    if (!skillWeavingEnabled || player.slots.length <= 1) return;

    // 1. If currently in a quick buff tap, return to main weapon once tap duration expires
    if (buffTapUntil > 0) {
      if (now >= buffTapUntil) {
        buffTapUntil = 0;
        if (player.active !== buffReturnSlot && buffReturnSlot < player.slots.length) {
          switchSlot(buffReturnSlot, true);
        }
      }
      return;
    }

    // 2. Classify slots empirically from observed close-combat window (3.5s): damaging weapons vs 0-damage support buffs
    const rawInfo = player.slots.map((s, idx) => {
      const moves = s || [];
      const kills = moves.reduce((sum, m) => sum + ((slotStats.get(m.id) || {}).kills || 0), 0);
      const dmg = moves.reduce((sum, m) => sum + ((slotStats.get(m.id) || {}).dmg || 0), 0);
      const cSec = moves.reduce((sum, m) => Math.max(sum, ((slotStats.get(m.id) || {}).combatSec || 0)), 0);
      const dps = cSec >= 1.5 ? dmg / cSec : 0;
      return { idx, moves, kills, dmg, cSec, dps };
    });

    let maxDps = 0, bestDmgIdx = 0;
    for (const s of rawInfo) {
      if ((s.cSec >= 3.0 || s.idx === 0) && s.dps > maxDps) { maxDps = s.dps; bestDmgIdx = s.idx; }
    }

    const slotInfo = rawInfo.map((s) => {
      // Never classify the highest-DPS tested slot (or slot 0 before any DPS observed) as a buff
      const isPrimary = s.idx === bestDmgIdx;
      const isBuff = !isPrimary && (s.cSec >= 3.5 && s.dps < Math.max(5, maxDps * 0.08) && s.kills <= 1);
      return { ...s, isBuff };
    });

    const bestDmg = slotInfo.filter((s) => !s.isBuff && s.moves.length > 0).sort((a, b) => (b.dps - a.dps) || (b.kills - a.kills))[0] || slotInfo[0];
    const currentInfo = slotInfo[player.active];

    // If current slot is an observed 0-damage buff, immediately switch back to best damaging weapon without post-switch grace leak
    if (currentInfo && currentInfo.isBuff && bestDmg && bestDmg.idx !== player.active) {
      switchSlot(bestDmg.idx, true);
      return;
    }

    // If any newly acquired slot has not been tested for 3.5s in close combat, hold it continuously so we learn its true DPS
    const untested = slotInfo.find((s) => s.moves.length > 0 && s.cSec < 3.5);
    if (untested) {
      if (untested.idx === player.active) return;
      if (nearEnemy && (now - lastSlotSwitchTime) > 1800) {
        switchSlot(untested.idx, false);
        return;
      }
    }

    // 3. From main weapon: check if any ready buff slot should be tapped briefly
    const buffCandidate = slotInfo.find((s) => {
      if (!s.isBuff || s.idx === player.active) return false;
      const firstMove = s.moves[0];
      const lastCast = (firstMove && (slotStats.get(firstMove.id) || {}).lastCastT) || 0;
      return now - lastCast >= 4800; // 4.8s cooldown interval
    });

    if (buffCandidate && now >= weaveBlockedUntil) {
      buffReturnSlot = bestDmg ? bestDmg.idx : 0;
      buffTapUntil = now + 220; // hold for 220ms to trigger buff cast
      const fm = buffCandidate.moves[0];
      if (fm) {
        const st = slotStats.get(fm.id) || { kills: 0, dmg: 0, combatSec: 0, lastCastT: 0, wasEvolved: !!fm.evolved };
        st.lastCastT = now;
        slotStats.set(fm.id, st);
      }
      switchSlot(buffCandidate.idx);
      return;
    }

    // 4. Rotate among competitive damaging weapons every rotate_s seconds (or stay on highest DPS weapon if rotate_s === 0)
    const minCompetitiveDps = (bestDmg && bestDmg.cSec >= 3.5) ? bestDmg.dps * 0.55 : 0;
    const dmgSlots = slotInfo.filter((s) => !s.isBuff && s.moves.length > 0 &&
      (s.idx === (bestDmg && bestDmg.idx) || s.cSec < 3.5 || s.dps >= minCompetitiveDps));
    if (!policy.rotate_s || dmgSlots.length <= 1) {
      if (bestDmg && bestDmg.idx !== player.active && (!currentInfo || currentInfo.cSec >= 3.5)) {
        switchSlot(bestDmg.idx);
      }
      return;
    }
    if ((now - lastSlotSwitchTime) / 1000 < policy.rotate_s) return;
    const curPos = dmgSlots.findIndex((s) => s.idx === player.active);
    const nextPos = (curPos + 1) % dmgSlots.length;
    switchSlot(dmgSlots[nextPos].idx);
  }

  // -------------------------------------------------------------
  // PURE LEARNING: perception + learned world model + reflex engine v3
  // Perception uses only what a player can SEE (positions, sizes, sprite names, ground
  // markings and how full they are, flying shots, pickup icons, map objects).
  // What is dangerous / valuable is LEARNED from my own HP and resource changes.
  // -------------------------------------------------------------
  const OBJ_LABEL = { altar: "stone pedestal with glowing orb", spring: "glowing pool", bigchest: "big golden chest", trap: "flat ground marking", chest: "small chest" };
  const BIN_EDGES = [0, 15, 35, 70];            // distance from the thing's edge (px): <0 overlap, 0-15, 15-35, 35-70
  const learned = { danger: {}, pickups: {}, kills: {}, objects: {} };  // from bridge (all past runs)
  const wdelta = { danger: {}, pickups: {}, kills: {}, seen: new Set() }; // this run's new observations
  const motion = { lastX: null, lastY: null, lastT: 0, speed: 110 };
  let exploreAngle = 0;
  let lastExploreShiftT = 0;
  const tracks = new WeakMap();                 // entity -> {x, y, t, vx, vy}
  const damageTracker = { lastHp: 0, bySource: {}, lastHitInfo: null,
    reset() { this.lastHp = 0; this.bySource = {}; this.lastHitInfo = null; } };

  function loadWorldModel(wm) {
    if (!wm) return;
    learned.danger = wm.danger || {}; learned.pickups = wm.pickups || {}; learned.kills = wm.kills || {}; learned.objects = wm.objects || {}; learned.terrain = wm.terrain || {};
  }
  function resetWorldDeltas() { wdelta.danger = {}; wdelta.pickups = {}; wdelta.kills = {}; wdelta.terrain = null; wdelta.seen = new Set(); }
  function worldDeltasPayload() { return { danger: wdelta.danger, pickups: wdelta.pickups, kills: wdelta.kills, terrain: wdelta.terrain || {}, seen: [...wdelta.seen] }; }

  function binOf(edge) { return edge < BIN_EDGES[0] ? 0 : edge < BIN_EDGES[1] ? 1 : edge < BIN_EDGES[2] ? 2 : edge < BIN_EDGES[3] ? 3 : -1; }
  function dcell(tab, key, b) { const row = tab[key]; return row && row[b] ? row[b] : [0, 0]; }
  function addDanger(key, b, exp, dmg) {
    const row = wdelta.danger[key] || (wdelta.danger[key] = [[0, 0], [0, 0], [0, 0], [0, 0]]);
    row[b][0] += exp; row[b][1] += dmg;
  }
  // learned damage rate (fraction of max HP per second) for being in distance-bin b of a thing
  function rawDangerRate(key, b) {
    if (b < 0) return 0;
    const fam = key.split(":")[0] + ":ANY";
    const a = dcell(learned.danger, key, b), d = dcell(wdelta.danger, key, b);
    const fa = dcell(learned.danger, fam, b), fd = dcell(wdelta.danger, fam, b);
    const exp = a[0] + d[0], dmg = a[1] + d[1];
    const fexp = fa[0] + fd[0], fdmg = fa[1] + fd[1];
    const famRate = fdmg / (fexp + 2);
    return (dmg + famRate * 3) / (exp + 3 + 2);   // species shrunk toward its family until it has its own data
  }
  function dangerRate(key, b) {
    if (b < 0) return 0;
    // Monotonicity: being closer (smaller bin) is never safer than being further away
    const r0 = rawDangerRate(key, b);
    const r1 = b < 3 ? rawDangerRate(key, b + 1) * 1.35 : 0;
    const r2 = b < 2 ? rawDangerRate(key, b + 2) * 1.7 : 0;
    return Math.max(r0, r1, r2);
  }
  function gainValue(g) {  // how much each kind of change is worth = Jev's policy weights
    return policy.w_level * (g.level || 0) + policy.w_bonds * (g.bonds || 0) + policy.w_coins * (g.coins || 0)
      + (policy.w_items || 45) * (g.items || 0)
      + policy.w_tickets * ((g.t1 || 0) + 10 * (g.t10 || 0)) + policy.w_hp * (g.hp || 0) + policy.w_xp * (g.xp || 0);
  }
  function learnedGains(tab, key) {
    const a = learned[tab][key], d = wdelta[tab][key];
    const n = (a ? a.n : 0) + (d ? d.n : 0);
    const avg = {};
    if (!n) return { n: 0, avg };
    for (const src of [a, d]) if (src) for (const [k, v] of Object.entries(src.sum || {})) avg[k] = (avg[k] || 0) + v;
    for (const k of Object.keys(avg)) avg[k] /= n;
    return { n, avg };
  }
  function learnedValue(tab, key) {
    const g = learnedGains(tab, key);
    return { n: g.n, v: g.n ? gainValue(g.avg) : 0 };
  }
  function addGain(tab, key, g, share = 1) {
    const e = wdelta[tab][key] || (wdelta[tab][key] = { n: 0, sum: {} });
    e.n += share;
    for (const [k, v] of Object.entries(g)) e.sum[k] = (e.sum[k] || 0) + v * share;
  }
  function statSnap(player) {
    const items = (runState.firstItemAnnounced || (player && player.item) ? 1 : 0) + (runState.items ? runState.items.length : 0);
    const slotBonds = (player && Array.isArray(player.slots))
      ? player.slots.reduce((acc, s) => acc + (Array.isArray(s) && s.length > 1 ? s.length - 1 : 0), 0)
      : 0;
    const bonds = Math.max(player.bonds || 0, slotBonds, runState.bondsCount || 0);
    return { hp: player.hp / (player.maxHp || 1), level: player.level, xp: (player.xp || 0) / (player.xpNext || 1),
             bonds, coins: player.coins || 0, t1: player.t1 || 0, t10: player.t10 || 0, items };
  }
  function diffSnap(a, b) {
    const g = {};
    for (const k of Object.keys(a)) g[k] = Math.round((b[k] - a[k]) * 1000) / 1000;
    if (g.level > 0) g.xp = (g.xp || 0) + g.level;  // level-ups wrap the xp bar
    return g;
  }

  function track(obj, now) {
    let tr = tracks.get(obj);
    if (!tr) { tr = { x: obj.x, y: obj.y, t: now, vx: 0, vy: 0, init: false }; tracks.set(obj, tr); return tr; }
    const dt = (now - tr.t) / 1000;
    if (dt > 0.001 && dt < 0.3) {
      const ivx = (obj.x - tr.x) / dt, ivy = (obj.y - tr.y) / dt;
      if (!tr.init) { tr.vx = ivx; tr.vy = ivy; tr.init = true; }
      else { tr.vx = tr.vx * 0.6 + ivx * 0.4; tr.vy = tr.vy * 0.6 + ivy * 0.4; }
    }
    tr.x = obj.x; tr.y = obj.y; tr.t = now;
    return tr;
  }
  function seen(key) { if (!wdelta.seen.has(key)) { wdelta.seen.add(key); } }

  // ---- what is visible right now (one list for danger learning and for the reflex engine)
  function perceive(player) {
    const now = performance.now();
    const px = player.x, py = player.y, pr = player.r || 14;
    const things = [];
    for (const e of PS.EnemyMgr ? PS.EnemyMgr.all() : []) {
      if (e.dead) continue;
      const d = Math.hypot(e.x - px, e.y - py);
      if (d > 380) continue;
      const tr = track(e, now);
      const key = `${e.boss || e.legend ? "boss" : "enemy"}:${e.name || e.dex}`;
      seen(key);
      things.push({ kind: "body", key, obj: e, x: e.x, y: e.y, vx: tr.vx, vy: tr.vy, R: (e.r || 12) + pr, d });
    }
    for (const hz of (PS.Hazards && PS.Hazards.all) || []) {
      const key = hz.kind === "zone" || hz.angle == null ? "mark:circle" : "mark:line";
      seen(key);
      // a marking that just went off stays visible a moment: still counts for learning, no longer for dodging
      things.push({ kind: "mark", key, hz, done: !!hz.hit, fill: Math.min(1, (hz.t || 0) / (hz.delay || 1)), tLeft: Math.max(0, (hz.delay || 1) - (hz.t || 0)) });
    }
    for (const p of (PS.Projectiles && PS.Projectiles.all ? PS.Projectiles.all() : [])) {
      if (p.friendly !== false || p.orb) continue;      // only shots flying from enemies (different colour on screen)
      if (Math.hypot(p.x - px, p.y - py) > 340) continue;
      seen("shot");
      things.push({ kind: "shot", key: "shot", x: p.x, y: p.y, vx: p.vx || 0, vy: p.vy || 0, R: (p.r || 7) + pr });
    }
    if (PS.World && PS.World.specialsNear) {
      for (const sp of PS.World.specialsNear(px, py, 420)) {
        const key = `object:${OBJ_LABEL[sp.type] || sp.type}`;
        seen(key);
        // Only actual hazard traps are treated as bodies in reflex avoidance;
        // passive interactive objects (springs, chests, altars) can be walked right over.
        if (sp.type === "trap") {
          things.push({ kind: "body", key, obj: sp, x: sp.x, y: sp.y - (sp.flat ? 8 : 0), vx: 0, vy: 0, R: (sp.r || 14) + pr * 0.5, d: Math.hypot(sp.x - px, sp.y - py), static: true });
        }
      }
    }
    return things;
  }

  function edgeNow(t, x, y, pr) {
    if (t.kind === "mark") return -hazardDepth(t.hz, x, y, pr);
    return Math.hypot(x - t.x, y - t.y) - t.R;
  }

  // ---- danger learning: exposure every frame, damage credited to what was touching me
  let lastPerceive = [];
  const binHistory = new Map();
  function learnDanger(player, things, dt) {
    const px = player.x, py = player.y, pr = player.r || 14;
    const nearest = new Map();
    const markFill = new Map();
    for (const t of things) {
      if (t.done) continue;
      const b = binOf(edgeNow(t, px, py, pr));
      if (b < 0) continue;
      if (!nearest.has(t.key) || b < nearest.get(t.key)) {
        nearest.set(t.key, b);
        if (t.kind === "mark") markFill.set(t.key, t.fill || 0);
      } else if (t.kind === "mark" && b === nearest.get(t.key)) {
        markFill.set(t.key, Math.max(markFill.get(t.key) || 0, t.fill || 0));
      }
    }
    if (PS.World && PS.World.isLiquid && PS.World.isLiquid(px, py)) nearest.set("terrain:liquid", 0);
    const nowT = performance.now() / 1000;
    for (const [key, b] of nearest) {
      const hist = binHistory.get(key) || [];
      hist.push([nowT, b]);
      while (hist.length && nowT - hist[0][0] > 0.9) hist.shift();
      binHistory.set(key, hist);
      // For ground markings, only accumulate exposure when the active telegraph is nearly full/charging
      const isMark = key.startsWith("mark:");
      const shouldAddExp = !isMark || ((markFill.get(key) || 0) >= 0.7 || dt === 0);
      if (shouldAddExp) {
        addDanger(key, b, dt, 0);
        if (key.startsWith("enemy:") || key.startsWith("boss:")) addDanger(key.split(":")[0] + ":ANY", b, dt, 0);
        if (isMark) addDanger("mark:ANY", b, dt, 0);
      }
    }
    // HP drop this frame?
    if (damageTracker.lastHp <= 0 || player.hp > damageTracker.lastHp) { damageTracker.lastHp = player.hp; return; }
    const loss = (damageTracker.lastHp - player.hp) / (player.maxHp || 1);
    damageTracker.lastHp = player.hp;
    if (loss <= 0) return;

    // Prioritize active attackers, shots, and telegraphs over passive background terrain or map objects
    const nonTerrainNearest = [...nearest].filter(([k]) => k !== "terrain:liquid" && (!k.startsWith("object:") || k.includes("trap") || k.includes("flat ground marking")));
    let cands = nonTerrainNearest.filter(([, b]) => b <= 1);
    if (!cands.length) cands = nonTerrainNearest.filter(([, b]) => b <= 2);
    // If nothing currently in b <= 2 (projectile/telegraph/trap detonated or despawned on impact!), check recent binHistory
    if (!cands.length) {
      for (const [k, hist] of binHistory) {
        if (k === "terrain:liquid" || (k.startsWith("object:") && !k.includes("trap") && !k.includes("flat ground marking"))) continue;
        const recent = hist.filter(([ht, hb]) => (nowT - ht) <= 0.4 && hb <= 3);
        if (recent.length) {
          cands.push([k, 0]);
        }
      }
    }
    // Only blame liquid terrain if genuinely no other hazard or attacker was near or recently hit
    if (!cands.length && nearest.has("terrain:liquid")) {
      cands = [["terrain:liquid", 0]];
    }
    if (!cands.length) cands = [["unknown", 0]];

    const wsum = cands.reduce((s, [, b]) => s + (b === 0 ? 3 : 1), 0);
    for (const [key, b] of cands) {
      const share = loss * (b === 0 ? 3 : 1) / wsum;
      addDanger(key, b, 0.1, share * 0.7);
      // temporal credit: the distance bins I was in during the last 0.9s led to this hit
      const hist = (binHistory.get(key) || []).filter(([, hb]) => hb > b);
      if (hist.length) {
        const wts = hist.map(([ht]) => Math.exp(-(nowT - ht) / 0.45));
        const tot = wts.reduce((x, y) => x + y, 0);
        hist.forEach(([, hb], i) => addDanger(key, hb, 0, share * 0.3 * wts[i] / tot));
      } else addDanger(key, b, 0, share * 0.3);
      if (key.startsWith("enemy:") || key.startsWith("boss:")) addDanger(key.split(":")[0] + ":ANY", b, 0, share);
      if (key.startsWith("mark:")) addDanger("mark:ANY", b, 0, share);
      damageTracker.bySource[key] = (damageTracker.bySource[key] || 0) + share * (player.maxHp || 1);
      damageTracker.lastHitInfo = { source: key };
    }
  }

  // ---- pickup & kill learning: what changed when I collected / killed something
  let prevPickups = new Set(), prevSnap = null;
  const pendingKills = [];
  let prevEnemies = new Set();
  const knownBosses = new Set();
  const defeatedBosses = new Set();
  function learnGains(player) {
    const now = PS.Game.st.time || 0;
    const snap = statSnap(player);
    const px = player.x, py = player.y;
    // pickups that vanished right next to me = collected
    const cur = new Set(PS.Pickups ? PS.Pickups.all() : []);
    if (prevSnap) {
      const got = [...prevPickups].filter((p) => !cur.has(p) && Math.hypot(p.x - px, p.y - py) < 60);
      if (got.length) {
        const g = diffSnap(prevSnap, snap);
        for (const p of got) { const key = `pickup:${p.kind}`; seen(key); addGain("pickups", key, g, 1 / got.length); }
        if (goal && goal.nearSnap) {
          goal.pickupGains = goal.pickupGains || {};
          for (const rk of ["level", "xp", "coins"]) {
            if (g[rk] > 0) goal.pickupGains[rk] = (goal.pickupGains[rk] || 0) + g[rk];
          }
        }
      }
    }
    prevPickups = cur;
    // enemies that died near me -> check gains 1.2s later
    const alive = new Set();
    for (const e of PS.EnemyMgr ? PS.EnemyMgr.all() : []) {
      const isBoss = !!(e.boss || e.legend);
      if (!e.dead) {
        alive.add(e);
        if (isBoss) knownBosses.add(e);
      } else {
        if (prevEnemies.has(e) && Math.hypot(e.x - px, e.y - py) < 220) {
          pendingKills.push({ key: `${isBoss ? "boss" : "enemy"}:${e.name || e.dex}`, t: now, snap });
        }
        if ((isBoss || knownBosses.has(e)) && !defeatedBosses.has(e)) {
          defeatedBosses.add(e);
          runState.bossKills = (runState.bossKills || 0) + 1;
          const bname = e.name || e.dex || "Boss";
          runState.bossesDefeated = runState.bossesDefeated || [];
          runState.bossesDefeated.push(bname);
          recordDecision(`⚔️ BOSS SLAIN: ${bname}! (Boss kills: ${runState.bossKills})`);
          console.log(`%c[Boss] Slayed ${bname}! Total boss kills: ${runState.bossKills}`, "color:#e056fd;font-weight:bold;");
        }
      }
    }
    // Also check if any knownBoss vanished from EnemyMgr while we were tracking it
    for (const b of knownBosses) {
      if (!alive.has(b) && !defeatedBosses.has(b)) {
        defeatedBosses.add(b);
        runState.bossKills = (runState.bossKills || 0) + 1;
        const bname = b.name || b.dex || "Boss";
        runState.bossesDefeated = runState.bossesDefeated || [];
        runState.bossesDefeated.push(bname);
        recordDecision(`⚔️ BOSS SLAIN: ${bname}! (Boss kills: ${runState.bossKills})`);
        console.log(`%c[Boss] Slayed ${bname}! Total boss kills: ${runState.bossKills}`, "color:#e056fd;font-weight:bold;");
      }
    }
    prevEnemies = alive;
    while (pendingKills.length && now - pendingKills[0].t > 1.2) {
      const batch = pendingKills.filter((k) => Math.abs(k.t - pendingKills[0].t) < 0.25);
      const g = diffSnap(batch[0].snap, snap);
      delete g.hp; delete g.xp; delete g.level;   // xp/levels come from the gem pickups, not the kill itself
      for (const k of batch) addGain("kills", k.key, g, 1 / batch.length);
      pendingKills.splice(0, batch.length);
    }
    prevSnap = snap;
  }

  function trackDamage(player) {
    const now = performance.now();
    const dt = lastLearnT ? Math.min(0.1, (now - lastLearnT) / 1000) : 0;
    lastLearnT = now;
    lastPerceive = perceive(player);
    learnDanger(player, lastPerceive, dt);
    learnGains(player);
  }
  let lastLearnT = 0;

  // ---- reflex engine v3: lookahead scored with LEARNED danger rates
  function inLiquid(x, y) { return !!(PS.World && PS.World.isLiquid && PS.World.isLiquid(x, y)); }
  let stuckFrames = 0;
  let unstickUntil = 0, unstickAngle = 0, unstickX = 0, unstickY = 0;
  let anchorX = null, anchorY = 0, anchorT = 0;
  function updateMotion(player) {
    const now = performance.now();
    if (motion.lastX !== null) {
      const dt = (now - motion.lastT) / 1000;
      const moving = activeKeys.KeyW || activeKeys.KeyA || activeKeys.KeyS || activeKeys.KeyD;
      if (moving && dt > 0 && dt < 0.2) {
        const v = Math.hypot(player.x - motion.lastX, player.y - motion.lastY) / dt;
        if (v > 5 && v < 600) {
          stuckFrames = 0;
          const wet = inLiquid(player.x, player.y);
          const t = wdelta.terrain || (wdelta.terrain = { land_v: 0, land_n: 0, liquid_v: 0, liquid_n: 0 });
          if (wet) { t.liquid_v += v; t.liquid_n++; } else { t.land_v += v; t.land_n++; motion.speed = motion.speed * 0.95 + v * 0.05; }
        } else if (v < 5) {
          stuckFrames++;
          if (stuckFrames >= 12) {
            stuckFrames = 0;
            releaseAllKeys();
            exploreAngle = (exploreAngle || 0) + 1.25;
            lastExploreShiftT = now;
            // I am pressing into something solid: commit to walking another way until I have moved away
            unstickAngle = exploreAngle;
            unstickUntil = now + 4000;
            unstickX = player.x; unstickY = player.y;
            runState.stuckEvents = (runState.stuckEvents || 0) + 1;
          }
        }
      } else {
        stuckFrames = 0;
      }
    }
    motion.lastX = player.x; motion.lastY = player.y; motion.lastT = now;
    // Net-progress check: jittering on the spot (e.g. along a ledge) counts as stuck too
    if (anchorX === null) {
      anchorX = player.x; anchorY = player.y; anchorT = now;
    } else if (now - anchorT >= 1200) {
      const movingNow = activeKeys.KeyW || activeKeys.KeyA || activeKeys.KeyS || activeKeys.KeyD;
      if (movingNow && now >= unstickUntil && Math.hypot(player.x - anchorX, player.y - anchorY) < 18) {
        exploreAngle = (exploreAngle || 0) + 1.25;
        lastExploreShiftT = now;
        unstickAngle = exploreAngle;
        unstickUntil = now + 4000;
        unstickX = player.x; unstickY = player.y;
        runState.stuckEvents = (runState.stuckEvents || 0) + 1;
      }
      anchorX = player.x; anchorY = player.y; anchorT = now;
    }
  }
  // learned: my speed in liquid relative to land (1.0 until observed)
  function liquidSpeedRatio() {
    const a = learned.terrain || {}, d = wdelta.terrain || {};
    const ln = (a.land_n || 0) + (d.land_n || 0), qn = (a.liquid_n || 0) + (d.liquid_n || 0);
    if (ln < 30 || qn < 15) return 1.0;
    return Math.max(0.2, Math.min(1.5, (((a.liquid_v || 0) + (d.liquid_v || 0)) / qn) / (((a.land_v || 0) + (d.land_v || 0)) / ln)));
  }
  function hazardDepth(hz, x, y, pr) {   // geometry of the marking as drawn on screen
    if (hz.kind === "zone" || hz.angle == null) return (hz.r || 40) + 0.6 * pr + 6 - Math.hypot(x - hz.x, y - hz.y);
    const c = Math.cos(hz.angle), s = Math.sin(hz.angle), dx = x - hz.x, dy = y - hz.y, along = dx * c + dy * s;
    if (along < -pr - 6 || along > (hz.len || 360) + pr + 6) return -999;
    return (hz.w || 22) / 2 + 0.6 * pr + 6 - Math.abs(-dx * s + dy * c);
  }
  function walkable(x, y, pr, px, py) {
    // 4th arg = liquid counts as passable; if player is currently overlapping a collider/teleport spot, do not lock all 24 directions
    if (PS.World && PS.World.isFree) {
      const curFree = (px === undefined || py === undefined) ? true : PS.World.isFree(px, py, pr, true);
      if (curFree && !PS.World.isFree(x, y, pr, true)) return false;
    }
    if (PS.World && PS.World.arena) {
      const a = PS.World.arena, T = 32;
      const acx = a.tx * T + T / 2, acy = a.ty * T + T / 2;
      const maxR = (a.r || 20) * T * 0.9;
      const newDist = Math.hypot(x - acx, y - acy);
      if (newDist > maxR) {
        const curDist = (px !== undefined && py !== undefined) ? Math.hypot(px - acx, py - acy) : 0;
        // If player is already outside 0.9*R (e.g. after teleporting out of the Rift or near arena edge), allow moving inward or freely if outside 1.05*R
        if (curDist <= maxR) return false;
        if (curDist <= (a.r || 20) * T * 1.05 && newDist > curDist) return false;
      }
    }
    return true;
  }
  const K_DANGER = 1800;
  function costAt(t, x, y, h, pr, W) {
    if (t.done) return 0;
    const px0 = motion.lastX ?? x, py0 = motion.lastY ?? y;
    let edge;
    if (t.kind === "mark") {
      const tau = (t.tLeft > 0 && t.tLeft < h) ? t.tLeft : h;
      const frac = h > 1e-4 ? (tau / h) : 1.0;
      const mx = px0 + (x - px0) * frac, my = py0 + (y - py0) * frac;
      edge = -hazardDepth(t.hz, mx, my, pr);
    } else if (t.kind === "shot") {
      const pvx = h > 1e-4 ? (x - px0) / h : 0, pvy = h > 1e-4 ? (y - py0) / h : 0;
      const rvx = t.vx - pvx, rvy = t.vy - pvy;
      const rv2 = rvx * rvx + rvy * rvy;
      let rx, ry;
      if (rv2 > 1e-4) {
        const u = ((px0 - t.x) * rvx + (py0 - t.y) * rvy) / rv2;
        const uClamped = Math.max(0, Math.min(h, u));
        rx = (t.x - px0) + rvx * uClamped;
        ry = (t.y - py0) + rvy * uClamped;
      } else {
        rx = t.x - x;
        ry = t.y - y;
      }
      edge = Math.hypot(rx, ry) - t.R;
    } else {
      const step = t.static ? 0 : Math.min(1, h);
      edge = Math.hypot(x - (t.x + t.vx * h * step), y - (t.y + t.vy * h * step)) - t.R - W.kite * 0.5;
    }
    const b = binOf(edge);
    if (b < 0) return 0;
    let r = dangerRate(t.key, b);
    if (t.kind === "mark") {
      r *= (0.75 + 0.75 * (t.fill || 0.5));
      // A filling marking hurts in one burst when it completes, not per-second:
      // a step that is still inside at that moment (or too deep to escape in remaining time) must cost like a burst.
      const remT = Math.max(0, t.tLeft - h);
      const inescapable = b === 0 && (-edge) > remT * (motion.speed || 110) * 0.95;
      if (t.tLeft <= h || inescapable) r *= (b === 0 ? 5.0 : 1.6);
      else if (t.tLeft <= h + 0.35) r *= 1.6;
    }
    // When inside a hazard (b === 0, edge < 0), depth increases linearly without a cap so bot has a steep exit gradient
    let within = 0;
    if (b === 0) {
      within = 1.0 + Math.max(0, -edge) / 10.0;
    } else {
      const nextEdge = BIN_EDGES[b], prevEdge = BIN_EDGES[b - 1];
      within = Math.max(0, Math.min(1, (nextEdge - edge) / Math.max(1, nextEdge - prevEdge)));
    }
    return K_DANGER * r * (0.6 + 0.8 * within) * (t.kind === "mark" ? W.hzw : W.eva);
  }

  let orbitHysteresisSign = 1;
  let lastDirX = 0, lastDirY = 0;
  let lastOrbitSwitchT = 0;

  function computeOptimalVelocity(player) {
    updateMotion(player);
    const A = activeAdaptations, pr = player.r || 14, px = player.x, py = player.y, sp = motion.speed;
    const W = {
      eva: A.evasion_weight_mult || 1,
      hzw: A.hazard_avoidance_weight || 1,
      kite: A.kite_distance_boost || 0,
      greed: A.pickup_mult || 1.8,
      agg: A.aggression || 1.5,
      range: A.combat_range || 110,
      orbit: A.orbit || 1.3,
    };
    const things = lastPerceive;

    // 1. Pickups: value = what I measured when I collected each kind before; unfamiliar ones -> curiosity
    const targets = [];
    const hpFrac = player.hp / (player.maxHp || 1);
    for (const p of PS.Pickups ? PS.Pickups.all() : []) {
      const d = Math.hypot(p.x - px, p.y - py);
      if (d > 850) continue;
      // Do not get baited by pickups currently sitting inside an active dangerous ground marking
      const insideMark = things.some((t) => t.kind === "mark" && !t.done && edgeNow(t, p.x, p.y, pr) < 12 && dangerRate(t.key, 0) > 0.005);
      if (insideMark) continue;
      const key = `pickup:${p.kind}`;
      const lg = learnedGains("pickups", key);
      let v = lg.n ? gainValue(lg.avg) : 0;
      // HP I cannot hold is worth nothing: discount the HP part by how full I am.
      if (lg.n && (lg.avg.hp || 0) > 0) v -= policy.w_hp * lg.avg.hp * hpFrac;
      const healThr = A.heal_seek_threshold ?? 0.65;
      if (lg.n && hpFrac < healThr && (lg.avg.hp || 0) > 0) {
        // When hurt, what heals me matters more the lower my HP is: in my own
        // history, runs that recovered after dropping low lived far longer.
        v += policy.w_hp * lg.avg.hp * 3 * (1 + 4 * (healThr - hpFrac) / healThr);
      }
      const curious = lg.n < 5;                                              // rarely picked up: go see what it does
      // Measured gains are small fractions; scale them into target-value range so
      // pickup kinds can outrank each other (a flat floor made them all equal).
      const baseV = curious ? 120 : Math.max(6, v * 10);
      const special = curious || baseV >= 60;
      targets.push({ x: p.x, y: p.y, V: baseV * W.greed + 4, d0: d, isSpecial: special });
    }

    const inTelegraph = things.some((t) => !t.done && (
      (t.kind === "mark" && edgeNow(t, px, py, pr) < 25 && dangerRate(t.key, 0) > 0.005) ||
      (t.kind === "shot" && edgeNow(t, px, py, pr) < 18 && dangerRate("shot", 0) > 0.005)
    ));
    const activeMapGoal = goal && !(goal.ref && goal.ref.portal && goal.entered) && !(goal.postDisappearT > 0);
    const goalDist = activeMapGoal ? Math.hypot(goal.x - px, goal.y - py) : 9999;
    if (activeMapGoal && !inTelegraph) {
      // High-priority goal pull (chests, altars, rifts)
      targets.push({ x: goal.x, y: goal.y, V: Math.max(300, 750 - goalDist * 0.4), d0: goalDist, isGoal: true });
    }
    // Escape mode: after finding myself stuck, walk ONE heading until the way to what I want
    // is no longer blocked by solid ground (normal attractions are muted in evalDir meanwhile,
    // or they just pull me back to the same spot)
    let unsticking = performance.now() < unstickUntil;
    if (unsticking) {
      const displaced = Math.hypot(px - unstickX, py - unstickY);
      const main = targets.filter((t) => !t.isExplore).sort((a, b) => b.V - a.V)[0];
      let clear = displaced >= 120;
      if (main && displaced >= 40) {
        const md = Math.max(1, Math.hypot(main.x - px, main.y - py));
        const mux = (main.x - px) / md, muy = (main.y - py) / md;
        clear = [0.12, 0.3, 0.6].every((h) => walkable(px + mux * sp * h, py + muy * sp * h, pr, px, py));
      }
      if (clear) { unsticking = false; unstickUntil = 0; }
      else targets.push({ x: px + Math.cos(unstickAngle) * 300, y: py + Math.sin(unstickAngle) * 300, V: 500, d0: 300, isUnstick: true });
    }
    if (PS.Rift && PS.Rift.inArena && !inTelegraph && PS.World && PS.World.specialsNear) {
      for (const sp of PS.World.specialsNear(px, py, 650)) {
        if (!sp.used && sp.type !== "trap") {
          const sd = Math.hypot(sp.x - px, sp.y - py);
          targets.push({ x: sp.x, y: sp.y, V: 850, d0: sd, isGoal: true });
        }
      }
    }

    // Active Roaming Exploration: if no active goal and no nearby pickups, drift across arena to find chests & portals
    const hasNearbyPickups = targets.some((t) => !t.isExplore && t.d0 < 160);
    if (!activeMapGoal && !hasNearbyPickups) {
      const nowMs = performance.now();
      if (nowMs - lastExploreShiftT > 10000) {
        let candidate = exploreAngle + 0.55;
        if (PS.World && PS.World.arena) {
          const a = PS.World.arena, T = 32;
          const acx = a.tx * T + T / 2, acy = a.ty * T + T / 2;
          const dCenter = Math.hypot(px - acx, py - acy);
          const maxR = (a.r || 20) * T * 0.75;
          if (dCenter > maxR && dCenter <= (a.r || 20) * T * 1.1) {
            candidate = Math.atan2(acy - py, acx - px);
          }
        }
        exploreAngle = candidate;
        lastExploreShiftT = nowMs;
      }
      const ex = px + Math.cos(exploreAngle) * 380;
      const ey = py + Math.sin(exploreAngle) * 380;
      targets.push({ x: ex, y: ey, V: 25 * (policy.roam ?? 1) * (policy.explore ? 1.6 : 0.8), d0: 380, isExplore: true, roam: policy.roam ?? 1 });
    }

    // 2. Combat Engagement & Tangential Swarm Orbiting (for things observed to be dangerous)
    const mobileEnemies = things.filter((t) => t.kind === "body" && !t.static && t.d < 380 &&
      (dangerRate(t.key, 0) > 0.005 || dangerRate((t.key || "").split(":")[0] + ":ANY", 0) > 0.005));
    let nearestEnemyEdge = 999;
    let packCentroid = null;
    let tangentVec = null;

    if (mobileEnemies.length > 0) {
      mobileEnemies.sort((a, b) => a.d - b.d);
      const nearest = mobileEnemies[0];
      nearestEnemyEdge = nearest.d - (nearest.R || 14) - pr;

      // Centroid of local enemy swarm within 250px
      let sx = 0, sy = 0, sc = 0;
      for (const e of mobileEnemies) {
        if (e.d < 250) { sx += e.x; sy += e.y; sc++; }
      }
      const cx = sc ? sx / sc : nearest.x, cy = sc ? sy / sc : nearest.y;
      const cdist = Math.hypot(px - cx, py - cy);
      packCentroid = { x: cx, y: cy, d: cdist };

      // If enemies are far away (> combat_range + 20px), pull into combat range to fight and farm
      if (nearestEnemyEdge > W.range + 20) {
        const ux_e = (cx - px) / Math.max(1, cdist), uy_e = (cy - py) / Math.max(1, cdist);
        targets.push({
          x: px + ux_e * Math.max(0, cdist - W.range),
          y: py + uy_e * Math.max(0, cdist - W.range),
          V: 45 * W.agg,
          d0: Math.abs(cdist - W.range),
        });
      }

      // If enemies are in combat distance (30px to range + 70px), compute tangent for circling/kiting
      if (nearestEnemyEdge >= 30 && cdist > 15) {
        const rx = (px - cx) / cdist, ry = (py - cy) / cdist;
        const t1 = { x: -ry, y: rx }, t2 = { x: ry, y: -rx };

        const now = performance.now();
        if (activeMapGoal) {
          // When pursuing a goal, choose the orbit tangent that moves TOWARDS the goal
          const gx = (goal.x - px) / Math.max(1, Math.hypot(goal.x - px, goal.y - py));
          const gy = (goal.y - py) / Math.max(1, Math.hypot(goal.x - px, goal.y - py));
          const dot1 = gx * t1.x + gy * t1.y;
          const dot2 = gx * t2.x + gy * t2.y;
          orbitHysteresisSign = dot1 >= dot2 ? 1 : -1;
          lastOrbitSwitchT = now;
        } else if (now - lastOrbitSwitchT > 1200) {
          let score1 = 0, score2 = 0;
          for (const tg of targets) {
            score1 += (tg.x - px) * t1.x + (tg.y - py) * t1.y;
            score2 += (tg.x - px) * t2.x + (tg.y - py) * t2.y;
          }
          orbitHysteresisSign = score1 >= score2 ? 1 : -1;
          lastOrbitSwitchT = now;
        }

        tangentVec = orbitHysteresisSign > 0 ? t1 : t2;
      }
    }

    const H = [0.12, 0.3, 0.6], HW = [1.0, 0.75, 0.45];
    // Look as far as the moment a nearby filling marking completes, so leaving
    // early enough can be told apart from leaving too late.
    let burstH = 0;
    for (const t of things) {
      if (t.kind === "mark" && !t.done && t.tLeft > 0.6 && t.tLeft <= 1.6 && edgeNow(t, px, py, pr) < 35) burstH = Math.max(burstH, t.tLeft);
    }
    if (burstH) { H.push(burstH); HW.push(0.6); }
    const evalDir = (ux, uy, ignoreWalk = false) => {
      let cost = 0, fx = px, fy = py;
      const wetRatio = liquidSpeedRatio();
      let travelled = 0, lastH = 0, cx = px, cy = py;
      for (let k = 0; k < H.length; k++) {
        const segSp = inLiquid(cx, cy) ? sp * wetRatio : sp;
        travelled += segSp * (H[k] - lastH); lastH = H[k];
        const x = px + ux * travelled, y = py + uy * travelled;
        cx = x; cy = y;
        if (!ignoreWalk && (ux || uy) && !walkable(x, y, pr, px, py)) { if (k === 0) return -Infinity; cost += 250 * HW[k]; break; }
        fx = x; fy = y;
        if (PS.World && PS.World.isLiquid && PS.World.isLiquid(x, y)) {
          const slowPenalty = (1.0 - Math.min(1.0, wetRatio)) * 320;
          cost += (K_DANGER * dangerRate("terrain:liquid", 0) + slowPenalty) * HW[k];
        }
        for (const t of things) cost += HW[k] * costAt(t, x, y, H[k], pr, W);
      }

      // Only reward progress I can actually make: past solid ground (fx, fy) there is none,
      // so a direction into a wall must not score as if I could walk through it.
      const reach = Math.hypot(fx - px, fy - py);
      let gain = 0;
      for (const tg of targets) {
        if (unsticking && !tg.isUnstick) continue;
        let dmin = tg.d0;
        for (const h of [0.06, 0.12, 0.3]) {
          const stepD = Math.min(sp * h, reach);
          dmin = Math.min(dmin, Math.hypot(px + ux * stepD - tg.x, py + uy * stepD - tg.y));
        }
        if (tg.isGoal || tg.isUnstick) {
          const progress = tg.d0 - dmin;
          gain += (progress / Math.max(1, sp * 0.3)) * 65 + (1 / (1 + dmin / 60)) * 85;
        } else if (tg.isSpecial) {
          const progress = tg.d0 - dmin;
          gain += (progress / Math.max(1, sp * 0.3)) * 28 + tg.V * (1 / (1 + dmin / 120) - 1 / (1 + tg.d0 / 120));
        } else if (tg.isExplore) {
          const progress = tg.d0 - dmin;
          gain += (progress / Math.max(1, sp * 0.3)) * 8 * (tg.roam || 1);
        } else {
          gain += tg.V * (1 / (1 + dmin / 140) - 1 / (1 + tg.d0 / 140));
        }
      }

      // Combat Orbit bonus: reward tangential movement around the pack at combat distance (disabled when dodging telegraphs)
      let combatBonus = 0;
      if (!inTelegraph && !unsticking && tangentVec && (ux || uy)) {
        const dotT = ux * tangentVec.x + uy * tangentVec.y;
        combatBonus += dotT * 35 * W.orbit * W.agg;

        if (packCentroid) {
          const fd = Math.min(sp * 0.3, reach);
          const futureDist = Math.hypot(px + ux * fd - packCentroid.x, py + uy * fd - packCentroid.y);
          const distErr = Math.abs(futureDist - W.range);
          combatBonus += Math.max(0, 25 - distErr * 0.4) * W.agg;
        }
        // When stepping onto a nearby goal (<120px), dampen orbit pull so we touch the object directly
        if (activeMapGoal && goalDist < 120) combatBonus *= 0.2;
      }

      const gainMult = inTelegraph ? 0.2 : 4.0;
      return (gain * gainMult + combatBonus) - cost;
    };

    let bestS = evalDir(0, 0) - 3, bvx = 0, bvy = 0;
    for (let c = 0; c < 24; c++) {
      const ang = (c / 24) * 2 * Math.PI, ux = Math.cos(ang), uy = Math.sin(ang);
      let s = evalDir(ux, uy, false);
      // Heading inertia: keep walking the way I just chose unless another way is clearly better,
      // so near-ties (e.g. either way along a ledge) do not flip-flop every frame.
      if (s !== -Infinity && (lastDirX || lastDirY)) s += 8 * (ux * lastDirX + uy * lastDirY);
      if (s > bestS) { bestS = s; bvx = ux; bvy = uy; }
    }
    if (bvx === 0 && bvy === 0) {
      let fbBest = -Infinity;
      for (let c = 0; c < 24; c++) {
        const ang = (c / 24) * 2 * Math.PI, ux = Math.cos(ang), uy = Math.sin(ang);
        let s = evalDir(ux, uy, true);
        if (s > fbBest) { fbBest = s; bvx = ux; bvy = uy; }
      }
    }
    lastDirX = bvx; lastDirY = bvy;
    return [bvx, bvy];
  }

  // -------------------------------------------------------------
  // Icon-based identification (language independent: the /en/ UI
  // translates titles, but icons are generated from internal ids)
  // -------------------------------------------------------------
  const STAT_ICON = { heart: "hp", fist: "atk", boot: "spd", clock: "cd", shield: "def", sprout: "rgn", magnet: "mag", book: "xp", berry: "heal" };
  let iconIndex = null;
  function buildIconIndex() {
    if (iconIndex || !PS || !PS.Icons || !PS.Icons.url) return iconIndex;
    const idx = new Map();
    const add = (name, info) => { try { idx.set(PS.Icons.url(name), info); } catch (e) {} };
    for (const [icon, id] of Object.entries(STAT_ICON)) add(icon, { kind: "stat", id });
    for (const m of (PS.Moves && PS.Moves.ALL) || []) add("move:" + m.id, { kind: "move", id: m.id });
    for (const it of (PS.Items && PS.Items.LIST) || []) add(it.id, { kind: "item", id: it.id });
    iconIndex = idx;
    return idx;
  }
  function identifyEl(el) {
    const idx = buildIconIndex();
    const img = el && el.querySelector(".ico img, img");
    if (!idx || !img) return null;
    return idx.get(img.getAttribute("src")) || null;
  }
  function describeLevelCard(el, player) {
    const title = ((el.querySelector(".nm") || {}).textContent || "").trim();
    const desc = ((el.querySelector(".ds") || {}).textContent || "").trim();
    const tagEl = el.querySelector(".tag-chip");
    const tag = tagEl ? tagEl.textContent.trim() : "";

    const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

    const hasArrow = /→|->/.test(title);
    const isTrueEvo = hasArrow || (el.classList.contains("evo") && !/evolves/i.test(title)) || /\bEVOLUTION\b/i.test(title) || /^evolution$/i.test(tag);

    let type = "unknown", id = slug(title);
    let evolvesHint = null;

    // Check for "Evolves <Move>" hint in title or tag (e.g. "Power Evolves Dragon Rage")
    const evoHintMatch = title.match(/^(.*?)\s+evolves\s+(.*)$/i) || tag.match(/^evolves\s+(.*)$/i);
    if (!hasArrow && evoHintMatch) {
      type = "stat";
      const statName = evoHintMatch[1] ? evoHintMatch[1].trim() : title.replace(/\s+Upgrade/i, "").trim();
      id = slug(statName);
      evolvesHint = slug(evoHintMatch[2] ? evoHintMatch[2].trim() : (tag.match(/^evolves\s+(.*)$/i) ? tag.match(/^evolves\s+(.*)$/i)[1].trim() : ""));
    } else if (isTrueEvo) {
      type = "evo";
      const cleanTitle = title.replace(/\s+EVOLUTION/i, "").trim();
      id = slug(cleanTitle);
    } else if (/nv\.|lv\.|>/i.test(tag) || /nv\.|lv\.|>/i.test(title)) {
      type = "up";
      const base = title.split(/\s+[NL]v\./i)[0].trim();
      id = slug(base);
    } else if (/new/i.test(tag) || /new/i.test(title)) {
      type = "new";
      const base = title.split(/\s+New/i)[0].trim();
      id = slug(base);
    } else if (/upgrade/i.test(title) || /upgrade/i.test(tag)) {
      type = "stat";
      const base = title.replace(/\s+Upgrade/i, "").trim();
      id = slug(base);
    } else {
      const info = identifyEl(el);
      if (info && info.kind === "stat") {
        type = "stat";
        id = info.id;
      } else if (info && info.kind === "move") {
        id = info.id;
        const mine = player && player.moves && player.moves.find((m) => m.id === info.id);
        type = isTrueEvo ? "evo" : (mine ? "up" : "new");
      }
    }

    const res = { type, id, key: `${type}:${id}`, title, tag, desc };
    if (evolvesHint) res.evolves_hint = evolvesHint;
    return res;
  }

  // -------------------------------------------------------------
  // Curiosity / investigation engine (pure): go look at things I see, measure what happened
  // -------------------------------------------------------------
  let goal = null;
  let lastOppScan = 0;
  const oppDecided = new Map();
  const oppAsking = new Set();
  const screenMessages = [];   // on-screen announcement text (read like a player would)

  function recentMessages() { return screenMessages.slice(-4).map((m) => m.text); }

  const SUCCESS_INTERACTIONS = new Set([
    "came back from the other place",
    "it changed when I touched it",
    "it disappeared after I reached it",
    "stood next to it for 6s"
  ]);

  function isOppCooling(id) {
    const dec = oppDecided.get(id);
    return !!(dec && performance.now() < dec.exp);
  }

  function finishGoal(why) {
    if (!goal) return;
    const player = PS.Game.player;
    let reached = SUCCESS_INTERACTIONS.has(why);
    const minH = goal.nearMinHp !== undefined ? goal.nearMinHp : goal.minHp;
    const startH = (goal.nearSnap ? goal.nearSnap.hp : goal.start.hp);
    const hpCost = Math.round(Math.max(0, startH - minH) * 100) / 100;
    if (reached) {
      const start = goal.nearSnap || goal.start;
      const end = player ? statSnap(player) : start;
      const reward = diffSnap(start, end);
      // Only keep ground pickup gains when a non-damaging object disappeared upon touch (chests drop loot on the spot when broken) or on portal return
      const isChestDrop = why === "it disappeared after I reached it" && hpCost < 0.05;
      if (goal.pickupGains && !isChestDrop && why !== "came back from the other place") {
        for (const rk of ["level", "xp", "coins"]) {
          if (goal.pickupGains[rk]) {
            reward[rk] = Math.max(0, Math.round((reward[rk] - goal.pickupGains[rk]) * 1000) / 1000);
          }
        }
      }
      // If the object neither disappeared nor changed state (stood next to it for 6s), XP/coins came from fighting
      if (why === "stood next to it for 6s") {
        reward.level = 0;
        reward.xp = 0;
        reward.coins = 0;
      }
      const tNow = Math.round(PS.Game.st.time || 0);
      const tTouch = goal.nearT || goal.t0;
      const touchMsgs = screenMessages
        .filter((m) => m.t >= tTouch - 1 && m.t <= tNow + 1 && !/enfurecido|escapado/i.test(m.text))
        .map((m) => m.text);
      if (touchMsgs.some((m) => /llevas|holding|equipped/i.test(m)) && hpCost < 0.05) {
        reward.items = Math.max(reward.items || 0, 1);
      }
      if (touchMsgs.some((m) => /vínculo|movimientos a la vez/i.test(m))) {
        reward.bonds = Math.max(reward.bonds || 0, 1);
      }
      // Standing on a healing pool at full HP (>=92%) where nothing changed is a full-HP no-op, not a meaningful trial
      if (why === "stood next to it for 6s" && startH >= 0.92 && (reward.hp || 0) <= 0.01 &&
          !(reward.items || reward.bonds || reward.t1 || reward.t10) && touchMsgs.length === 0) {
        reached = false;
      }
      if (reached) {
        const out = { type: goal.key, why, died: !!(player && player.dead), reached: true, reward,
          messages: touchMsgs,
          hp_cost: hpCost,
          time_cost: Math.round((PS.Game.st.time || 0) - tTouch), t: tNow };
        if (runState.active) (runState.opportunities = runState.opportunities || []).push(out);
        callJevBridge("JEV_OPP_OUTCOME", out, 4000).then((res) => {
          if (res && res.data && res.data.evidence) lastLessonText = res.data.evidence;
        });
      }
    }
    if (goal.id) {
      oppDecided.set(goal.id, { engage: false, exp: performance.now() + 60000 });
    }
    if (goal.ref && goal.ref.portal && PS.Rift && PS.Rift.rift) {
      const r = PS.Rift.rift;
      oppDecided.set(`portal:${Math.round(r.x)},${Math.round(r.y)}`, { engage: false, exp: performance.now() + 90000 });
    }
    releaseAllKeys();
    recordDecision(`🔬 ${goal.key.replace("object:", "")}: ${why}`);
    goal = null;
  }

  function askEngage(key, id, x, y, player, ref) {
    if (isOppCooling(id) || oppAsking.has(id) || goal) return;
    oppAsking.add(id);
    const near = lastPerceive.filter((t) => t.kind === "body" && !t.static && t.d < 140).length;
    callJevBridge("JEV_OPPORTUNITY", { type: key, t: Math.round(PS.Game.st.time || 0), hp_pct: player.hp / (player.maxHp || 1),
      level: player.level, distance: Math.hypot(x - player.x, y - player.y), enemies_near: near, messages: recentMessages(),
      explore: !!policy.explore }, 3500)
      .then((res) => {
        oppAsking.delete(id);
        const engage = !!(res && res.data && res.data.engage);
        oppDecided.set(id, { engage, exp: performance.now() + (engage ? 40000 : 15000) });
        if (engage && !goal && PS.Game.player && !PS.Game.player.dead) {
          const p = PS.Game.player;
          goal = { key, id, x, y, ref, t0: PS.Game.st.time || 0, start: statSnap(p), minHp: p.hp / (p.maxHp || 1), stayT: 0 };
          recordDecision(`🔎 Investigate ${key.replace("object:", "")} (${res.data.source})`);
        }
      });
  }

  function scanOpportunities(player) {
    const now = performance.now();
    if (now - lastOppScan < 500) return;
    lastOppScan = now;
    if (goal || oppAsking.size > 0) return;
    if (PS.Rift && PS.Rift.inArena) return;

    const hpPct = player.hp / (player.maxHp || 1);
    const candidates = [];
    if (PS.Rift && PS.Rift.rift) {
      const r = PS.Rift.rift;
      seen("object:purple portal");
      const id = `portal:${Math.round(r.x)},${Math.round(r.y)}`;
      if (!isOppCooling(id)) {
        candidates.push({ key: "object:purple portal", id, x: r.x, y: r.y - 10, ref: { portal: true }, dist: Math.hypot(r.x - player.x, r.y - player.y) });
      }
    }
    if (PS.World && PS.World.specialsNear) {
      for (const sp of PS.World.specialsNear(player.x, player.y, 1800)) {
        if (sp.used) continue;  // looks dimmed / spent on screen
        const key = `object:${OBJ_LABEL[sp.type] || sp.type}`;
        const id = sp.id !== undefined ? `obj:${sp.id}` : `obj:${sp.type}:${Math.round(sp.x)},${Math.round(sp.y)}`;
        if (isOppCooling(id)) continue;
        const objExp = learned.objects && learned.objects[key];
        const ag = (objExp && objExp.avg_gain) || {};
        const hasBuffMsg = !!(objExp && (objExp.positive_rate || 0) >= 0.35 && (objExp.avg_hp_cost || 0) < 0.06 &&
                              objExp.messages && objExp.messages.some((m) => /poder|%|durante/i.test(m)));
        const hasValuableReward = (ag.items || 0) >= 0.15 || (ag.bonds || 0) >= 0.08 || hasBuffMsg;
        // If an object has been tried >= 3 times and learned to be a net hazard with no valuable rewards, skip pursuing it
        if (objExp && objExp.tried >= 3 && (objExp.avg_hp_cost || 0) >= 0.05 && !hasValuableReward) continue;
        // If an object has been tried >= 3 times and observed to have no non-HP rewards or buff messages, skip when HP >= 85%
        if (objExp && objExp.tried >= 3 && hpPct >= 0.85) {
          const hasNonHpReward = hasValuableReward || (ag.t1 || 0) >= 0.05 || (ag.t10 || 0) > 0 || (ag.level || 0) > 0.1;
          if (!hasNonHpReward) continue;
        }
        // If an object has been tried >= 5 times and confirmed to yield negligible reward, skip pursuing it locally
        if (objExp && objExp.tried >= 5 && (objExp.positive_rate || 0) <= 0.20 && !hasValuableReward) continue;
        const dist = Math.hypot(sp.x - player.x, sp.y - player.y);
        candidates.push({ key, id, x: sp.x, y: sp.y - (sp.flat ? 8 : 0), ref: { sp }, dist });
      }
    }
    // Only query Jev about the closest single candidate at a time to prevent request flooding
    if (candidates.length > 0) {
      candidates.sort((a, b) => a.dist - b.dist);
      const c = candidates[0];
      askEngage(c.key, c.id, c.x, c.y, player, c.ref);
    }
  }

  function updateGoal(player) {
    // If we entered the purple portal arena even without an active portal goal, track the portal visit automatically
    if (PS.Rift && PS.Rift.inArena && (!goal || !goal.ref || !goal.ref.portal)) {
      const tNow = PS.Game.st.time || 0;
      const hpNow = player.hp / (player.maxHp || 1);
      const snap = statSnap(player);
      goal = { key: "object:purple portal", id: "portal:arena", x: player.x, y: player.y, ref: { portal: true },
               entered: true, nearSnap: snap, nearT: tNow, nearMinHp: hpNow, t0: tNow, start: snap, minHp: hpNow, stayT: 0 };
    }
    if (!goal) return;
    // Wait while a strategic popup (Item, Bond, Level-Up) triggered by touching the object is open
    const scrItem = document.getElementById("scr-item");
    const scrBond = document.getElementById("scr-bond");
    const scrLevel = document.getElementById("scr-levelup");
    if (levelUpPending || itemPending || bondPending ||
        (scrItem && !scrItem.classList.contains("hidden")) ||
        (scrBond && !scrBond.classList.contains("hidden")) ||
        (scrLevel && !scrLevel.classList.contains("hidden"))) {
      return;
    }
    const t = PS.Game.st.time || 0;
    const hp = player.hp / (player.maxHp || 1);
    goal.minHp = Math.min(goal.minHp, hp);
    const d = Math.hypot(goal.x - player.x, goal.y - player.y);
    if (d < 50 && !goal.nearSnap) {
      goal.nearSnap = statSnap(player);
      goal.nearT = t;
      goal.nearMinHp = hp;
    }
    if (goal.nearSnap) goal.nearMinHp = Math.min(goal.nearMinHp, hp);

    if (player.dead) return finishGoal("died");
    if (goal.ref.portal) {
      if (PS.Rift.inArena) { goal.entered = true; return; }          // a different place: wait until I am back
      if (goal.entered) {
        goal.postDisappearT = (goal.postDisappearT || 0) + 1 / 60;
        if (goal.postDisappearT < 0.6) return;
        return finishGoal("came back from the other place");
      }
      if (!PS.Rift.rift) return finishGoal("it disappeared before I reached it");
    } else {
      const still = (PS.World.specialsNear(goal.x, goal.y, 40) || []).find((s) => s === goal.ref.sp);
      if (!still) {
        if (goal.nearSnap) {
          goal.postDisappearT = (goal.postDisappearT || 0) + 1 / 60;
          if (goal.postDisappearT < 0.9) return;
          return finishGoal("it disappeared after I reached it");
        }
        return finishGoal("it disappeared before I reached it");
      }
      if (still.used && !goal.wasUsed) {
        goal.postDisappearT = (goal.postDisappearT || 0) + 1 / 60;
        if (goal.postDisappearT < 0.4) return;
        goal.wasUsed = true;
        return finishGoal("it changed when I touched it");
      }
    }
    if (d < 22) { goal.stayT += 1 / 60; if (goal.stayT > 6.5) return finishGoal("stood next to it for 6s"); }
    const maxWalkT = (goal.ref && goal.ref.portal) ? 35 : 20;
    if (t - goal.t0 > maxWalkT) return finishGoal(`could not reach it in ${maxWalkT}s`);
  }

  // -------------------------------------------------------------
  // Meta between runs (pure): partner + permanent upgrades chosen by Jev from visible text + my results
  // -------------------------------------------------------------
  let metaState = "idle";
  async function runMetaDecisions() {
    if (!PS || !PS.DB || !PS.DB.save || !PS.DEX_BY) return;
    const save = PS.DB.save;
    const owned0 = Object.keys(save.owned || {}).length;
    const ask = await callJevBridge("JEV_META", { current: { dex: save.partner, name: (PS.DEX_BY[save.partner] || {}).name },
      owned: [], tickets: save.tickets || {} }, 6000);
    const wantPull = !!(ask && ask.data && ask.data.pull);
    if (wantPull && PS.Gacha && PS.Gacha.pull) {
      const got = [];
      try {
        let guard = 0;
        while ((save.tickets?.t10 || 0) >= 1 && guard++ < 20) { const r = PS.Gacha.pull(save, 10, 1, "t10"); if (!r) break; got.push(...r); }
        while ((save.tickets?.t1 || 0) >= 1 && guard++ < 60) { const r = PS.Gacha.pull(save, 1, 1, "t1"); if (!r) break; got.push(...r); }
      } catch (e) { console.warn("[Meta] gacha", e); }
      if (got.length) PS.DB.commit(true);
      recordDecision(got.length ? `🎰 Gacha x${got.length}` : "🎰 Gacha: nothing came out");
    }
    const owned = Object.keys(save.owned || {}).map(Number).filter((d) => PS.DEX_BY[d]).map((d) => {
      const m = PS.DEX_BY[d];
      return { dex: d, name: m.name, rarity: m.rarity, hp: m.hp, atk: m.atk, spd: m.spd };
    });
    const cur = owned.find((o) => o.dex === save.partner) || { dex: save.partner, name: "?" };
    const res = owned.length > owned0 || owned.length > 1 ? await callJevBridge("JEV_META", { current: cur, owned, tickets: {} }, 6000) : null;
    const pick = res && res.data ? res.data.partner_dex : null;
    if (pick && pick !== save.partner && save.owned[pick]) {
      save.partner = pick; save.partnerShiny = false; PS.DB.commit(true);
      if (PS.MenuUI && PS.MenuUI.open) PS.MenuUI.open();
      recordDecision(`🔁 Partner → ${(PS.DEX_BY[pick] || {}).name} (${res.data.partner_source})`);
    }
    // permanent upgrades: Jev reads the upgrade rows (name, description, level, cost)
    for (let i = 0; i < 8 && PS.Upgrades && PS.DB.upgradesOf; i++) {
      const upg = PS.DB.upgradesOf(save.partner);
      const options = PS.Upgrades.LIST.map((u) => {
        const lvl = upg[u.id] || 0;
        const cost = lvl >= u.max ? null : PS.Upgrades.cost(u.id, lvl);
        return { id: u.id, name: u.name, desc: u.unit, level: lvl, max: u.max, cost, affordable: cost !== null && save.coins >= cost };
      });
      if (!options.some((o) => o.affordable)) break;
      const r = await callJevBridge("JEV_UPGRADE", { coins: save.coins, options }, 6000);
      const buy = r && r.data ? r.data.buy : null;
      const o = options.find((x) => x.id === buy);
      if (!o || !o.affordable) break;
      save.coins -= o.cost; upg[o.id] = o.level + 1;
      PS.DB.commit(true);
      if (PS.UI && PS.UI.refreshCoins) PS.UI.refreshCoins();
      recordDecision(`🎖 ${o.name} → Lv${o.level + 1} (${r.data.source})`);
    }
  }

  // -------------------------------------------------------------
  // Run lifecycle: start (get experiment) -> sample -> checkpoint -> end (reflect)
  // -------------------------------------------------------------
  async function startRun() {
    if (runState.active || runState.starting) return;
    runState.starting = true;
    damageTracker.reset();
    slotStats.clear();
    enemyHpMap = new WeakMap();
    lastKillsObserved = 0;
    buffTapUntil = 0;
    prevActiveSlot = 0;
    Object.assign(runState, { timeline: [], picks: [], items: [], firstItemAnnounced: 0, bondsCount: 0, lastSampleT: -1, lastCheckpointT: 0,
      bossKills: 0, bossesDefeated: [], stuckEvents: 0,
      dmgAtLastSample: { ...damageTracker.bySource }, runId: null, experiment: "-", hypothesis: "" });
    knownBosses.clear();
    defeatedBosses.clear();
    tempAdjust = { evasion_weight_mult: 1.0, pickup_mult: 1.0, kite_distance_boost: 0 };
    lastCheckpoint = null;
    goal = null;
    oppDecided.clear();
    runState.opportunities = [];
    resetWorldDeltas();
    screenMessages.length = 0;
    exploreAngle = Math.random() * Math.PI * 2;
    lastExploreShiftT = 0;
    overMenuTried = false;
    const partner = PS.Game.player && PS.Game.player.mon ? PS.Game.player.mon.name : "Partner";
    // id exists even if the bridge is slow/offline, so the run can never be recorded as "untracked" or twice
    runState.runId = `c${Date.now()}${Math.floor(Math.random() * 1000)}`;
    runState.plan = null;
    const res = await callJevBridge("JEV_RUN_START", { partner, client_run_id: runState.runId, bot_build: BOT_BUILD }, 8000);
    if (res && res.data && res.data.run_id) {
      const d = res.data;
      runState.runId = d.run_id;
      runState.runNumber = d.run_number;
      runState.experiment = d.experiment;
      runState.hypothesis = d.hypothesis;
      runState.plan = d.plan || null;
      applyPolicy(d.policy || {});
      loadWorldModel(d.world_model);
      if (d.stat_priorities) statPriorities = d.stat_priorities;
      if (d.lessons && d.lessons.length) lastLessonText = d.lessons[0];
      refreshAdaptations();
      if (runState.plan) recordDecision(`🎯 Run #${d.run_number}: ${runState.plan.stance_label} · ❓ ${runState.plan.question_text || "-"}`);
      else recordDecision(`🧪 Run #${d.run_number}: ${d.experiment}`);
      console.log(`%c[Journal] Run #${d.run_number} plan:`, "color:#b38cff;font-weight:bold;", d.plan || d.experiment);
    } else {
      recordDecision("🧪 Bridge offline: using cached best params");
    }
    runState.active = true;
    runState.starting = false;
  }

  function sampleRun(player) {
    const t = PS.Game.st.time || 0;
    runState.lastT = t;
    runState.lastLevel = player.level;
    runState.lastKills = player.kills || 0;
    if (t - runState.lastSampleT < 10) return;
    runState.lastSampleT = t;

    const now = damageTracker.bySource;
    const prev = runState.dmgAtLastSample || {};
    const win = {};
    let winTotal = 0;
    for (const k of Object.keys(now)) {
      const v = Math.round((now[k] - (prev[k] || 0)) * 10) / 10;
      if (v > 0) { win[k] = v; winTotal += v; }
    }
    runState.dmgAtLastSample = { ...now };
    const point = {
      t: Math.round(t), level: player.level, hp_pct: Math.round((player.hp / (player.maxHp || 1)) * 100) / 100,
      x: Math.round(player.x || 0), y: Math.round(player.y || 0),
      kills: player.kills || 0, boss_kills: runState.bossKills || 0, enemies: PS.EnemyMgr ? PS.EnemyMgr.count : 0, dmg: win,
      dmg_pct_maxhp: Math.round((winTotal / (player.maxHp || 1)) * 100) / 100,
      moves: (player.moves || []).length,
    };
    runState.timeline.push(point);

    if (t - runState.lastCheckpointT >= 60) {
      runState.lastCheckpointT = t;
      const last6 = runState.timeline.slice(-6);
      const dmgPerMin = last6.reduce((a, p) => a + (p.dmg_pct_maxhp || 0), 0);
      callJevBridge("JEV_CHECKPOINT", { run_id: runState.runId, t: point.t, level: point.level, hp_pct: point.hp_pct,
        kills: point.kills, boss_kills: point.boss_kills, enemies: point.enemies, dmg_per_min: Math.round(dmgPerMin * 100) / 100, dmg_window: win }, 5000)
        .then((res) => {
          if (!res || !res.data || res.data.error) return;
          lastCheckpoint = res.data;
          tempAdjust = { evasion_weight_mult: 1.0, pickup_mult: 1.0, kite_distance_boost: 0, ...(res.data.temp_adjust || {}) };
          refreshAdaptations();
          if (res.data.posture !== "stay_course") {
            recordDecision(`🔎 ${Math.floor(point.t / 60)}m: ${res.data.verdict} → ${res.data.posture} (${res.data.source})`);
          }
        });
    }
  }

  function classifyDeath() {
    // the visible thing that took most of my HP this run (pure observation)
    let top = "unknown", max = 0;
    for (const [k, v] of Object.entries(damageTracker.bySource)) if (v > max) { max = v; top = k; }
    return top;
  }

  async function endRun() {
    if (!runState.active || runState.ending) return;
    runState.ending = true;
    const player = PS.Game.player;
    if (goal) finishGoal("run ended");
    let runTime = (PS.Game.st && PS.Game.st.time) || runState.lastT || 0;
    if (!runTime) {
      const b = document.querySelector("#over-stats b");
      const m = b && b.textContent.match(/(\d+):(\d+)/);
      if (m) runTime = +m[1] * 60 + +m[2];
    }
    const directItem = player && player.item ? (typeof player.item === "string" ? player.item : (player.item.id || null)) : null;
    const record = {
      run_id: runState.runId,
      time: Math.round(runTime * 10) / 10,
      level: (player && player.level) || runState.lastLevel,
      kills: (player && player.kills) || runState.lastKills,
      boss_kills: runState.bossKills || 0,
      bosses_defeated: runState.bossesDefeated || [],
      partner: player && player.mon ? player.mon.name : "Partner",
      moves: player && player.moves ? player.moves.map((m) => {
        const st = slotStats.get(m.id) || { kills: 0, dmg: 0, combatSec: 0 };
        const dps = st.combatSec >= 1.5 ? Math.round((st.dmg / st.combatSec) * 10) / 10 : 0;
        return { id: m.id, lvl: m.lvl, evolved: !!m.evolved, kills: st.kills, dmg: Math.round(st.dmg), combat_sec: Math.round(st.combatSec * 10) / 10, dps };
      }) : [],
      item: runState.items.length ? runState.items[runState.items.length - 1].equipped : (directItem || "none"),
      stats_taken: (player && player.statsTaken) || {},
      death_cause: classifyDeath(),
      world_deltas: worldDeltasPayload(),
      messages: screenMessages.slice(-20),
      damage_sources: { ...damageTracker.bySource },
      killer_info: damageTracker.lastHitInfo || {},
      picks: runState.picks,
      items: runState.items,
      opportunities: runState.opportunities || [],
      stuck_events: runState.stuckEvents || 0,
      timeline: runState.timeline,
      params: runParams,
      experiment: runState.experiment,
    };
    recordDecision(`🧠 Reflecting on run (${Math.floor(runTime / 60)}:${String(Math.floor(runTime % 60)).padStart(2, "0")})...`);
    const res = await callJevBridge("JEV_RUN_END", record, 15000);
    if (res && res.data && res.data.lesson) {
      const d = res.data;
      lastLessonText = d.lesson;
      if (d.best_params) {
        try { localStorage.setItem("ps_jev_best_params", JSON.stringify(d.best_params)); } catch (e) {}
      }
      learnedSummary = { ...(learnedSummary || {}), total_runs: d.total_runs, best_time: d.best_time_seconds, lessons: d.lessons };
      runState.nextPlan = d.next_plan || null;
      if (d.world_model) { loadWorldModel(d.world_model); resetWorldDeltas(); }
      recordDecision(`💡 ${d.decision} | ${d.verdict}${d.record ? " | NEW RECORD" : ""}`);
      console.log("%c[Learning] " + d.lesson, "color:#ffd23f;font-weight:bold;", d);
    } else {
      // keep the record locally so nothing is lost when the bridge is down
      try {
        const q = JSON.parse(localStorage.getItem("ps_jev_unsent_runs") || "[]");
        q.push(record);
        localStorage.setItem("ps_jev_unsent_runs", JSON.stringify(q.slice(-20)));
      } catch (e) {}
    }
    claimMissionsAndAchievements();
    if (res && res.data && res.data.expected_bot_build && res.data.expected_bot_build !== BOT_BUILD) {
      console.log(`%c[Bot] New extension build on disk (${res.data.expected_bot_build} vs ${BOT_BUILD}) — reloading tab...`, "color:#5fe08a;font-weight:bold;");
      setTimeout(() => location.reload(), 300);
      return;
    }
    runState.active = false;
    runState.ending = false;
  }

  async function flushUnsentRuns() {
    let q = [];
    try { q = JSON.parse(localStorage.getItem("ps_jev_unsent_runs") || "[]"); } catch (e) {}
    if (!q.length) return;
    const remain = [];
    for (const rec of q) {
      const res = await callJevBridge("JEV_RUN_END", rec, 15000);
      if (!(res && res.data && res.data.lesson)) remain.push(rec);
    }
    try { localStorage.setItem("ps_jev_unsent_runs", JSON.stringify(remain)); } catch (e) {}
  }

  function runLifecycleTick(player) {
    if (!runState.active && !runState.starting && !runState.ending && (PS.Game.st.time || 0) < 20) {
      startRun();
    }
    if (runState.active) sampleRun(player);
  }

  // -------------------------------------------------------------
  // Automatic Mission & Achievement Claiming
  // -------------------------------------------------------------
  function claimMissionsAndAchievements() {
    if (!PS || !PS.DB || !PS.DB.save) return;
    try {
      if (PS.Progress) {
        if (typeof PS.Progress.checkAch === "function") {
          PS.Progress.checkAch();
        }
        const save = PS.DB.save;
        if (save && save.missions) {
          let claimedCount = 0;
          (save.missions.daily || []).forEach((m) => {
            if (PS.Progress.done(m) && !m.claimed) {
              PS.Progress.claim(m, false);
              claimedCount++;
            }
          });
          (save.missions.weekly || []).forEach((m) => {
            if (PS.Progress.done(m) && !m.claimed) {
              PS.Progress.claim(m, true);
              claimedCount++;
            }
          });
          if (claimedCount > 0) {
            console.log(`%c[Bot] Claimed ${claimedCount} mission reward(s)!`, "color:#5fe08a; font-weight:bold;");
          }
        }
      }
    } catch (e) {
      console.warn("[Bot] Error claiming missions:", e);
    }
  }

  // -------------------------------------------------------------
  // Automatic Dialog & Menu Handling
  // -------------------------------------------------------------
  function handleMenus() {
    if (!autoPlayMenus) return;

    const tapStart = document.getElementById("tap-start");
    if (tapStart && !tapStart.classList.contains("hidden")) {
      tapStart.click();
    }

    const scrLogin = document.getElementById("scr-login");
    if (scrLogin && !scrLogin.classList.contains("hidden")) {
      const btnGuest = document.getElementById("btn-guest");
      if (btnGuest) {
        console.log("[Bot] Entering Guest Mode...");
        btnGuest.click();
      }
    }

    const scrTest = document.getElementById("scr-test");
    if (scrTest && !scrTest.classList.contains("hidden")) {
      const firstAns = scrTest.querySelector(".answer");
      if (firstAns) firstAns.click();
    }

    const scrResult = document.getElementById("scr-result");
    if (scrResult && !scrResult.classList.contains("hidden")) {
      const okBtn = document.getElementById("res-ok");
      if (okBtn) okBtn.click();
    }

    const scrMenu = document.getElementById("scr-menu");
    if (scrMenu && !scrMenu.classList.contains("hidden")) {
      if (metaState === "idle") {
        metaState = "running";
        claimMissionsAndAchievements();
        runMetaDecisions().catch((e) => console.warn("[Meta]", e)).finally(() => { metaState = "done"; });
      }
      const playBtn = scrMenu.querySelector('[data-go="play"]');
      if (playBtn && metaState === "done") {
        metaState = "idle";
        console.log("[Bot] Launching run from Menu...");
        playBtn.click();
      }
    }

    const scrOver = document.getElementById("scr-over");
    if (scrOver && !scrOver.classList.contains("hidden")) {
      const timeElem = scrOver.querySelector("#over-stats b");
      const survived = timeElem ? timeElem.textContent : "";
      console.log(`%c[Bot] Run finished. Survived: ${survived}`, "color:#ff6b6b; font-weight:bold;");

      // Reflect first, then start the next run (so the next run gets the new experiment)
      endRun();
      const againBtn = document.getElementById("btn-again");
      if (againBtn && !runState.active && !runState.ending && !againClickScheduled) {
        againClickScheduled = true;
        setTimeout(() => {
          againClickScheduled = false;
          const save = PS.DB && PS.DB.save;
          const np = runState.nextPlan;
          const tix = save && save.tickets ? (save.tickets.t1 || 0) + (save.tickets.t10 || 0) : 0;
          const wantMenu = save && ((np && np.partner_dex != null && np.partner_dex !== save.partner) || tix > 0);
          // like a player: go back to the menu to switch partner / open the gacha, instead of "play again"
          if (wantMenu && !overMenuTried && PS.MenuUI && PS.MenuUI.open) {
            overMenuTried = true;
            recordDecision(`🏠 Menu: ${np && np.partner_dex !== save.partner ? `switch to ${np.partner_name}` : ""}${tix ? ` · ${tix} tickets` : ""}`);
            try { PS.MenuUI.open(); } catch (e) { againBtn.click(); }
          } else {
            overMenuTried = false;
            againBtn.click();
          }
        }, 1500);
      }
    }
  }

  // -------------------------------------------------------------
  // Strategic Decision Triggers (Level-Up, Item, Bond)
  // -------------------------------------------------------------
  async function checkStrategicPopups() {
    if (!PS || !PS.Game) return;

    // A. Level-Up Screen
    const scrLevel = document.getElementById("scr-levelup");
    if (scrLevel && !scrLevel.classList.contains("hidden") && !levelUpPending) {
      levelUpPending = true;
      const player = PS.Game.player;
      const cards = [];
      const cardElems = document.querySelectorAll("#cards .card");

      cardElems.forEach((card) => cards.push(describeLevelCard(card, player)));

      const payload = {
        time: PS.Game.st.time,
        player: {
          name: player ? player.mon.name : "Partner",
          level: player ? player.level : 1,
          kills: player ? player.kills || 0 : 0,
          kpm: Math.round(((player ? player.kills || 0 : 0) * 60) / Math.max(10, PS.Game.st.time || 1)),
          hpPct: player ? player.hp / (player.maxHp || 1) : 1.0,
          moves: player ? player.moves.map((m) => {
            const st = slotStats.get(m.id) || { kills: 0, dmg: 0, combatSec: 0 };
            const kpm = st.combatSec > 3 ? Math.round((st.kills * 60) / st.combatSec) : 0;
            const dps = st.combatSec > 1 ? Math.round((st.dmg / st.combatSec) * 10) / 10 : 0;
            const role = (st.combatSec >= 3.5 && dps < 2 && st.kills <= 1) ? "0-damage support buff" : "damaging weapon";
            return { name: m.name, id: m.id, lvl: m.lvl, evolved: !!m.evolved, kills: st.kills, dps, kpm, role };
          }) : [],
        },
        cards,
        picked_titles: runState.picks.map((p) => p.title).slice(-12),
        picked_keys: runState.picks.map((p) => p.key),
        rerolls: player ? Math.max(0, 1 + (player.rerollBonus || 0) - (player.rerolls || 0)) : 0,
      };

      try {
        const res = await callJevBridge("JEV_LEVELUP", payload, 5000);
        const decision = res && res.data ? res.data : { action: "pick", index: 0, source: "fallback" };
        recordDecision(`LevelUp: ${decision.card || `Card ${decision.index + 1}`} (${decision.source})`);
        const chosen = cards[decision.index || 0];
        if (runState.active && decision.action !== "reroll" && chosen) {
          runState.picks.push({ t: Math.round(PS.Game.st.time || 0), level: player ? player.level : 1,
            key: chosen.key, title: chosen.title, type: chosen.type, source: decision.source,
            offered: cards.map((c) => c.key),
            evolves_hint: chosen.evolves_hint || null });
        }

        if (decision.action === "reroll" && PS.RunUI && PS.RunUI.reroll) {
          PS.RunUI.reroll();
        } else if (PS.RunUI && PS.RunUI.pick) {
          PS.RunUI.pick(decision.index || 0);
        } else if (cardElems[decision.index || 0]) {
          cardElems[decision.index || 0].click();
        }
      } catch (err) {
        if (cardElems[0]) cardElems[0].click();
      } finally {
        setTimeout(() => { levelUpPending = false; }, 400);
      }
    }

    // B. Held Item Screen
    const scrItem = document.getElementById("scr-item");
    if (scrItem && !scrItem.classList.contains("hidden") && !itemPending) {
      itemPending = true;
      const cardElems = document.querySelectorAll("#item-cards .card");
      const player = PS.Game.player;

      const curInfo = identifyEl(cardElems[0]);
      const newInfo = identifyEl(cardElems[1]);
      const txt = (el, sel) => ((el && el.querySelector(sel)) || {}).textContent || "";
      const payload = {
        time: PS.Game.st.time,
        current: { id: curInfo ? curInfo.id : null, title: txt(cardElems[0], ".nm").trim(), desc: txt(cardElems[0], ".ds").trim() },
        offered: { id: newInfo ? newInfo.id : null, title: txt(cardElems[1], ".nm").trim(), desc: txt(cardElems[1], ".ds").trim() },
        player: { hpPct: player ? player.hp / (player.maxHp || 1) : 1.0 },
      };

      try {
        const res = await callJevBridge("JEV_ITEM", payload, 4000);
        const decision = res && res.data ? res.data : { action: "keep", index: 0 };
        recordDecision(`Item: ${decision.action} (${decision.source || "jev"})`);

        const pickIdx = decision.index !== undefined ? decision.index : (decision.action === "equip" ? 1 : 0);
        if (runState.active) {
          const eq = pickIdx === 1 ? payload.offered : payload.current;
          runState.items.push({ t: Math.round(PS.Game.st.time || 0), current: payload.current.id, offered: payload.offered.id,
            equipped: eq.id, source: decision.source });
          if (eq.id) runState.picks.push({ t: Math.round(PS.Game.st.time || 0), key: `item:${eq.id}`, title: eq.title, type: "item", source: decision.source });
        }
        if (cardElems[pickIdx]) {
          cardElems[pickIdx].click();
        } else if (cardElems[0]) {
          cardElems[0].click();
        }
      } catch (err) {
        if (cardElems[0]) cardElems[0].click();
      } finally {
        setTimeout(() => { itemPending = false; }, 400);
      }
    }

    // C. Bond Fusion Screen
    const scrBond = document.getElementById("scr-bond");
    if (scrBond && !scrBond.classList.contains("hidden") && !bondPending) {
      bondPending = true;
      releaseAllKeys();
      const cardElems = document.querySelectorAll("#bond-cards .card, #scr-bond .card");
      try {
        recordDecision("Bond: fused move slot");
        if (PS.RunUI && PS.RunUI.pickBond) {
          PS.RunUI.pickBond(0);
        }
        if (!scrBond.classList.contains("hidden") && cardElems[0]) {
          cardElems[0].click();
        }
      } catch (err) {
        if (cardElems[0]) cardElems[0].click();
      } finally {
        releaseAllKeys();
        setTimeout(() => { bondPending = false; }, 400);
      }
    }
  }

  // -------------------------------------------------------------
  // Periodic Macro Tactical Stance Update
  // -------------------------------------------------------------
  // Main 60 FPS Game Loop
  // -------------------------------------------------------------
  let lastInRiftArena = false;
  function botLoop() {
    requestAnimationFrame(botLoop);

    if (!botEnabled) {
      releaseAllKeys();
      return;
    }

    handleMenus();

    if (!PS || !PS.Game) return;

    const inRiftNow = !!(PS.Rift && PS.Rift.inArena);
    if (inRiftNow !== lastInRiftArena) {
      lastInRiftArena = inRiftNow;
      releaseAllKeys();
    }

    checkStrategicPopups();

    if (PS.Game.state === "playing") {
      const player = PS.Game.player;
      if (player && !player.dead) {
        // Real-time damage attribution & forensics
        trackDamage(player);

        // Self-learning: experiment start, telemetry sampling, mid-run self-check
        runLifecycleTick(player);

        // Opportunities: notice -> decide -> pursue -> measure
        scanOpportunities(player);
        updateGoal(player);


        // 1. Intelligent Skill Weaving (Slot switching)
        updateSkillWeaving(player);

        // 2. 60 FPS reflex steering
        const [vx, vy] = computeOptimalVelocity(player);
        applyContinuousVectorWASD(vx, vy);
      } else {
        releaseAllKeys();
      }
    } else {
      releaseAllKeys();
    }

    updateHUD();
  }

  // -------------------------------------------------------------
  // Floating On-Screen HUD Overlay
  // -------------------------------------------------------------
  function createHUD() {
    if (document.getElementById("ps-jev-hud")) return;

    const hud = document.createElement("div");
    hud.id = "ps-jev-hud";
    hud.style.cssText = `
      position: fixed;
      top: 12px;
      left: 12px;
      z-index: 999999;
      background: rgba(10, 18, 38, 0.94);
      border: 1px solid rgba(255, 210, 94, 0.4);
      border-radius: 8px;
      padding: 10px 14px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 12px;
      color: #e0e8f8;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
      min-width: 270px;
      max-width: 320px;
      pointer-events: auto;
      user-select: none;
    `;

    hud.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid rgba(255,255,255,0.1); padding-bottom:6px;">
        <span style="font-weight:700; color:#ffd25e; font-size:13px;">⚡ Jev Bot · PURE LEARNING</span>
        <button id="ps-hud-toggle" style="background:#2a3b60; color:#fff; border:none; padding:3px 8px; border-radius:4px; cursor:pointer; font-size:11px; font-weight:600;">ACTIVE</button>
      </div>
      <div style="margin-bottom:4px;">⏱ Time: <b id="ps-hud-time" style="color:#fff;">00:00</b> / <span style="color:#ffd25e;">31:00</span> (Clear Goal)</div>
      <div style="margin-bottom:4px;">🛡 Stance: <b id="ps-hud-stance" style="color:#5fe08a;">FARM_AGGRO</b></div>
      <div style="margin-bottom:4px;">❤️ HP: <span id="ps-hud-hp">100%</span> | Lvl: <b id="ps-hud-lvl">1</b></div>
      <div style="margin-bottom:6px; font-size:11px; color:#9db8ff;">
        ⚔️ Active Slot: <b id="ps-hud-active-slot" style="color:#ffd25e;">[1]</b>
        <button id="ps-hud-weave-toggle" style="margin-left:6px; background:#1b2a4a; color:#8fa3c4; border:1px solid rgba(255,255,255,0.2); padding:1px 5px; border-radius:3px; cursor:pointer; font-size:10px;">Weave: ON</button>
      </div>
      <div style="margin-bottom:4px; font-size:11px; color:#9db8ff; border-top:1px solid rgba(255,255,255,0.08); padding-top:4px;">
        🧠 Memory: <span id="ps-hud-memory" style="color:#e0e8f8;">Run #0 | Best: 00:00</span>
      </div>
      <div id="ps-hud-plan" style="margin-bottom:2px; font-size:11px; color:#5fe08a; line-height:1.3;">🎯 計劃：等待開局</div>
      <div id="ps-hud-question" style="margin-bottom:6px; font-size:11px; color:#c9b6ff; line-height:1.3;">❓ 問題：-</div>
      <div style="margin-bottom:6px; font-size:10px; color:#ffd25e;" id="ps-hud-lesson">
        💡 Lesson: Ready to learn from runs
      </div>
      <div id="ps-hud-lastlesson" style="margin-bottom:6px; font-size:10px; color:#c9b6ff; line-height:1.3; max-height:40px; overflow:hidden;">🧠 Awaiting first run...</div>
      <div style="font-size:11px; color:#8fa3c4; border-top:1px solid rgba(255,255,255,0.08); padding-top:4px;">
        <div>Recent Decisions:</div>
        <div id="ps-hud-log" style="font-size:10px; color:#ffd25e; min-height:36px; line-height:1.3; margin-top:2px;">Bridge ready.</div>
      </div>
    `;

    document.body.appendChild(hud);

    const toggleBtn = document.getElementById("ps-hud-toggle");
    if (toggleBtn) {
      toggleBtn.onclick = () => {
        botEnabled = !botEnabled;
        toggleBtn.textContent = botEnabled ? "ACTIVE" : "PAUSED";
        toggleBtn.style.background = botEnabled ? "#2a3b60" : "#602a2a";
        if (!botEnabled) releaseAllKeys();
      };
    }

    const weaveBtn = document.getElementById("ps-hud-weave-toggle");
    if (weaveBtn) {
      weaveBtn.onclick = () => {
        skillWeavingEnabled = !skillWeavingEnabled;
        weaveBtn.textContent = `Weave: ${skillWeavingEnabled ? "ON" : "OFF"}`;
        weaveBtn.style.color = skillWeavingEnabled ? "#5fe08a" : "#ff8a96";
      };
    }
  }

  function updateHUD() {
    const timeEl = document.getElementById("ps-hud-time");
    const stanceEl = document.getElementById("ps-hud-stance");
    const hpEl = document.getElementById("ps-hud-hp");
    const lvlEl = document.getElementById("ps-hud-lvl");
    const slotEl = document.getElementById("ps-hud-active-slot");
    const memEl = document.getElementById("ps-hud-memory");
    const lessonEl = document.getElementById("ps-hud-lesson");
    if (!timeEl || !PS || !PS.Game) return;

    const gameTime = PS.Game.st.time || 0;
    const mins = Math.floor(gameTime / 60);
    const secs = Math.floor(gameTime % 60);
    timeEl.textContent = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;

    const player = PS.Game.player;
    if (player) {
      const hpPct = Math.round((player.hp / (player.maxHp || 1)) * 100);
      hpEl.textContent = `${hpPct}%`;
      lvlEl.textContent = player.level;

      if (slotEl && player.slots) {
        const curSlot = player.slots[player.active] || [];
        const slotDesc = getSlotSummary(curSlot);
        slotEl.textContent = `[${player.active + 1}] ${slotDesc}`;
      }
    }

    if (memEl) {
      const totalRuns = (learnedSummary && learnedSummary.total_runs) || 0;
      const bestSecs = (learnedSummary && learnedSummary.best_time) || 0;
      const bMin = Math.floor(bestSecs / 60);
      const bSec = Math.floor(bestSecs % 60);
      const cp = lastCheckpoint ? ` | vs best: ${lastCheckpoint.verdict}${lastCheckpoint.level_delta ? ` (${lastCheckpoint.level_delta > 0 ? "+" : ""}${lastCheckpoint.level_delta} lv)` : ""}` : "";
      memEl.textContent = `Run #${runState.runNumber || totalRuns + 1} | Best ${String(bMin).padStart(2, "0")}:${String(bSec).padStart(2, "0")}${cp}`;
      memEl.title = runState.hypothesis || "";
    }
    const planEl = document.getElementById("ps-hud-plan");
    const qEl = document.getElementById("ps-hud-question");
    const pl = runState.plan;
    if (planEl && qEl) {
      const planTxt = pl ? `🎯 計劃：${pl.stance_label} · ${pl.partner_name || "照舊搭檔"} · ${pl.build_text || ""}` : `🎯 計劃：${runState.experiment || "-"}`;
      const qTxt = `❓ 問題：${(pl && pl.question_text) || "-"}`;
      if (planEl.dataset.v !== planTxt) { planEl.dataset.v = planTxt; planEl.textContent = planTxt; }
      if (qEl.dataset.v !== qTxt) { qEl.dataset.v = qTxt; qEl.textContent = qTxt; }
    }

    if (lessonEl) {
      const a = activeAdaptations;
      const keys = new Set([...Object.keys(learned.danger), ...Object.keys(wdelta.danger)]);
      const top = [...keys].filter((k) => !k.endsWith(":ANY")).map((k) => [k, dangerRate(k, 0)]).sort((x, y) => y[1] - x[1]).slice(0, 2);
      lessonEl.textContent = `⚠ learned danger: ${top.map(([k, r]) => `${k.split(":")[1] || k} ${(r * 100).toFixed(0)}%/s`).join(", ") || "nothing yet"} | ⚙ Eva x${a.evasion_weight_mult.toFixed(2)} Greed x${a.pickup_mult.toFixed(2)}`;
      lessonEl.title = lastLessonText;
    }
    const llEl = document.getElementById("ps-hud-lastlesson");
    if (llEl && llEl.dataset.v !== lastLessonText) {
      llEl.dataset.v = lastLessonText;
      llEl.textContent = `🧠 ${lastLessonText}`;
    }

    if (stanceEl) {
      stanceEl.textContent = goal ? `INVESTIGATING ${goal.key.replace("object:", "")}` : "LEARNING";
      stanceEl.style.color = goal ? "#c9b6ff" : "#5fe08a";
    }
  }

  function recordDecision(text) {
    decisionHistory.unshift(text);
    if (decisionHistory.length > 3) decisionHistory.pop();
    const logEl = document.getElementById("ps-hud-log");
    if (logEl) {
      logEl.innerHTML = decisionHistory.map((d) => `• ${d}`).join("<br>");
    }
  }

  console.log("[PokéSurvivor Jev Bot] MAIN world script loaded successfully.");
})();
