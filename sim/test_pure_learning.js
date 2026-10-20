#!/usr/bin/env node
/**
 * Pure-learning test: the bot starts knowing NOTHING about what hurts.
 * It must learn from its own HP losses and change behaviour in later episodes.
 *   node sim/test_pure_learning.js
 */
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "../extension/page_agent.js"), "utf8");
const block = src.slice(src.indexOf("  // PURE LEARNING: perception"), src.indexOf("  // Icon-based identification"));

function makeBot(world, policyExtra) {
  const PS = {
    Game: { st: { time: 0 }, player: { mon: { types: [] } } },
    World: { isFree: (x, y, r, allowLiquid) => world.isFree ? world.isFree(x, y, r, allowLiquid)
             : (world.isLiquid && world.isLiquid(x, y) ? !!allowLiquid : true),
             isLiquid: (x, y) => !!(world.isLiquid && world.isLiquid(x, y)), specialsNear: () => [] },
    Hazards: { get all() { return world.hazards; } },
    Projectiles: { all: () => [] },
    EnemyMgr: { all: () => world.enemies },
    Pickups: { all: () => world.pickups },
  };
  const activeKeys = { KeyW: true };
  const activeAdaptations = { evasion_weight_mult: 1, hazard_avoidance_weight: 1, kite_distance_boost: 0, pickup_mult: 1, heal_seek_threshold: 0.65 };
  let goal = null;
  const policy = { w_level: 30, w_xp: 20, w_hp: 40, w_coins: 0.3, w_tickets: 15, w_bonds: 25, ...(policyExtra || {}) };
  const api = new Function("PS", "activeKeys", "activeAdaptations", "goal", "performance", "policy", "runState", "releaseAllKeys",
    block + "\nreturn { trackDamage, computeOptimalVelocity, motion, learned, wdelta, dangerRate, damageTracker, liquidSpeedRatio };");
  let clock = 0;
  const perf = { now: () => clock };
  const bot = api(PS, activeKeys, activeAdaptations, goal, perf, policy, {}, () => {});
  bot.tick = (ms) => { clock += ms; PS.Game.st.time = clock / 1000; };
  return bot;
}

function episode(bot, setup, seconds) {
  const world = bot.world;
  setup(world);
  const player = { x: 0, y: 0, r: 14, hp: 100, maxHp: 100 };
  bot.damageTracker.lastHp = 0;
  bot.motion.speed = 110;
  const dt = 1 / 60;
  let taken = 0;
  for (let t = 0; t < seconds; t += dt) {
    bot.tick(1000 * dt);
    world.step(player, dt, (dmg) => { player.hp -= dmg; taken += dmg; });
    bot.trackDamage(player);
    const [vx, vy] = bot.computeOptimalVelocity(player);
    const spd = world.isLiquid && world.isLiquid(player.x, player.y) ? 55 : 110;   // the game slows me in water
    player.x += vx * spd * dt; player.y += vy * spd * dt;
  }
  return taken;
}

