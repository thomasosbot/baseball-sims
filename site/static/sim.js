/**
 * sim.js — Browser-side MLB game simulation engine.
 *
 * Faithful port of the Python Monte Carlo simulation from:
 *   src/simulation/pa_model.py
 *   src/simulation/constants.py
 *   src/simulation/game_sim.py
 *
 * Pure vanilla JS, no external dependencies.
 *
 * Usage:
 *   const game = new SimGame(homeTeam, awayTeam, parkFactors);
 *   while (!game.isGameOver) {
 *     const event = game.nextPA();
 *     // render event...
 *   }
 *   // or: const events = game.simToEnd();
 */

// =========================================================================
// Outcome categories (same order as Python)
// =========================================================================

const OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"];
const _K = 0, _BB = 1, _HBP = 2, _HR = 3, _3B = 4, _2B = 5, _1B = 6, _OUT = 7;

// =========================================================================
// League-average constants (2024 MLB)
// =========================================================================

const LEAGUE_RATES = {
  K:   0.224,
  BB:  0.085,
  HBP: 0.011,
  HR:  0.030,
  "3B": 0.004,
  "2B": 0.047,
  "1B": 0.143,
  OUT: 0.456,
};

// Base advancement probabilities by hit type and occupied base.
// Keys: base number (1=1B, 2=2B, 3=3B). "score" means runner scores.
const BASE_ADVANCEMENT = {
  "1B": {
    1: { advance_to: [2, 3],        probs: [0.72, 0.28] },
    2: { advance_to: [3, "score"],  probs: [0.40, 0.60] },
    3: { advance_to: ["score"],     probs: [1.00] },
  },
  "2B": {
    1: { advance_to: [3, "score"],  probs: [0.44, 0.56] },
    2: { advance_to: ["score"],     probs: [1.00] },
    3: { advance_to: ["score"],     probs: [1.00] },
  },
  "3B": {
    1: { advance_to: ["score"], probs: [1.00] },
    2: { advance_to: ["score"], probs: [1.00] },
    3: { advance_to: ["score"], probs: [1.00] },
  },
  HR: {
    1: { advance_to: ["score"], probs: [1.00] },
    2: { advance_to: ["score"], probs: [1.00] },
    3: { advance_to: ["score"], probs: [1.00] },
  },
};

// Stolen base rates
const SB_ATTEMPT_RATE_1B = 0.07;
const SB_ATTEMPT_RATE_2B = 0.015;
const SB_SUCCESS_RATE    = 0.78;
const SB_SPEED_FACTOR    = 0.008;
const LEAGUE_AVG_SPEED   = 100;

// Wild pitch / passed ball
const WILD_PITCH_RATE = 0.008;

// Errors (reached on error)
const ERROR_RATE = 0.014;

// Productive outs
const PRODUCTIVE_OUT_2B_TO_3B = 0.18;
const PRODUCTIVE_OUT_1B_TO_2B = 0.11;

// Sac fly, double play, ground ball fraction
const SAC_FLY_PROB         = 0.13;
const DOUBLE_PLAY_PROB     = 0.12;
const GROUND_BALL_OUT_FRAC = 0.45;

// Starter usage and TTO
const STARTER_BATTER_LIMIT = 22;
const TTO_HIT_BOOST = { 1: 1.00, 2: 1.10, 3: 1.20 };

// Bullpen leverage threshold (run differential)
const BULLPEN_HIGH_LEV_THRESHOLD = 2;

// =========================================================================
// Utility: seeded PRNG (xoshiro128** — fast, good quality, seedable)
// =========================================================================

/**
 * Simple seedable PRNG using the Mulberry32 algorithm.
 * Returns a function that produces floats in [0, 1).
 */
