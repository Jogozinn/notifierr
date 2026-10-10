const CACHE = "notifierr-static-v1";
const STATIC = ["/notifierr-icon-192.png", "/notifierr-icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key.startsWith("notifierr-") && key !== CACHE).map((key) => caches.delete(key))),
    ),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin === self.location.origin && STATIC.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data ? event.data.text() : "A new opportunity is ready." };
  }
  event.waitUntil(
    self.registration.showNotification(payload.title || "Notifierr", {
      body: payload.body || "A new opportunity is ready.",
      icon: "/notifierr-icon-192.png",
      badge: "/notifierr-icon-192.png",
      tag: payload.tag || "notifierr-opportunity",
      data: {
        url: payload.url || (payload.item_id ? `/?item=${encodeURIComponent(payload.item_id)}` : "/"),
        item_id: payload.item_id || "",
        tier: payload.tier || "",
      },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const target = new URL(
    data.url || (data.item_id ? `/?item=${encodeURIComponent(data.item_id)}` : "/"),
    self.location.origin,
  );
  if (target.origin !== self.location.origin) {
    target.href = new URL("/", self.location.origin).href;
  }
  const targetHref = target.href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async (windows) => {
      const existing = windows.find((client) => {
        try {
          return new URL(client.url).origin === self.location.origin && typeof client.focus === "function";
        } catch {
          return false;
        }
      });
      if (existing) {
        try {
          if (new URL(existing.url).href !== targetHref && typeof existing.navigate === "function") {
            const navigated = await existing.navigate(targetHref);
            await (navigated || existing).focus();
          } else {
            await existing.focus();
          }
          return;
        } catch {
          // A closed or suspended iOS PWA client can reject navigation; open the deep link instead.
        }
      }
      return self.clients.openWindow(targetHref);
    }),
  );
});
