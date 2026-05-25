const STATUS_TEXT = {
  candidate: "High-quality",
  risky: "Risky",
  rejected: "Rejected",
  alerted: "Alerted",
};

export default function ItemTable({
  items,
  loading,
  onWatch,
  onReview,
  onIgnore,
  onIgnoreSeller,
  onPromote,
  onNote,
}) {
  if (loading) {
    return <div className="empty-state">Loading listings...</div>;
  }

  if (!items.length) {
    return <div className="empty-state">No listings match this queue.</div>;
  }

  return (
    <section className="deal-list">
      {items.map((item) => (
        <article key={item.item_id} className={cardClass(item)}>
          <div className="deal-media">
            {item.image_url ? (
              <img src={item.image_url} alt="" loading="lazy" />
            ) : (
              <div className="image-placeholder">No image</div>
            )}
          </div>

          <div className="deal-body">
            <div className="deal-title-row">
              <div>
                <h3>{item.title}</h3>
                <div className="subline">
                  <span>{item.model || "Unknown model"}</span>
                  <span>{sellerText(item)}</span>
                </div>
              </div>
              <div className="score-block">
                <span>Score</span>
                <strong>{formatNumber(item.score)}</strong>
              </div>
            </div>

            <div className="badge-row">
              <Badge tone={displayStatusTone(item)}>{displayStatusText(item)}</Badge>
              {isPreviouslyAlerted(item) ? <Badge tone="neutral">Previously alerted</Badge> : null}
              <Badge tone={item.stale ? "neutral" : "info"}>{item.item_age_label || "Age unknown"}</Badge>
              {item.stale ? <Badge tone="neutral">Stale archived</Badge> : null}
              <Badge tone={userTone(item.user_status)}>{item.user_status || "new"}</Badge>
              <PricingBadge item={item} />
              <ReviewBadges item={item} />
            </div>

            <div className="metric-grid">
              <Metric label="Price + shipping" value={`${currency(item.price)} + ${currency(item.shipping)} = ${currency(item.total_cost)}`} />
              <Metric label="Estimated parts" value={partsText(item)} />
              <Metric label={hasResaleRange(item) ? "Resale range" : "Estimated resale"} value={resaleText(item)} />
              <Metric label={hasProfitRange(item) ? "Profit range" : "Estimated profit"} value={profitText(item)} important={item.estimated_profit_available} />
            </div>

            <div className="detail-grid">
              <Detail label="Issue summary" values={item.positive_flags} />
              <Detail label="Pricing warning" text={item.pricing_warning || item.parts_pricing_label} />
              <Detail label="Manual-review reason" text={item.manual_review_reason} />
              <Detail label="Whole-phone confidence" text={item.whole_phone_confidence_passed ? "Passed" : "Needs review"} />
              <Detail label="Classification flags" values={item.listing_classification_flags} />
              <Detail label="Risk flags" values={item.risk_flags} />
              <Detail label="Reject flags" values={item.hard_reject_flags} danger />
            </div>

            {item.user_note ? <p className="user-note">Note: {item.user_note}</p> : null}
            {item.ignored_reason ? <p className="ignored-note">Ignored: {item.ignored_reason}</p> : null}
          </div>

          <div className="deal-actions">
            {item.item_url ? (
              <a className="action-link" href={item.item_url} target="_blank" rel="noreferrer">
                Open eBay
              </a>
            ) : null}
            <button type="button" onClick={() => onWatch(item)}>Watch</button>
            <button type="button" onClick={() => onReview(item)}>Mark reviewed</button>
            <button type="button" onClick={() => onIgnore(item)}>Ignore item</button>
            <button type="button" onClick={() => onIgnoreSeller(item)} disabled={!item.seller_username}>Ignore seller</button>
            <button type="button" onClick={() => onPromote(item)}>Promote/manual alert</button>
            <button type="button" onClick={() => onNote(item)}>Add note</button>
          </div>
        </article>
      ))}
    </section>
  );
}

