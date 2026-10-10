import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const serviceWorkerSource = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");

function serviceWorkerWith(clients) {
  const listeners = new Map();
  const self = {
    location: { origin: "https://notifierr.example" },
    clients,
    addEventListener(name, listener) {
      listeners.set(name, listener);
    },
  };
  vm.runInNewContext(serviceWorkerSource, { self, URL });
  return listeners;
}

function clickEvent(data) {
  let task;
  let closed = false;
  return {
    event: {
      notification: { data, close: () => { closed = true; } },
      waitUntil(value) { task = value; },
    },
    wait: () => task,
    wasClosed: () => closed,
  };
}

test("notification click navigates and focuses the existing app window", async () => {
  const calls = [];
  const client = {
    url: "https://notifierr.example/",
    async navigate(url) { calls.push(["navigate", url]); this.url = url; return this; },
    async focus() { calls.push(["focus"]); },
  };
  const listeners = serviceWorkerWith({
    matchAll: async () => [client],
    openWindow: async (url) => calls.push(["open", url]),
  });
  const click = clickEvent({ item_id: "v1|phone id", url: "/?item=v1%7Cphone%20id" });

  listeners.get("notificationclick")(click.event);
  await click.wait();

  assert.equal(click.wasClosed(), true);
  assert.deepEqual(calls, [
    ["navigate", "https://notifierr.example/?item=v1%7Cphone%20id"],
    ["focus"],
  ]);
});

test("notification click opens the item URL when the PWA has no live window", async () => {
  const calls = [];
  const listeners = serviceWorkerWith({
    matchAll: async () => [],
    openWindow: async (url) => calls.push(url),
  });
  const click = clickEvent({ item_id: "phone-22", url: "/?item=phone-22" });

  listeners.get("notificationclick")(click.event);
  await click.wait();

  assert.deepEqual(calls, ["https://notifierr.example/?item=phone-22"]);
});

test("notification click falls back to opening the deep link if client navigation fails", async () => {
  const calls = [];
  const client = {
    url: "https://notifierr.example/",
    async navigate() { throw new Error("suspended client"); },
    async focus() { calls.push("focus"); },
  };
  const listeners = serviceWorkerWith({
    matchAll: async () => [client],
    openWindow: async (url) => calls.push(url),
  });
  const click = clickEvent({ item_id: "phone-23", url: "/?item=phone-23" });

  listeners.get("notificationclick")(click.event);
  await click.wait();

  assert.deepEqual(calls, ["https://notifierr.example/?item=phone-23"]);
});
