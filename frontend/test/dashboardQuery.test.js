import test from "node:test";
import assert from "node:assert/strict";

import { dashboardItemsPath } from "../src/dashboardQuery.js";

test("queue, sort, search and page are sent to the backend before slicing", () => {
  const path = dashboardItemsPath({
    queue: "high_quality", sort: "profit", search: "iPhone 14 + unlocked",
    includeIgnored: false, includeStale: true, limit: 50, offset: 100,
  });
  const url = new URL(path, "http://localhost");
  assert.equal(url.pathname, "/items/dashboard");
  assert.equal(url.searchParams.get("queue"), "high_quality");
  assert.equal(url.searchParams.get("sort"), "profit");
  assert.equal(url.searchParams.get("search"), "iPhone 14 + unlocked");
  assert.equal(url.searchParams.get("limit"), "50");
  assert.equal(url.searchParams.get("offset"), "100");
  assert.equal(url.searchParams.get("include_ignored"), "false");
  assert.equal(url.searchParams.get("include_stale"), "true");
});