function createRNG(seed) {
  // Convert any seed to a 32-bit integer
  let s = seed | 0;
  if (s === 0) s = 1;

  return {
    /** Return a float in [0, 1). */
    random() {
      s |= 0;
      s = (s + 0x6D2B79F5) | 0;
      let t = Math.imul(s ^ (s >>> 15), 1 | s);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    },

    /**
     * Choose an index from a probability array (weighted random selection).
     * @param {number[]} probs — array of probabilities summing to ~1
     * @returns {number} — chosen index
     */
    choice(probs) {
      const r = this.random();
      let cumulative = 0;
      for (let i = 0; i < probs.length; i++) {
        cumulative += probs[i];
        if (r < cumulative) return i;
      }
      return probs.length - 1;
    },

    /**
     * Choose a value from a list of options with given probabilities.
     * @param {Array} options — array of possible values
     * @param {number[]} probs — parallel array of probabilities
     * @returns {*} — chosen value
     */
    choiceFrom(options, probs) {
      return options[this.choice(probs)];
    },
  };
}

// =========================================================================
// PA Model: odds-ratio blend + park factors
// =========================================================================

/**
 * Multiplicative odds-ratio blend: P = (b * p) / l, clipped to [eps, 1].
 */
function oddsRatioBlend(b, p, l) {
  const eps = 1e-9;
  const bv = Math.max(eps, Math.min(b, 1.0));
  const pv = Math.max(eps, Math.min(p, 1.0));
  const lv = Math.max(eps, Math.min(l, 1.0));
  return Math.max(eps, Math.min((bv * pv) / lv, 1.0));
}

/**
 * Compute normalised PA outcome distribution for one batter-pitcher matchup.
 *
 * @param {Object} batterProfile  — rates keyed by outcome (K, BB, HBP, HR, 3B, 2B, 1B, OUT)
 * @param {Object} pitcherProfile — rates keyed by outcome
 * @param {Object} [parkFactors]  — multiplicative adjustments (HR, 3B, 2B, 1B, bb, k)
 * @returns {Object} — normalised probabilities keyed by outcome
 */
function computePAProbabilities(batterProfile, pitcherProfile, parkFactors) {
  const raw = {};
  for (const outcome of OUTCOMES) {
    const b = (batterProfile[outcome] != null) ? batterProfile[outcome] : LEAGUE_RATES[outcome];
    const p = (pitcherProfile[outcome] != null) ? pitcherProfile[outcome] : LEAGUE_RATES[outcome];
    raw[outcome] = oddsRatioBlend(b, p, LEAGUE_RATES[outcome]);
  }

  // Park factor adjustments (multiplicative, pre-normalisation)
  if (parkFactors) {
    for (const outcome of ["HR", "3B", "2B", "1B"]) {
      if (parkFactors[outcome] != null) {
        raw[outcome] *= parkFactors[outcome];
      }
    }
    if (parkFactors.bb != null) raw.BB *= parkFactors.bb;
    if (parkFactors.k  != null) raw.K  *= parkFactors.k;
  }

  // Normalise to sum = 1
  let total = 0;
  for (const o of OUTCOMES) total += raw[o];
  const probs = {};
  for (const o of OUTCOMES) probs[o] = raw[o] / total;
  return probs;
}

// =========================================================================
// Pre-compute PA probability arrays
// =========================================================================

/**
 * Compute PA probs for a lineup vs a bullpen profile.
 * @returns {number[][]} — shape [9][8]
 */
function computeBullpenProbs(lineup, bullpenProfile, parkFactors) {
  const pitcherHand = bullpenProfile.throws || "R";
  const probs = [];
  for (let slot = 0; slot < 9; slot++) {
    const batter = lineup[slot];
    let batterHand = batter.bats || "R";
    // Switch hitter: use "L" vs RHP, "R" vs LHP
    if (batterHand === "S") {
      batterHand = (pitcherHand === "R") ? "L" : "R";
    }
    const batterRates  = batter.profile[pitcherHand] || batter.profile.R;
    const pitcherRates = bullpenProfile[batterHand]   || bullpenProfile.R;
    const paProbs = computePAProbabilities(batterRates, pitcherRates, parkFactors);
    probs.push(OUTCOMES.map(o => paProbs[o]));
  }
  return probs;
}

/**
 * Pre-compute all PA probability arrays for one side of the game.
 *
 * @param {Object[]} lineup           — array of 9 batter objects
 * @param {Object}   starterProfile   — starter pitcher profile
 * @param {Object}   bullpenHiProfile — high-leverage bullpen profile
 * @param {Object}   bullpenLoProfile — low-leverage bullpen profile
 * @param {Object}   parkFactors      — park factor adjustments
 * @returns {{ starterProbs: number[][][], bullpenHiProbs: number[][], bullpenLoProbs: number[][] }}
 *   starterProbs shape: [9][3][8] — [slot][tto_level_idx][outcome]
 */
