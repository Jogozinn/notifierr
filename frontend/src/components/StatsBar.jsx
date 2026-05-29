const QUEUE_CARDS = [
  { key: "high_quality", label: "Best Finds" },
  { key: "priority_review", label: "Priority Review" },
  { key: "needs_data", label: "Needs Data" },
  { key: "watched", label: "Watched" },
  { key: "promoted", label: "Promoted" },
  { key: "ignored", label: "Ignored" },
  { key: "rejected", label: "Rejected" },
  { key: "all", label: "All" },
];

export default function StatsBar({ stats, counts, activeStatus, onChange }) {
  return (
    <section className="stats-panel" aria-label="Deal review queues">
      <div className="stats-grid">
        {QUEUE_CARDS.map((card) => (
          <button
            className={card.key === activeStatus ? "stat-cell active" : "stat-cell"}
            key={card.key}
            onClick={() => onChange(card.key)}
            type="button"
          >
            <span>{card.label}</span>
            <strong>{formatCount(stats, counts, card.key)}</strong>
          </button>
        ))}
      </div>
      <div className="live-summary" aria-label="Live scan summary">
        <span>Found today: <strong>{stats?.found_today ?? "Loading"}</strong></span>
        <span>Fresh found: <strong>{stats?.fresh_found ?? "Loading"}</strong></span>
        <span>Rejected today: <strong>{stats?.rejected_today ?? "Loading"}</strong></span>
        <span>Stale hidden: <strong>{stats?.stale_items ?? "Loading"}</strong></span>
      </div>
      <div className="last-scan">Last scan: {formatLastScan(stats)}</div>
    </section>
  );
}

function formatCount(stats, counts, key) {
  if (!stats) {
    return "Loading";
  }
  if (counts && key in counts) {
    return counts[key];
  }
  if (key === "action_needed") {
    return stats.action_needed ?? 0;
  }
  if (key === "priority_review") {
    return stats.priority_review ?? 0;
  }
  if (key === "high_quality") {
    return stats.best_finds ?? ((stats.by_status?.candidate ?? 0) + (stats.by_status?.alerted ?? 0));
  }
  if (key === "needs_data") {
    return stats.needs_data ?? 0;
  }
  if (key === "promoted") {
    return stats.promoted ?? stats.by_user_status?.promoted ?? 0;
  }
  if (key === "watched" || key === "ignored") {
    return stats.by_user_status?.[key] ?? 0;
  }
  if (key === "rejected") {
    return stats.by_status?.rejected ?? 0;
  }
  if (key === "all") {
    return stats.total ?? 0;
  }
  return 0;
}

function formatLastScan(stats) {
  if (!stats) {
    return "Loading";
  }
  return stats.latest_found_at ? new Date(stats.latest_found_at).toLocaleString() : "Never";
}
