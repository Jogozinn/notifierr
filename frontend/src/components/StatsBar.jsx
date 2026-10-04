import { autoscanStateLabel, formatAutoscanInterval } from "../autoscanStatus.js";

const QUEUE_CARDS = [
  { key: "high_quality", label: "GEM", tone: "emerald" },
  { key: "profitable", label: "Profitable", tone: "green" },
  { key: "review", label: "Review", tone: "amber" },
  { key: "unsent_actionable", label: "Unsent", tone: "cyan" },
  { key: "missed_opportunities", label: "Missed", tone: "rose" },
  { key: "priority_review", label: "Legacy review", tone: "violet" },
  { key: "needs_data", label: "Needs data", tone: "slate" },
  { key: "watched", label: "Watched", tone: "blue" },
  { key: "promoted", label: "Promoted", tone: "violet" },
  { key: "ignored", label: "Ignored", tone: "slate" },
  { key: "rejected", label: "Rejected", tone: "rose" },
  { key: "all", label: "All", tone: "slate" },
];

export default function StatsBar({ stats, pollingStatus, counts, activeStatus, onChange }) {
  const state = pollingStatus?.state || "unknown";
  return (
    <section className="stats-panel" aria-label="Deal review queues">
      <div className="stats-grid">
        {QUEUE_CARDS.map((card) => (
          <button
            className={`stat-cell stat-tone-${card.tone}${card.key === activeStatus ? " active" : ""}`}
            key={card.key}
            onClick={() => onChange(card.key)}
            type="button"
          >
            <span className="stat-label">{card.label}</span>
            <strong>{formatCount(stats, counts, card.key)}</strong>
          </button>
        ))}
      </div>

      <div className="scan-command-strip">
        <div className={`autoscan-state autoscan-${state}`} aria-live="polite">
          <span className="status-dot" aria-hidden="true" />
          <div>
            <span className="status-kicker">Scanner</span>
            <strong>{autoscanStateLabel(pollingStatus)}</strong>
          </div>
        </div>
        <div className="scan-meta-grid">
          <ScanMeta label="Background" value={formatTime(pollingStatus?.last_background_succeeded_at)} />
          <ScanMeta label="Manual" value={formatTime(pollingStatus?.last_manual_succeeded_at)} />
          <ScanMeta label="Next" value={formatTime(pollingStatus?.next_scheduled_at)} />
          <ScanMeta label="Cadence" value={formatAutoscanInterval(pollingStatus?.effective_interval_seconds)} />
          <ScanMeta label="Latest listing" value={formatLastScan(stats)} />
        </div>
        {(pollingStatus?.reason || pollingStatus?.last_error || pollingStatus?.last_skip_reason) ? (
          <div className={`scan-state-note ${state === "running" ? "" : "scan-state-note-warning"}`}>
            {pollingStatus?.reason || pollingStatus?.last_error || pollingStatus?.last_skip_reason}
          </div>
        ) : null}
      </div>

      <div className="live-summary" aria-label="Live scan summary">
        <SummaryChip label="Found today" value={stats?.found_today} />
        <SummaryChip label="Fresh" value={stats?.fresh_found} />
        <SummaryChip label="Rejected" value={stats?.rejected_today} />
        <SummaryChip label="Stale hidden" value={stats?.stale_items} />
      </div>

      {stats?.scan_funnel?.cycle_id ? <ScanFunnel funnel={stats.scan_funnel} /> : null}
    </section>
  );
}

function SummaryChip({ label, value }) {
  return (
    <span className="summary-chip">
      <span>{label}</span>
      <strong>{value ?? "—"}</strong>
    </span>
  );
}

function ScanMeta({ label, value }) {
  return (
    <div className="scan-meta">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ScanFunnel({ funnel }) {
  const steps = [
    ["Found", funnel.found], ["Unique", funnel.unique], ["Detail", funnel.detail_fetched],
    ["Scored", funnel.scored], ["Whole phones", funnel.whole_phones],
    ["Profit-ready", funnel.potentially_profitable], ["GEM", funnel.gem],
    ["Profitable", funnel.profitable], ["Review", funnel.review],
    ["Alert tried", funnel.alert_attempted], ["Sent", funnel.alert_sent],
  ];
  return (
    <div className="scan-funnel" aria-label={`Latest scan funnel cycle ${funnel.cycle_id}`}>
      <div className="scan-funnel-header">
        <span>Latest scan funnel</span>
        <strong>Cycle {funnel.cycle_id}</strong>
      </div>
      <div className="scan-funnel-steps">
        {steps.map(([label, value]) => (
          <span className="funnel-step" key={label}>
            <small>{label}</small>
            <strong>{value ?? 0}</strong>
          </span>
        ))}
      </div>
      {funnel.lost_reasons?.length ? (
        <div className="funnel-losses">
          <span>Top filters</span>
          <p>{funnel.lost_reasons.slice(0, 5).map(([reason, count]) => `${formatReason(reason)} ${count}`).join(" · ")}</p>
        </div>
      ) : null}
    </div>
  );
}

function formatReason(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/^./, (character) => character.toUpperCase());
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function formatCount(stats, counts, key) {
  if (!stats) {
    return "—";
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