function precomputePAArrays(lineup, starterProfile, bullpenHiProfile, bullpenLoProfile, parkFactors) {
  const pitcherHandS = starterProfile.throws || "R";
  const starterProbs = [];

  for (let slot = 0; slot < 9; slot++) {
    const batter = lineup[slot];
    let batterHand = batter.bats || "R";
    if (batterHand === "S") {
      batterHand = (pitcherHandS === "R") ? "L" : "R";
    }

    const batterRates  = batter.profile[pitcherHandS] || batter.profile.R;
    const pitcherRates = starterProfile[batterHand]    || starterProfile.R;

    // Base PA probs vs starter (TTO1 = no boost)
    const baseProbs = computePAProbabilities(batterRates, pitcherRates, parkFactors);
    const arr = OUTCOMES.map(o => baseProbs[o]);

    const slotProbs = [arr.slice()]; // tto_idx 0

    // TTO2 and TTO3+ boosted versions
    for (const ttoLevel of [2, 3]) {
      const boost = TTO_HIT_BOOST[Math.min(ttoLevel, 3)] || 1.0;
      const boosted = arr.slice();
      boosted[_HR]  *= boost;
      boosted[_3B]  *= boost;
      boosted[_2B]  *= boost;
      boosted[_1B]  *= boost;
      // Re-normalise
      let sum = 0;
      for (let i = 0; i < 8; i++) sum += boosted[i];
      for (let i = 0; i < 8; i++) boosted[i] /= sum;
      slotProbs.push(boosted);
    }

    starterProbs.push(slotProbs); // starterProbs[slot][tto_idx][outcome]
  }

  const bullpenHiProbs = computeBullpenProbs(lineup, bullpenHiProfile, parkFactors);
  const bullpenLoProbs = computeBullpenProbs(lineup, bullpenLoProfile, parkFactors);

  return { starterProbs, bullpenHiProbs, bullpenLoProbs };
}

// =========================================================================
// Baserunner logic
// =========================================================================

/**
 * Advance runners on a hit (1B, 2B, 3B, HR).
 * Processes runners from 3rd base backward (highest base first).
 * Batter placed on appropriate base after runners advance.
 *
 * @param {boolean[]} bases   — [1B, 2B, 3B] occupancy
 * @param {string}    hitType — "1B", "2B", "3B", or "HR"
 * @param {Object}    rng     — PRNG instance
 * @returns {{ bases: boolean[], runs: number }}
 */
function advanceRunners(bases, hitType, rng) {
  if (hitType === "HR") {
    const runs = bases.filter(Boolean).length + 1;
    return { bases: [false, false, false], runs };
  }

  const newBases = [false, false, false];
  const adv = BASE_ADVANCEMENT[hitType];
  let runs = 0;

  // Process from 3rd base backward (indices 2, 1, 0)
  for (const baseIdx of [2, 1, 0]) {
    if (!bases[baseIdx]) continue;
    const baseNum = baseIdx + 1;
    const info = adv[baseNum];
    if (!info) {
      // No advancement rule — runner stays
      newBases[baseIdx] = true;
      continue;
    }
    const dest = rng.choiceFrom(info.advance_to, info.probs);
    if (dest === "score") {
      runs++;
    } else {
      newBases[dest - 1] = true;
    }
  }

  // Place batter on appropriate base
  if (hitType === "1B")      newBases[0] = true;
  else if (hitType === "2B") newBases[1] = true;
  else if (hitType === "3B") newBases[2] = true;

  return { bases: newBases, runs };
}

/**
 * Handle a walk or HBP — push runners along forced bases.
 * @param {boolean[]} bases
 * @returns {{ bases: boolean[], runs: number }}
 */
function handleWalk(bases) {
  let runs = 0;
  const newBases = bases.slice();
  if (newBases[0]) {
    if (newBases[1]) {
      if (newBases[2]) {
        runs = 1; // bases loaded, runner on 3B scores
      } else {
        newBases[2] = true;
      }
    } else {
      newBases[1] = true;
    }
  }
  newBases[0] = true;
  return { bases: newBases, runs };
}