let ok = true;
function report(name, dmgs, cond) {
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}: damage per episode = [${dmgs.map((d) => d.toFixed(0)).join(", ")}]`);
  ok = ok && cond;
}

// A) an unknown creature walks at me; touching it hurts (30% HP/s)
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  const dmgs = [];
  for (let ep = 0; ep < 6; ep++) {
    dmgs.push(episode(bot, (w) => {
      w.enemies = [{ x: 140, y: 30, r: 12, name: "Rattata" }];
      w.step = (p, dt, hurt) => {
        const e = w.enemies[0], d = Math.hypot(p.x - e.x, p.y - e.y) || 1;
        e.x += ((p.x - e.x) / d) * 70 * dt; e.y += ((p.y - e.y) / d) * 70 * dt;   // slower than me
        if (d < e.r + p.r) hurt(30 * dt);
      };
    }, 5));
  }
  report("learns to keep away from a creature that hurts on contact", dmgs, dmgs[5] < dmgs[0] * 0.5);
  console.log("      learned danger 'enemy:Rattata' touching =", (bot.dangerRate("enemy:Rattata", 0) * 100).toFixed(0) + "%/s");
}

// B) a circle appears under me while something I like sits in the middle; it explodes after 1s (25% HP if inside)
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  const dmgs = [];
  for (let ep = 0; ep < 6; ep++) {
    dmgs.push(episode(bot, (w) => {
      const hz = { kind: "zone", x: 0, y: 0, r: 50, delay: 1.0, t: 0, hit: false };
      w.hazards = [hz];
      w.pickups = [{ x: 0, y: 0, kind: "xp" }];
      w.step = (p, dt, hurt) => {
        if (hz.hit) return;
        hz.t += dt;
        if (hz.t >= hz.delay) { hz.hit = true; if (Math.hypot(p.x - hz.x, p.y - hz.y) < hz.r + 0.6 * p.r) hurt(25); }
      };
    }, 1.5));
  }
  report("learns that ground circles hurt and leaves them before they go off", dmgs, dmgs[0] > 0 && dmgs[5] === 0);
  console.log("      learned danger 'mark:circle' inside =", (bot.dangerRate("mark:circle", 0) * 100).toFixed(0) + "%/s");
}

// C) a creature that never hurts: must NOT become feared
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  for (let ep = 0; ep < 3; ep++) {
    episode(bot, (w) => {
      w.enemies = [{ x: 60, y: 0, r: 12, name: "Pidgey" }];
      w.step = (p, dt) => { const e = w.enemies[0]; const d = Math.hypot(p.x - e.x, p.y - e.y) || 1; e.x += ((p.x - e.x) / d) * 70 * dt; e.y += ((p.y - e.y) / d) * 70 * dt; };
    }, 3);
  }
  const r = bot.dangerRate("enemy:Pidgey", 0);
  report("does not fear a harmless creature", [0], r < 0.01);
  console.log("      learned danger 'enemy:Pidgey' touching =", (r * 100).toFixed(1) + "%/s");
}

// D) water: walkable but slow. The bot must be willing to wade in (to learn), and learn it is slower there.
{
  const world = { enemies: [], hazards: [], pickups: [], isLiquid: (x) => x > 40 };
  const bot = makeBot(world); bot.world = world;
  let reached = false;
  episode(bot, (w) => {
    w.pickups = [{ x: 90, y: 0, kind: "gem" }];
    w.step = (p) => { if (Math.hypot(p.x - 90, p.y) < 12) { reached = true; w.pickups = []; } };
  }, 3);
  // wander a bit more so both terrains get observed
  for (let i = 0; i < 2; i++) episode(bot, (w) => { w.pickups = [{ x: -60 + 160 * i, y: 40, kind: "gem" }]; w.step = () => {}; }, 2);
  const ratio = bot.liquidSpeedRatio();
  report("wades into water to investigate and learns it is slower there", [0], reached && ratio < 0.8);
  console.log("      reached item in water:", reached, "| learned water speed ratio:", ratio.toFixed(2));
}

// E) a wall (ledge) between me and something I want: I must walk around its end, not press into it forever.
{
  const WALL = { x0: 60, x1: 76, y0: -160, y1: 110 };
  const free = (x, y, r) => {
    const nx = Math.max(WALL.x0, Math.min(WALL.x1, x)), ny = Math.max(WALL.y0, Math.min(WALL.y1, y));
    return Math.hypot(x - nx, y - ny) >= (r || 14) * 0.6;
  };
  const world = { enemies: [], hazards: [], pickups: [{ x: 170, y: 0, kind: "gem" }], isFree: free };
  const bot = makeBot(world); bot.world = world;
  const player = { x: 0, y: 0, r: 14, hp: 100, maxHp: 100 };
  bot.damageTracker.lastHp = 0;
  bot.motion.speed = 110;
  const dt = 1 / 60;
  let reached = false, crossed = false;
  for (let t = 0; t < 14 && !reached; t += dt) {
    bot.tick(1000 * dt);
    bot.trackDamage(player);
    const [vx, vy] = bot.computeOptimalVelocity(player);
    const nx = player.x + vx * 110 * dt, ny = player.y + vy * 110 * dt;
    if (free(nx, player.y, player.r)) player.x = nx;   // axis-separated slide, like a top-down game
    if (free(player.x, ny, player.r)) player.y = ny;
    if (player.x > WALL.x1) crossed = true;
    if (Math.hypot(player.x - 170, player.y) < 16) reached = true;
  }
  report("walks around the end of a wall instead of pressing into it", [0], reached);
  console.log(`      end pos (${player.x.toFixed(0)}, ${player.y.toFixed(0)}), crossed wall line: ${crossed}, reached pickup: ${reached}`);
}

// G) a BIG circle centred on me: getting out takes most of the fuse, so I must start leaving at once.
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  const dmgs = [];
  for (let ep = 0; ep < 6; ep++) {
    dmgs.push(episode(bot, (w) => {
      const hz = { kind: "zone", x: 0, y: 0, r: 150, delay: 1.5, t: 0, hit: false };
      w.hazards = [hz];
      w.pickups = [{ x: 0, y: 0, kind: "xp" }];
      w.step = (p, dt, hurt) => {
        if (hz.hit) return;
        hz.t += dt;
        if (hz.t >= hz.delay) { hz.hit = true; if (Math.hypot(p.x - hz.x, p.y - hz.y) < hz.r + 0.6 * p.r) hurt(25); }
      };
    }, 2.2));
  }
  report("starts leaving a big circle early enough to clear its edge", dmgs, dmgs[0] > 0 && dmgs[5] === 0);
}

// H) a big circle while a pack closes in: escaping means crossing the pack (contact hurts),
// but the burst (60) is far worse than brushing past them. Late-run data: marking damage is
// concentrated in the crowded final phase, so this is the case that actually kills.
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  const dmgs = [];
  for (let ep = 0; ep < 8; ep++) {
    dmgs.push(episode(bot, (w) => {
      const hz = { kind: "zone", x: 0, y: 0, r: 150, delay: 1.5, t: 0, hit: false };
      w.hazards = [hz];
      w.pickups = [{ x: 0, y: 0, kind: "xp" }];
      w.enemies = [];
      for (let i = 0; i < 14; i++) {
        const a = (i / 14) * 2 * Math.PI;
        w.enemies.push({ x: Math.cos(a) * 120, y: Math.sin(a) * 120, r: 12, name: "Rattata" });
      }
      w.step = (p, dt, hurt) => {
        for (const e of w.enemies) {
          const d = Math.hypot(p.x - e.x, p.y - e.y) || 1;
          e.x += ((p.x - e.x) / d) * 70 * dt; e.y += ((p.y - e.y) / d) * 70 * dt;
          if (d < e.r + p.r) hurt(30 * dt);
        }
        if (!hz.hit) {
          hz.t += dt;
          if (hz.t >= hz.delay) { hz.hit = true; if (Math.hypot(p.x - hz.x, p.y - hz.y) < hz.r + 0.6 * p.r) hurt(60); }
        }
      };
    }, 2.2));
  }
  report("escapes a crowded big circle before the burst (contact only, no burst)", dmgs, dmgs[0] > 60 && dmgs[7] < 60);
}

// I) pickup economics: with measured gains in memory (heal +HP, xp +XP), a badly
// hurt bot must head for the heal even when XP is closer; at full HP the heal loses.
// Guards the flat value floor that once made every pickup kind worth the same.
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  bot.learned.pickups["pickup:heal"] = { n: 20, sum: { hp: 1.36 } };        // measured: +6.8% HP each
  bot.learned.pickups["pickup:xp"] = { n: 20, sum: { xp: 0.14, level: 0.16 } };
  const player = { x: 0, y: 0, r: 14, hp: 100, maxHp: 100 };
  bot.damageTracker.lastHp = 0;
  bot.motion.speed = 110;
  world.pickups = [{ x: 160, y: 0, kind: "heal" }, { x: -90, y: 0, kind: "xp" }];
  bot.tick(16);
  bot.trackDamage(player);
  const [vxFull] = bot.computeOptimalVelocity(player);
  player.hp = 30;
  bot.trackDamage(player);
  const [vxHurt] = bot.computeOptimalVelocity(player);
  report("hurt bot heads for the measured heal over closer XP", [0], vxHurt > 0.5 && vxFull < 0);
  console.log(`      full-HP vx=${vxFull.toFixed(2)} (toward xp), hurt vx=${vxHurt.toFixed(2)} (toward heal)`);
}

// J) a fast-fuse line beam (delay 0.50s <= 0.60s horizon) aimed across me while XP sits ahead:
// costAt must evaluate my position at tLeft (0.50s), not after I would have crossed the beam at 0.60s.
{
  const world = { enemies: [], hazards: [], pickups: [] };
  const bot = makeBot(world); bot.world = world;
  const dmgs = [];
  for (let ep = 0; ep < 6; ep++) {
    dmgs.push(episode(bot, (w) => {
      const hz = { kind: "line", x: -120, y: 0, angle: 0, len: 360, w: 28, delay: 0.50, t: 0, hit: false };
      w.hazards = [hz];
      w.pickups = [{ x: 60, y: 0, kind: "xp" }];
      w.step = (p, dt, hurt) => {
        if (hz.hit) return;
        hz.t += dt;
        if (hz.t >= hz.delay) {
          hz.hit = true;
          if (Math.abs(p.y - hz.y) < hz.w / 2 + 0.6 * p.r) hurt(35);
        }
      };
    }, 1.0));
  }
  report("dodges a fast-fuse line marking (delay <= 0.6s) before it fires", dmgs, dmgs[0] > 0 && dmgs[5] === 0);
}

console.log(ok ? "\nALL PASS" : "\nSOME FAILED");
process.exit(ok ? 0 : 1);
