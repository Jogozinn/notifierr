const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    let detail = `Request failed with ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(detail);
  }

  return response.json();
}

export function getStats() {
  return request("/stats");
}

export function getItems({ status = "all", userStatus = "", includeIgnored = false, includeStale = false, limit = 500 } = {}) {
  const params = new URLSearchParams({
    limit: String(limit),
    include_ignored: String(includeIgnored),
    include_stale: String(includeStale),
  });
  if (status && status !== "all") {
    params.set("status", status);
  }
  if (userStatus) {
    params.set("user_status", userStatus);
  }
  return request(`/items?${params.toString()}`);
}

export function runScan() {
  return request("/scan/run", {
    method: "POST",
    body: JSON.stringify({ notify: true }),
  });
}

export function reviewItem(itemId) {
  return itemAction(itemId, "review");
}

export function watchItem(itemId) {
  return itemAction(itemId, "watch");
}

export function ignoreItem(itemId, reason = "") {
  return itemAction(itemId, "ignore", { reason });
}

export function promoteItem(itemId) {
  return itemAction(itemId, "promote");
}

export function noteItem(itemId, note = "") {
  return itemAction(itemId, "note", { note });
}

export function ignoreSeller(itemId, reason = "") {
  return itemAction(itemId, "ignore-seller", { reason });
}

export function addIgnoredKeyword(keyword, reason = "") {
  return request("/ignored-keywords", {
    method: "POST",
    body: JSON.stringify({ keyword, reason }),
  });
}

function itemAction(itemId, action, body = null) {
  return request(`/items/${encodeURIComponent(itemId)}/${action}`, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
}