/**
 * Handle an out outcome, including errors, sac flies, double plays,
 * and productive outs.
 *
 * @param {boolean[]} bases
 * @param {number}    outs — current outs before this PA
 * @param {Object}    rng
 * @returns {{ bases: boolean[], runs: number, extraOuts: number }}
 *   extraOuts: -1 = no out recorded (reached on error), 0 = normal, 1 = DP
 */
function handleOut(bases, outs, rng) {
  const newBases = bases.slice();
  let runs = 0;
  let extraOuts = 0;

  // --- Reached on error: batter reaches 1B, runners advance one base ---
  if (rng.random() < ERROR_RATE) {
    if (newBases[2]) { runs++; newBases[2] = false; }
    if (newBases[1]) { newBases[2] = true; newBases[1] = false; }
    if (newBases[0]) { newBases[1] = true; }
    newBases[0] = true;
    return { bases: newBases, runs, extraOuts: -1 };
  }

  // --- Sac fly: runner on 3B scores ---
  if (newBases[2] && outs < 2 && rng.random() < SAC_FLY_PROB) {
    runs++;
    newBases[2] = false;
    return { bases: newBases, runs, extraOuts: 0 };
  }

  // --- Double play ---
  if (newBases[0] && outs < 2 && rng.random() < GROUND_BALL_OUT_FRAC) {
    if (rng.random() < DOUBLE_PLAY_PROB) {
      newBases[0] = false;
      extraOuts = 1;
      if (newBases[1]) {
        newBases[2] = true;
        newBases[1] = false;
      }
      return { bases: newBases, runs, extraOuts };
    }
  }

  // --- Productive outs (elif: only one can trigger) ---
  if (newBases[1] && !newBases[2] && rng.random() < PRODUCTIVE_OUT_2B_TO_3B) {
    newBases[2] = true;
    newBases[1] = false;
  } else if (newBases[0] && !newBases[1] && rng.random() < PRODUCTIVE_OUT_1B_TO_2B) {
    newBases[1] = true;
    newBases[0] = false;
  }

  return { bases: newBases, runs, extraOuts: 0 };
}

// =========================================================================
// Stolen bases and wild pitches
// =========================================================================

/**
 * Check for stolen base attempts before a PA.
 * Steal of 2B: runner on 1B, 2B empty, < 2 outs.
 * Steal of 3B: runner on 2B, 3B empty, < 2 outs.
 *
 * @param {boolean[]} bases
 * @param {number}    outs
 * @param {number}    teamSpeed — team average BHQ SPD
 * @param {Object}    rng
 * @returns {{ bases: boolean[], runs: number, outsAdded: number, event: string|null }}
 */
function checkStolenBase(bases, outs, teamSpeed, rng) {
  const newBases = bases.slice();
  let runs = 0;
  let outsAdded = 0;
  let event = null;
  const speedRatio = teamSpeed / LEAGUE_AVG_SPEED;

  // Steal of 2B
  if (newBases[0] && !newBases[1] && outs < 2) {
    const attemptRate = SB_ATTEMPT_RATE_1B * speedRatio;
    if (rng.random() < attemptRate) {
      let successRate = SB_SUCCESS_RATE + SB_SPEED_FACTOR * (teamSpeed - LEAGUE_AVG_SPEED);
      successRate = Math.max(0.50, Math.min(successRate, 0.95));
      if (rng.random() < successRate) {
        newBases[1] = true;
        newBases[0] = false;
        event = "sb_success";
      } else {
        newBases[0] = false;
        outsAdded = 1;
        event = "sb_caught";
      }
    }
  }
  // Steal of 3B (elif — only if no steal of 2B situation)
  else if (newBases[1] && !newBases[2] && outs < 2) {
    const attemptRate = SB_ATTEMPT_RATE_2B * speedRatio;
    if (rng.random() < attemptRate) {
      let successRate = SB_SUCCESS_RATE + SB_SPEED_FACTOR * (teamSpeed - LEAGUE_AVG_SPEED);
      successRate = Math.max(0.50, Math.min(successRate, 0.95));
      if (rng.random() < successRate) {
        newBases[2] = true;
        newBases[1] = false;
        event = "sb_success";
      } else {
        newBases[1] = false;
        outsAdded = 1;
        event = "sb_caught";
      }
    }
  }

  return { bases: newBases, runs, outsAdded, event };
}

