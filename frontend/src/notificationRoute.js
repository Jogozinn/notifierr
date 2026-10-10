export function itemIdFromSearch(search = "") {
  return new URLSearchParams(search).get("item")?.trim() || "";
}

export function itemDeepLink(itemId) {
  const value = String(itemId || "").trim();
  return value ? `/?item=${encodeURIComponent(value)}` : "/";
}

export function itemDetailPath(itemId) {
  return `/items/detail?item_id=${encodeURIComponent(String(itemId || ""))}`;
}

export function dashboardPathWithoutDeepLink(location = window.location) {
  return `${location.pathname || "/"}${location.hash || ""}`;
}