function Metric({ label, value, important = false }) {
  return (
    <div className={important ? "metric metric-important" : "metric"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Detail({ label, values, text, danger = false }) {
  const safeValues = values || [];
  if (!text && !safeValues.length) {
    return null;
  }
  return (
    <div className="detail-block">
      <span>{label}</span>
      {text ? <strong>{text}</strong> : <FlagList values={safeValues} danger={danger} />}
    </div>
  );
}

function PricingBadge({ item }) {
  const label = pricingLabel(item);
  const verified = label === "Verified parts";
  return <Badge tone={verified ? "good" : "warn"}>{label}</Badge>;
}

function ReviewBadges({ item }) {
  const badges = [];
  if (item.whole_phone_confidence_passed === false) {
    badges.push("Not a whole phone");
  }
  if ((item.listing_classification_flags || []).some((flag) => flag.endsWith("_not_phone"))) {
    badges.push("Accessory/part listing");
  }
  if (item.has_repair_issue === false) {
    badges.push("No repair issue");
  }
  if (item.estimated_parts_cost_available === false) {
    badges.push("Missing part price");
  }
  if (!item.model || item.model === "unknown") {
    badges.push("Model unknown");
  }
  if (item.manual_review_needed) {
    badges.push("Manual review");
  }
  if ((item.manual_review_reason || "").includes("Expected profit below threshold")) {
    badges.push("Expected profit below threshold");
  }
  if ((item.manual_review_reason || "").includes("Only optimistic profit clears threshold")) {
    badges.push("Optimistic profit only");
  }
  if ((item.manual_review_reason || "").includes("Too cheap without proof")) {
    badges.push("Too cheap without proof");
    badges.push("Missing verification proof");
    badges.push("High upside but needs proof");
  }
  if ((item.manual_review_reason || "").includes("Model/spec mismatch")) {
    badges.push("Spec mismatch");
  }
  if ((item.manual_review_reason || "").includes("Read description listing")) {
    badges.push("Read description required");
  }
  if (item.parts_pricing_label === "Parts estimate not verified" && item.estimated_profit_available) {
    badges.push("Rough profit only");
  }
  return badges.map((badge) => (
    <Badge key={badge} tone="danger">
      {badge}
    </Badge>
  ));
}

function Badge({ children, tone = "neutral" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function FlagList({ values, danger = false }) {
  if (!values?.length) {
    return <strong>None</strong>;
  }
  return (
    <div className="flag-list">
      {values.map((value) => (
        <span className={danger ? "flag danger" : "flag"} key={value}>
          {formatFlag(value)}
        </span>
      ))}
    </div>
  );
}

function partsText(item) {
  if (item.estimated_parts_cost_available === false) {
    return "Estimated profit unavailable - part price missing or issue unknown";
  }
  return currency(item.estimated_parts_cost);
}

function profitText(item) {
  if (item.estimated_profit_available === false) {
    if (Number(item.resale_value || 0) <= 0) {
      return "Estimated profit unavailable - resale value missing";
    }
    if (item.estimated_parts_cost_available === false) {
      return "Estimated profit unavailable - part price missing or issue unknown";
    }
    if (item.has_repair_issue === false) {
      return "Estimated profit unavailable - no specific repair issue detected";
    }
    return item.pricing_warning || "Estimated profit unavailable";
  }
  if (hasProfitRange(item)) {
    const range = `${currency(item.profit_low)}-${currency(item.profit_high)}`;
    const expected = `expected ${currency(item.profit_mid || item.estimated_profit)}`;
    if (item.parts_pricing_label === "Parts estimate not verified") {
      return `Rough profit range - parts estimate not verified: ${range} (${expected})`;
    }
    return `${range} (${expected})`;
  }
  if (item.parts_pricing_label === "Parts estimate not verified") {
    return `Rough profit - parts estimate not verified: ${currency(item.estimated_profit)}`;
  }
  return currency(item.estimated_profit);
}

function resaleText(item) {
  if (hasResaleRange(item)) {
    const range = `${currency(item.resale_low)}-${currency(item.resale_high)}`;
    const details = [];
    if (item.resale_confidence) {
      details.push(item.resale_confidence);
    }
    if (Number(item.resale_sample_size || 0) > 0) {
      details.push(`${item.resale_sample_size} comps`);
    }
    return details.length ? `${range} (${details.join(", ")})` : range;
  }
  return Number(item.resale_value || 0) > 0 ? currency(item.resale_value) : "Resale value missing";
}

function pricingLabel(item) {
  if (item.parts_pricing_label) {
    return item.parts_pricing_label;
  }
  if (item.estimated_parts_cost_available === false) {
    return "Actual part price not on file";
  }
  if (Number(item.resale_value || 0) <= 0) {
    return "Resale value missing";
  }
  if (["verified_screenshot", "verified_screenshot_and_page"].includes(item.parts_pricing_status)) {
    return "Verified parts";
  }
  return "Parts estimate not verified";
}

function hasResaleRange(item) {
  const low = Number(item.resale_low || 0);
  const mid = Number(item.resale_mid || item.resale_value || 0);
  const high = Number(item.resale_high || 0);
  return low > 0 && mid > 0 && high > 0 && low !== high;
}

function hasProfitRange(item) {
  if (item.estimated_profit_available === false) {
    return false;
  }
  const low = Number(item.profit_low || 0);
  const mid = Number(item.profit_mid || item.estimated_profit || 0);
  const high = Number(item.profit_high || 0);
  return low !== high && [low, mid, high].some((value) => value !== 0);
}

function cardClass(item) {
  const classes = ["deal-card", `deal-${item.status}`, `user-${item.user_status || "new"}`];
  if (item.manual_review_needed) {
    classes.push("needs-review");
  }
  if (item.status === "candidate" || (item.status === "alerted" && item.alert_eligible === true)) {
    classes.push("high-quality");
  }
  return classes.join(" ");
}

function displayStatusText(item) {
  if (item.status === "alerted" && item.alert_eligible !== true) {
    if (item.hard_reject_flags?.length || item.whole_phone_confidence_passed === false) {
      return "Rejected";
    }
    return "Needs review";
  }
  return STATUS_TEXT[item.status] || item.status;
}

function displayStatusTone(item) {
  if (item.status === "alerted" && item.alert_eligible !== true) {
    if (item.hard_reject_flags?.length || item.whole_phone_confidence_passed === false) {
      return "danger";
    }
    return "neutral";
  }
  if (item.status === "candidate") return "good";
  if (item.status === "alerted") return "info";
  if (item.status === "rejected") return "danger";
  return "warn";
}

function isPreviouslyAlerted(item) {
  return item.status === "alerted" && item.alert_eligible !== true;
}

function userTone(status) {
  if (status === "watched" || status === "promoted") return "info";
  if (status === "ignored") return "danger";
  if (status === "reviewed") return "neutral";
  return "new";
}

function sellerText(item) {
  if (!item.seller_username) {
    return "Seller unknown";
  }
  const feedback = item.seller_feedback_percentage
    ? `${Number(item.seller_feedback_percentage).toFixed(1)}%`
    : "feedback n/a";
  const score = item.seller_feedback_score ? ` (${item.seller_feedback_score})` : "";
  return `${item.seller_username} - ${feedback}${score}`;
}

function currency(value) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

function formatNumber(value) {
  return Number(value || 0).toFixed(0);
}

function formatFlag(value) {
  return value.replaceAll("_", " ");
}