/**
 * Check for wild pitch / passed ball before a PA.
 * Advances all runners one base; runner on 3B scores.
 *
 * @param {boolean[]} bases
 * @param {Object}    rng
 * @returns {{ bases: boolean[], runs: number, occurred: boolean }}
 */
function checkWildPitch(bases, rng) {
  if (!bases[0] && !bases[1] && !bases[2]) {
    return { bases, runs: 0, occurred: false };
  }
  if (rng.random() >= WILD_PITCH_RATE) {
    return { bases, runs: 0, occurred: false };
  }

  const newBases = [false, false, false];
  let runs = 0;
  if (bases[2]) runs++;
  if (bases[1]) newBases[2] = true;
  if (bases[0]) newBases[1] = true;
  return { bases: newBases, runs, occurred: true };
}

// =========================================================================
// Human-readable descriptions
// =========================================================================

/**
 * Generate a human-readable description for a play.
 */
function describePlay(batterName, outcome, runsScored, prePlay, postPlay, events) {
  const parts = [];

  // Pre-PA events
  for (const ev of events) {
    if (ev === "sb_success")  parts.push("Stolen base!");
    if (ev === "sb_caught")   parts.push("Caught stealing.");
    if (ev === "wild_pitch")  parts.push("Wild pitch — runners advance.");
  }

  // Main outcome
  switch (outcome) {
    case "K":   parts.push(`${batterName} strikes out.`); break;
    case "BB":  parts.push(`${batterName} walks.`); break;
    case "HBP": parts.push(`${batterName} hit by pitch.`); break;
    case "HR": {
      const rbi = runsScored;
      if (rbi === 1)      parts.push(`${batterName} hits a solo home run!`);
      else if (rbi === 2) parts.push(`${batterName} hits a 2-run homer!`);
      else if (rbi === 3) parts.push(`${batterName} hits a 3-run homer!`);
      else                parts.push(`${batterName} hits a GRAND SLAM!`);
      break;
    }
    case "3B":  parts.push(`${batterName} triples!`); break;
    case "2B":  parts.push(`${batterName} doubles!`); break;
    case "1B":  parts.push(`${batterName} singles.`); break;
    case "OUT": {
      // Check if it was actually an error (no out added)
      if (prePlay.outs === postPlay.outs && prePlay.outs < 3) {
        parts.push(`${batterName} reaches on an error.`);
      } else if (postPlay.outs - prePlay.outs >= 2) {
        parts.push(`${batterName} grounds into a double play.`);
      } else if (runsScored > 0) {
        parts.push(`${batterName} hits a sacrifice fly, run scores.`);
      } else {
        parts.push(`${batterName} is out.`);
      }
      break;
    }
    default: parts.push(`${batterName}: ${outcome}`);
  }

  if (runsScored > 0 && outcome !== "HR") {
    parts.push(`${runsScored} run${runsScored > 1 ? "s" : ""} score${runsScored === 1 ? "s" : ""}.`);
  }

  return parts.join(" ");
}

// =========================================================================
// SimGame class
// =========================================================================

/**
 * Interactive baseball game simulation.
 *
 * @param {Object} homeTeam — { lineup: [{name, bats, profile, speed?}, ...], starter, bullpenHi, bullpenLo, speed }
 * @param {Object} awayTeam — same structure as homeTeam
 * @param {Object} parkFactors — { HR, 3B, 2B, 1B, bb, k }
 * @param {number} [seed] — optional PRNG seed for reproducibility
 *
 * Each team object must have:
 *   - lineup: array of 9 batter objects, each with:
 *       - name: string
 *       - bats: "L" | "R" | "S"
 *       - profile: { "R": {K, BB, HBP, HR, 3B, 2B, 1B, OUT}, "L": {...} }
 *         (keyed by pitcher hand the batter faces)
 *       - speed: number (optional, default 100)
 *   - starter: pitcher profile { throws: "R"|"L", "L": {rates...}, "R": {rates...} }
 *   - bullpenHi: high-leverage bullpen profile (same shape as starter)
 *   - bullpenLo: low-leverage bullpen profile (same shape as starter)
 *   - speed: team average speed (optional, default 100)
 */
