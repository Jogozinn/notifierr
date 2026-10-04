import test from "node:test";
import assert from "node:assert/strict";

import { autoscanStateLabel, formatAutoscanInterval, shouldRefreshDashboard } from "../src/autoscanStatus.js";

test("renders supported autoscan states and interval", () => {
  assert.equal(autoscanStateLabel({ state: "running" }), "Running");
  assert.equal(autoscanStateLabel({ state: "scanning" }), "Scanning now");
  assert.equal(autoscanStateLabel({ state: "degraded" }), "Degraded");
  assert.equal(autoscanStateLabel({ state: "stale" }), "Stopped/stale");
  assert.equal(autoscanStateLabel({ state: "stopped" }), "Stopped");
  assert.equal(autoscanStateLabel({ state: "blocked" }), "Blocked");
  assert.equal(autoscanStateLabel({ state: "outside_window" }), "Outside hours");
  assert.equal(autoscanStateLabel({ state: "disabled" }), "Disabled");
  assert.equal(formatAutoscanInterval(600), "10 min");
});

test("refreshes listings and stats only after a new completed background cycle", () => {
  assert.equal(shouldRefreshDashboard(10, { last_background_cycle_id: 10 }), false);
  assert.equal(shouldRefreshDashboard(10, { last_background_cycle_id: 11 }), true);
  assert.equal(shouldRefreshDashboard(null, { last_background_cycle_id: 11 }), false);
});

test("manual completion cannot trigger background refresh", () => {
  assert.equal(shouldRefreshDashboard(10, { last_background_cycle_id: 10, last_manual_succeeded_at: "later" }), false);
});
