export function dashboardItemsPath({ queue, sort, search, includeIgnored, includeStale, limit, offset }) {
  const params = new URLSearchParams({
    queue, sort, search, limit: String(limit), offset: String(offset),
    include_ignored: String(includeIgnored), include_stale: String(includeStale),
  });
  return `/items/dashboard?${params.toString()}`;
}