class SimGame {
  constructor(homeTeam, awayTeam, parkFactors, seed) {
    this.rng = createRNG(seed != null ? seed : Math.floor(Math.random() * 2147483647));

    this.homeTeam = homeTeam;
    this.awayTeam = awayTeam;
    this.parkFactors = parkFactors || {};

    // Compute team average speeds
    this.homeSpeed = homeTeam.speed || _avgSpeed(homeTeam.lineup);
    this.awaySpeed = awayTeam.speed || _avgSpeed(awayTeam.lineup);

    // Pre-compute PA probability arrays.
    // "home pitching" = probs for away batters vs home starter/bullpen
    // "away pitching" = probs for home batters vs away starter/bullpen
    const homePitching = precomputePAArrays(
      awayTeam.lineup, homeTeam.starter, homeTeam.bullpenHi, homeTeam.bullpenLo, parkFactors
    );
    const awayPitching = precomputePAArrays(
      homeTeam.lineup, awayTeam.starter, awayTeam.bullpenHi, awayTeam.bullpenLo, parkFactors
    );

    // Store pitching arrays by half-inning perspective
    this._pitching = {
      top: homePitching,    // away bats vs home pitching
      bottom: awayPitching, // home bats vs away pitching
    };

    // Game state
    this._inning = 1;
    this._halfInning = "top"; // "top" = away bats, "bottom" = home bats
    this._outs = 0;
    this._bases = [false, false, false];
    this._score = [0, 0]; // [away, home]

    // Lineup / pitcher tracking
    this._awayOrderPos = 0;
    this._homeOrderPos = 0;
    this._homePitcherBF = 0;
    this._awayPitcherBF = 0;

    // TTO tracking per batter slot (starter only)
    this._awayBatterTTO = {}; // away batters' TTO vs home starter
    this._homeBatterTTO = {}; // home batters' TTO vs away starter

    // Extras tracking
    this._extras = 0;
    this._maxExtras = 10;

    // Game over flag
    this._gameOver = false;
    this._walkoff = false;

    // Pending half-inning start (for ghost runner setup, etc.)
    this._halfInningStarted = false;
    this._ghostRunnerPlaced = false;
  }

  /** Whether the game has ended. */
  get isGameOver() {
    return this._gameOver;
  }

  /** Current score as [away, home]. */
  get score() {
    return this._score.slice();
  }

