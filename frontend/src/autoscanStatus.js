export function autoscanStateLabel(status) {
  if (!status) return "Loading";
  const labels = {
    running: "Running",
    scanning: "Scanning now",
    degraded: "Degraded",
    stale: "Stopped/stale",
    stopped: "Stopped",
    blocked: "Blocked",
    outside_window: "Outside hours",
    disabled: "Disabled",
  };
  return labels[status.state] || status.state || "Unknown";
}

export function formatAutoscanInterval(seconds) {
  if (!seconds) return "Unknown";
  return seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} sec`;
}

export function shouldRefreshDashboard(previousCycle, status) {
  const completedCycle = status?.last_background_cycle_id ?? status?.last_background_succeeded_at;
  return Boolean(previousCycle && completedCycle && completedCycle !== previousCycle);
}
