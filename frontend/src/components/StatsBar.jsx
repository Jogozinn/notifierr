import { autoscanStateLabel, formatAutoscanInterval } from "../autoscanStatus.js";

const PRIMARY_CARDS = [
  { key: "high_quality", label: "GEM", tone: "emerald" },
  { key: "profitable", label: "PROFITABLE", tone: "green" },
  { key: "review", label: "REVIEW", tone: "amber" },
];

export default function StatsBar({
  stats,
  pollingStatus,
  counts = {},
  countsComputedAt,
  countsLoading = false,
  activeStatus,
  onChange,
}) {
  const state = pollingStatus?.state || "unknown";
  return (
    <section className="stats-panel" aria-label="Actionable opportunity counts">
      <div className="stats-primary-grid">
        {PRIMARY_CARDS.map((card) => (
          <button
            className={`stat-cell stat-primary stat-tone-${card.tone}${card.key === activeStatus ? " active" : ""}`}
            key={card.key}
            onClick={() => onChange(card.key)}
            type="button"
          >
            <span className="stat-label">{card.label}</span>
            <strong>{counts[card.key] ?? <span className="stat-count-loading" aria-label="Loading count">···</span>}</strong>
          </button>
        ))}
      </div>
      <p className="counts-freshness" aria-live="polite">
        {countsLoading ? "Refreshing counts" : "Counts"}
        {countsComputedAt ? ` · last updated ${formatAge(countsComputedAt)}` : countsLoading ? "…" : " not available yet"}
      </p>

      <details className="secondary-dashboard-details">
        <summary>More statistics and scanner activity</summary>
        <div className="secondary-stat-grid">
          <SecondaryStat label="All in dashboard window" value={counts.all} />
          <SecondaryStat label="Rejected" value={counts.rejected ?? stats?.by_status?.rejected} />
          <SecondaryStat label="Needs data" value={counts.needs_data ?? stats?.needs_data} />
          <SecondaryStat label="Missed opportunities" value={counts.missed_opportunities} />
          <SecondaryStat label="Watched" value={counts.watched ?? stats?.by_user_status?.watched} />
          <SecondaryStat label="Promoted" value={counts.promoted ?? stats?.promoted} />
          <SecondaryStat label="Found today" value={stats?.found_today} />
          <SecondaryStat label="Fresh today" value={stats?.fresh_found} />
          <SecondaryStat label="Rejected today" value={stats?.rejected_today} />
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
        {stats?.scan_funnel?.cycle_id ? <ScanFunnel funnel={stats.scan_funnel} /> : null}
      </details>
    </section>
  );
}

function SecondaryStat({ label, value }) {
  return <div className="secondary-stat"><span>{label}</span><strong>{value ?? "Loading"}</strong></div>;
}

function ScanMeta({ label, value }) {
  return <div className="scan-meta"><span>{label}</span><strong>{value}</strong></div>;
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
      <div className="scan-funnel-header"><span>Latest scan funnel</span><strong>Cycle {funnel.cycle_id}</strong></div>
      <div className="scan-funnel-steps">
        {steps.map(([label, value]) => (
          <span className="funnel-step" key={label}><small>{label}</small><strong>{value ?? 0}</strong></span>
        ))}
      </div>
      {funnel.lost_reasons?.length ? (
        <div className="funnel-losses"><span>Top filters</span><p>{funnel.lost_reasons.slice(0, 5).map(([reason, count]) => `${formatReason(reason)} ${count}`).join(" · ")}</p></div>
      ) : null}
    </div>
  );
}

function formatReason(value) {
  return String(value || "").replaceAll("_", " ").toLowerCase().replace(/^./, (character) => character.toUpperCase());
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : "Never";
}

function formatAge(value) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "an unknown time ago";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatLastScan(stats) {
  if (!stats) return "Loading";
  return stats.latest_found_at ? new Date(stats.latest_found_at).toLocaleString() : "Never";
}
