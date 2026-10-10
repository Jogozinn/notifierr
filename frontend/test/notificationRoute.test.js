import assert from "node:assert/strict";
import test from "node:test";
import { dashboardPathWithoutDeepLink, itemDeepLink, itemDetailPath, itemIdFromSearch } from "../src/notificationRoute.js";

test("item deep links encode the stable listing ID and parse it on app startup", () => {
  const id = "v1|phone/with space";
  const url = itemDeepLink(id);

  assert.equal(url, "/?item=v1%7Cphone%2Fwith%20space");
  assert.equal(itemIdFromSearch(new URL(url, "https://notifierr.example").search), id);
});

test("detail fetch URL targets the requested item directly", () => {
  assert.equal(itemDetailPath("v1|phone/22"), "/items/detail?item_id=v1%7Cphone%2F22");
});

test("closing an item deep link returns to the dashboard path", () => {
  assert.equal(
    dashboardPathWithoutDeepLink({ pathname: "/", hash: "#dashboard", search: "?item=phone-1" }),
    "/#dashboard",
  );
});