  /**
   * Advance one plate appearance and return a detailed event object.
   * Returns null if game is already over.
   */
  nextPA() {
    if (this._gameOver) return null;

    // --- Start of half-inning setup ---
    if (!this._halfInningStarted) {
      this._startHalfInning();
    }

    const isTop = this._halfInning === "top";
    const battingTeam = isTop ? this.awayTeam : this.homeTeam;
    const orderPos = isTop ? this._awayOrderPos : this._homeOrderPos;
    const pitcherBF = isTop ? this._homePitcherBF : this._awayPitcherBF;
    const batterTTO = isTop ? this._awayBatterTTO : this._homeBatterTTO;
    const teamSpeed = isTop ? this.awaySpeed : this.homeSpeed;
    const pitching = this._pitching[this._halfInning];

    const lineupSlot = orderPos % 9;
    const batter = battingTeam.lineup[lineupSlot];

    // Pre-play snapshot
    const prePlay = {
      bases: this._bases.slice(),
      outs: this._outs,
      score: this._score.slice(),
    };

    // --- Pre-PA events: stolen base + wild pitch ---
    const events = [];

    if (this._bases[0] || this._bases[1] || this._bases[2]) {
      // Stolen base check
      const sb = checkStolenBase(this._bases, this._outs, teamSpeed, this.rng);
      this._bases = sb.bases;
      this._outs += sb.outsAdded;
      if (sb.event) events.push(sb.event);

      // Check if half-inning ended on caught stealing
      if (this._outs >= 3) {
        return this._endHalfInningOnPrePA(batter, lineupSlot, prePlay, events, orderPos);
      }

      // Wild pitch check
      const wp = checkWildPitch(this._bases, this.rng);
      if (wp.occurred) {
        this._bases = wp.bases;
        this._addRuns(wp.runs, isTop);
        events.push("wild_pitch");
      }
    }

    // --- Select PA probabilities ---
    const isStarter = pitcherBF < STARTER_BATTER_LIMIT;
    let probs;
    let pitcherType;

    if (isStarter) {
      const tto = (batterTTO[lineupSlot] || 0) + 1;
      batterTTO[lineupSlot] = tto;
      const ttoIdx = Math.min(tto, 3) - 1; // 0, 1, 2
      probs = pitching.starterProbs[lineupSlot][ttoIdx];
      pitcherType = "starter";
    } else {
      // Select bullpen tier based on run differential
      const runDiff = isTop
        ? (this._score[1] - this._score[0])  // home perspective for home pitching
        : (this._score[0] - this._score[1]); // away perspective for away pitching
      const useHi = Math.abs(runDiff) <= BULLPEN_HIGH_LEV_THRESHOLD;
      probs = useHi ? pitching.bullpenHiProbs[lineupSlot] : pitching.bullpenLoProbs[lineupSlot];
      pitcherType = useHi ? "bullpenHi" : "bullpenLo";
    }

    // --- Sample outcome ---
    const outcomeIdx = this.rng.choice(probs);
    const outcome = OUTCOMES[outcomeIdx];

    // Advance pitcher BF and order position
    if (isTop) {
      this._homePitcherBF++;
      this._awayOrderPos++;
    } else {
      this._awayPitcherBF++;
      this._homeOrderPos++;
    }

    // --- Process outcome ---
    let runsOnPA = 0;

    if (outcomeIdx === _K) {
      this._outs++;
    } else if (outcomeIdx === _BB || outcomeIdx === _HBP) {
      const walk = handleWalk(this._bases);
      this._bases = walk.bases;
      runsOnPA = walk.runs;
    } else if (outcomeIdx === _HR || outcomeIdx === _3B || outcomeIdx === _2B || outcomeIdx === _1B) {
      const adv = advanceRunners(this._bases, outcome, this.rng);
      this._bases = adv.bases;
      runsOnPA = adv.runs;
    } else if (outcomeIdx === _OUT) {
      const out = handleOut(this._bases, this._outs, this.rng);
      this._bases = out.bases;
      runsOnPA = out.runs;
      if (out.extraOuts === -1) {
        // Reached on error — no out recorded
      } else {
        this._outs += 1 + out.extraOuts;
      }
    }

    // Add runs to score
    this._addRuns(runsOnPA, isTop);

    // Build probs object for the event
    const probsObj = {};
    for (let i = 0; i < 8; i++) probsObj[OUTCOMES[i]] = probs[i];

    // Post-play snapshot
    const postPlay = {
      bases: this._bases.slice(),
      outs: Math.min(this._outs, 3),
      score: this._score.slice(),
    };

    // Total runs scored this PA (including pre-PA events like wild pitches)
    const totalRuns = this._score[0] + this._score[1] - prePlay.score[0] - prePlay.score[1];

    // Build event object
    const event = {
      inning: this._inning,
      halfInning: this._halfInning,
      batter: {
        name: batter.name || `Batter ${lineupSlot + 1}`,
        slot: lineupSlot,
        bats: batter.bats || "R",
      },
      pitcherType,
      probs: probsObj,
      outcome,
      prePlay,
      postPlay,
      runsScored: totalRuns,
      events,
      description: describePlay(
        batter.name || `Batter ${lineupSlot + 1}`,
        outcome, runsOnPA, prePlay, postPlay, events
      ),
      gameOver: false,
      walkoff: false,
    };

    // --- Check walk-off ---
    if (!isTop && this._inning >= 9 && this._score[1] > this._score[0]) {
      this._gameOver = true;
      this._walkoff = true;
      event.gameOver = true;
      event.walkoff = true;
      return event;
    }

    // --- Check half-inning end ---
    if (this._outs >= 3) {
      this._transitionHalfInning();

      // Check game-over conditions after half-inning transition
      if (this._gameOver) {
        event.gameOver = true;
      }
    }

    return event;
  }

