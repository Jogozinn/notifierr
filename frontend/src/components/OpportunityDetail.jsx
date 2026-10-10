export default function OpportunityDetail({ item, state, error, onBack, onRetry }) {
  if (state === "loading") {
    return (
      <main className="app-shell opportunity-detail-shell">
        <button type="button" className="detail-back-button" onClick={onBack}>← Dashboard</button>
        <section className="opportunity-detail-state" aria-live="polite">
          <span className="empty-state-kicker">Opening notification</span>
          <strong>Loading this phone…</strong>
        </section>
      </main>
    );
  }

  if (state === "unavailable") {
    return (
      <main className="app-shell opportunity-detail-shell">
        <button type="button" className="detail-back-button" onClick={onBack}>← Dashboard</button>
        <section className="opportunity-detail-state" role="status">
          <span className="empty-state-kicker">Listing unavailable</span>
          <h1>This phone is no longer available in Notifierr.</h1>
          <p>It may have been archived or removed. Your notification link is still valid, but this listing cannot be loaded.</p>
        </section>
      </main>
    );
  }

  if (state === "error" || !item) {
    return (
      <main className="app-shell opportunity-detail-shell">
        <button type="button" className="detail-back-button" onClick={onBack}>← Dashboard</button>
        <section className="opportunity-detail-state" role="alert">
          <span className="empty-state-kicker">Could not load listing</span>
          <h1>Keep this notification open and try again.</h1>
          <p>{error || "A temporary connection error prevented the listing from loading."}</p>
          <button type="button" className="primary-button" onClick={onRetry}>Retry</button>
        </section>
      </main>
    );
  }

  const price = number(item.price);
  const totalCost = number(item.total_cost, price);
  const projectedProfit = number(item.estimated_profit_available ? item.estimated_profit : null,
    number(item.profit_mid, number(item.profit_high)));
  const risks = uniqueStrings([
    ...(item.risk_flags || []),
    ...(item.hard_reject_flags || []),
    ...(item.manual_review_reason ? [item.manual_review_reason] : []),
  ]);

  return (
    <main className="app-shell opportunity-detail-shell">
      <header className="opportunity-detail-header">
        <button type="button" className="detail-back-button" onClick={onBack}>← Dashboard</button>
        <span className={`detail-tier detail-tier-${String(item.alert_tier || "review").toLowerCase()}`}>
          {item.alert_tier || "Opportunity"}
        </span>
      </header>

      <article className="opportunity-detail-card">
        {item.image_url ? <img className="opportunity-detail-image" src={item.image_url} alt="" /> : null}
        <div className="opportunity-detail-copy">
          <p className="detail-model">{item.model || "iPhone opportunity"}</p>
          <h1>{item.title || item.model || "Phone listing"}</h1>
          <p className="detail-status-line">
            <span>{displayStatus(item)}</span>
            <span>{item.item_age_label || (item.stale ? "Archived listing" : "Age unknown")}</span>
          </p>
        </div>

        <section className="opportunity-detail-metrics" aria-label="Purchase and profit estimate">
          <Metric label="eBay asking price" value={currency(price)} />
          <Metric label="Total purchase cost" value={currency(totalCost)} />
          <Metric label="Projected profit" value={currency(projectedProfit)} important />
          <Metric label="Expected resale" value={currency(number(item.resale_mid, number(item.resale_value)))} />
        </section>

        <section className="opportunity-detail-risks" aria-label="Listing risks and review notes">
          <h2>Risks and review notes</h2>
          {risks.length ? (
            <ul>{risks.map((risk) => <li key={risk}>{humanize(risk)}</li>)}</ul>
          ) : <p>No risk flags are recorded for this listing.</p>}
          {item.user_status ? <p>Notifierr status: <strong>{humanize(item.user_status)}</strong></p> : null}
        </section>

        <section className="opportunity-detail-actions">
          {item.item_url ? (
            <a className="primary-button action-link" href={item.item_url} target="_blank" rel="noreferrer">
              Open this listing on eBay ↗
            </a>
          ) : <p>The eBay listing link is no longer available.</p>}
          <p>Listing ID: <code>{item.item_id}</code></p>
        </section>
      </article>
    </main>
  );
}

function Metric({ label, value, important = false }) {
  return (
    <div className={important ? "opportunity-detail-metric metric-important" : "opportunity-detail-metric"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function number(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && value !== null && value !== undefined ? parsed : fallback;
}

function currency(value) {
  return value === null || value === undefined
    ? "Not available"
    : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value);
}

function uniqueStrings(values) {
  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))];
}

function humanize(value) {
  return String(value || "").replaceAll("_", " ").replaceAll("-", " ");
}

function displayStatus(item) {
  return item.user_status && item.user_status !== "new"
    ? humanize(item.user_status)
    : humanize(item.status || "unclassified");
}
