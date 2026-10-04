import { autoscanStateLabel, formatAutoscanInterval } from "../autoscanStatus.js";

const QUEUE_CARDS = [
  { key: "high_quality", label: "GEM" },
  { key: "profitable", label: "PROFITABLE" },
  { key: "review", label: "REVIEW" },
  { key: "unsent_actionable", label: "Unsent Actionable" },
  { key: "missed_opportunities", label: "Missed Opportunities" },
  { key: "priority_review", label: "Legacy Review" },
  { key: "needs_data", label: "Needs Data" },
  { key: "watched", label: "Watched" },
  { key: "promoted", label: "Promoted" },
  { key: "ignored", label: "Ignored" },
  { key: "rejected", label: "Rejected" },
  { key: "all", label: "All" },
];

export default function StatsBar({ stats, pollingStatus, counts, activeStatus, onChange }) {
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
      {stats?.scan_funnel?.cycle_id ? <ScanFunnel funnel={stats.scan_funnel} /> : null}
      <div className={`autoscan-status autoscan-${pollingStatus?.state || "unknown"}`} aria-live="polite">
        <strong>Autoscan: {autoscanStateLabel(pollingStatus)}</strong>
        <span>Background: {formatTime(pollingStatus?.last_background_succeeded_at)}</span>
        <span>Manual: {formatTime(pollingStatus?.last_manual_succeeded_at)}</span>
        <span>Next: {formatTime(pollingStatus?.next_scheduled_at)}</span>
        <span>Every: {formatAutoscanInterval(pollingStatus?.effective_interval_seconds)}</span>
        {pollingStatus?.reason ? <span className={pollingStatus.state === "running" ? "" : "autoscan-warning"}>{pollingStatus.reason}</span> : null}
        {!pollingStatus?.reason && pollingStatus?.last_error ? <span className="autoscan-warning">{pollingStatus.last_error}</span> : null}
        {!pollingStatus?.reason && !pollingStatus?.last_error && pollingStatus?.last_skip_reason ? <span>{pollingStatus.last_skip_reason}</span> : null}
      </div>
      <div className="last-scan">Latest listing found: {formatLastScan(stats)}</div>
    </section>
  );
}

function ScanFunnel({ funnel }) {
  const steps = [
    ["Found", funnel.found], ["Unique", funnel.unique], ["Detail fetched", funnel.detail_fetched],
    ["Scored", funnel.scored], ["Whole phones", funnel.whole_phones],
    ["Potentially profitable", funnel.potentially_profitable], ["GEM", funnel.gem],
    ["PROFITABLE", funnel.profitable], ["REVIEW", funnel.review],
    ["Alert attempted", funnel.alert_attempted], ["Alert sent", funnel.alert_sent],
  ];
  return (
    <div className="scan-funnel" aria-label={`Latest scan funnel cycle ${funnel.cycle_id}`}>
      {steps.map(([label, value], index) => (
        <span key={label}>{index ? "→ " : ""}{label}: <strong>{value ?? 0}</strong></span>
      ))}
      {funnel.lost_reasons?.length ? (
        <span className="funnel-losses">Main losses: {funnel.lost_reasons.map(([reason, count]) => `${reason} (${count})`).join(", ")}</span>
      ) : null}
    </div>
  );
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "Never";
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
  if (key === "unsent_actionable") {
    return stats.unsent_actionable ?? stats.notification_delivery?.never_notified_active_actionable ?? 0;
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