  /**
   * Simulate the rest of the game and return an array of all remaining events.
   */
  simToEnd() {
    const events = [];
    while (!this._gameOver) {
      const ev = this.nextPA();
      if (ev) events.push(ev);
    }
    return events;
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  /** Add runs to the appropriate team's score. */
  _addRuns(runs, isTop) {
    if (runs > 0) {
      if (isTop) this._score[0] += runs;
      else       this._score[1] += runs;
    }
  }

  /** Initialize state for the start of a half-inning. */
  _startHalfInning() {
    this._outs = 0;
    this._bases = [false, false, false];
    this._halfInningStarted = true;
    this._ghostRunnerPlaced = false;

    // Ghost runner in extras (runner on 2B)
    if (this._inning > 9) {
      this._bases[1] = true;
      this._ghostRunnerPlaced = true;
    }

    // In extras, force bullpen (set pitcher BF past starter limit)
    if (this._inning > 9) {
      if (this._halfInning === "top") {
        this._homePitcherBF = Math.max(this._homePitcherBF, STARTER_BATTER_LIMIT + 1);
      } else {
        this._awayPitcherBF = Math.max(this._awayPitcherBF, STARTER_BATTER_LIMIT + 1);
      }
    }
  }

  /**
   * Handle the case where a pre-PA event (caught stealing) ends the half-inning.
   * Returns an event object with no PA outcome.
   */
  _endHalfInningOnPrePA(batter, lineupSlot, prePlay, events, orderPos) {
    const postPlay = {
      bases: this._bases.slice(),
      outs: 3,
      score: this._score.slice(),
    };

    const event = {
      inning: this._inning,
      halfInning: this._halfInning,
      batter: {
        name: batter.name || `Batter ${lineupSlot + 1}`,
        slot: lineupSlot,
        bats: batter.bats || "R",
      },
      pitcherType: null,
      probs: null,
      outcome: null,
      prePlay,
      postPlay,
      runsScored: 0,
      events,
      description: "Caught stealing to end the inning.",
      gameOver: false,
      walkoff: false,
    };

    this._transitionHalfInning();
    if (this._gameOver) event.gameOver = true;
    return event;
  }

  /** Transition from one half-inning to the next, checking game-over conditions. */
  _transitionHalfInning() {
    this._halfInningStarted = false;

    if (this._halfInning === "top") {
      // After top half: check if bottom of 9+ can be skipped (home already leads)
      if (this._inning >= 9 && this._score[1] > this._score[0]) {
        this._gameOver = true;
        return;
      }
      this._halfInning = "bottom";
    } else {
      // After bottom half
      if (this._inning >= 9) {
        // Home wins (walk-off already handled in nextPA, but catch edge cases)
        if (this._score[1] > this._score[0]) {
          this._gameOver = true;
          return;
        }
        // Game tied after 9+ → continue to extras
        if (this._score[0] === this._score[1]) {
          if (this._inning >= 9) {
            this._extras++;
            if (this._extras > this._maxExtras) {
              // Max extras reached — game ends as tie (shouldn't happen often)
              this._gameOver = true;
              return;
            }
          }
        } else {
          // Away leads after bottom of 9+ → away wins
          this._gameOver = true;
          return;
        }
      }
      // Move to next inning
      this._inning++;
      this._halfInning = "top";
    }
  }
}

// =========================================================================
// Utility functions
// =========================================================================

/** Compute average speed for a lineup. */
function _avgSpeed(lineup) {
  let sum = 0;
  let count = 0;
  for (const b of lineup) {
    sum += (b.speed != null ? b.speed : LEAGUE_AVG_SPEED);
    count++;
  }
  return count > 0 ? sum / count : LEAGUE_AVG_SPEED;
}

// =========================================================================
// Exports (works as ES module or plain script)
// =========================================================================

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    OUTCOMES,
    LEAGUE_RATES,
    SimGame,
    createRNG,
    computePAProbabilities,
    oddsRatioBlend,
  };
}
