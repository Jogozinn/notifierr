const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const TOKEN_KEY = "notifierr_access_token";

async function request(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...options.headers,
  };
  const token = getStoredToken();
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers,
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
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return response.json();
}

export function getStoredToken() {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token) {
  if (token) {
    window.localStorage.setItem(TOKEN_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

export function clearStoredToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function getAuthStatus() {
  return request("/auth/status");
}

export function login(email, password) {
  return request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function register(payload) {
  return request("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getCurrentUser() {
  return request("/auth/me");
}

export function logout() {
  return request("/auth/logout", {
    method: "POST",
  });
}

export function getAdminUsers() {
  return request("/admin/users");
}

export function getAdminInvites() {
  return request("/admin/invites");
}

export function createAdminInvite(payload) {
  return request("/admin/invites", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function revokeAdminInvite(inviteId) {
  return request(`/admin/invites/${encodeURIComponent(inviteId)}/revoke`, {
    method: "POST",
  });
}

export function createAdminUser(payload) {
  return request("/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAdminUser(userId, payload) {
  return request(`/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function enableAdminUser(userId) {
  return request(`/admin/users/${encodeURIComponent(userId)}/enable`, {
    method: "POST",
  });
}

export function disableAdminUser(userId) {
  return request(`/admin/users/${encodeURIComponent(userId)}/disable`, {
    method: "POST",
  });
}

export function getAdminUserUsage(userId) {
  return request(`/admin/users/${encodeURIComponent(userId)}/usage`);
}

export function getAdminScanStats() {
  return request("/admin/scan/stats");
}

export function getUserSettings() {
  return request("/settings");
}

export function updateUserSettings(payload) {
  return request("/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getUserKeywords() {
  return request("/settings/keywords");
}

export function createUserKeyword(payload) {
  return request("/settings/keywords", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateUserKeyword(keywordId, payload) {
  return request(`/settings/keywords/${encodeURIComponent(keywordId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteUserKeyword(keywordId) {
  return request(`/settings/keywords/${encodeURIComponent(keywordId)}`, {
    method: "DELETE",
  });
}

export function getUserNotifications() {
  return request("/settings/notifications");
}

export function getUserRepairOverrides() {
  return request("/settings/repair-overrides");
}

export function createUserRepairOverride(payload) {
  return request("/settings/repair-overrides", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteUserRepairOverride(overrideId) {
  return request(`/settings/repair-overrides/${encodeURIComponent(overrideId)}`, {
    method: "DELETE",
  });
}

export function getUserResaleOverrides() {
  return request("/settings/resale-overrides");
}

export function createUserResaleOverride(payload) {
  return request("/settings/resale-overrides", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function deleteUserResaleOverride(overrideId) {
  return request(`/settings/resale-overrides/${encodeURIComponent(overrideId)}`, {
    method: "DELETE",
  });
}

export function updateUserNotifications(payload) {
  return request("/settings/notifications", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function testDiscordNotification() {
  return request("/settings/notifications/test-discord", {
    method: "POST",
  });
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

export function rejectItem(itemId, reason = "") {
  return itemAction(itemId, "reject", { reason });
}

export function promoteItem(itemId) {
  return itemAction(itemId, "promote");
}

export function noteItem(itemId, note = "") {
  return itemAction(itemId, "note", { note });
}

export function getItemCorrection(itemId) {
  return request(`/items/${encodeURIComponent(itemId)}/correction`);
}

export function updateItemCorrection(itemId, payload) {
  return request(`/items/${encodeURIComponent(itemId)}/correction`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteItemCorrection(itemId) {
  return request(`/items/${encodeURIComponent(itemId)}/correction`, {
    method: "DELETE",
  });
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

export function updatePartCost(model, payload) {
  return request(`/repair-values/${encodeURIComponent(model)}/parts`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateGlobalPartCost(model, payload) {
  return request(`/admin/repair-values/${encodeURIComponent(model)}/parts`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function itemAction(itemId, action, body = null) {
  return request(`/items/${encodeURIComponent(itemId)}/${action}`, {
    method: "POST",
    body: body ? JSON.stringify(body) : undefined,
  });
}
