import { useEffect, useState } from "react";

const STATUS_TEXT = {
  candidate: "High-quality",
  risky: "Risky",
  rejected: "Rejected",
  alerted: "Alerted",
};

export default function ItemTable({
  items,
  loading,
  isAdmin = false,
  onWatch,
  onReview,
  onIgnore,
  onIgnoreSeller,
  onPromote,
  onReject,
  onUpdatePartCost,
  onUpdateGlobalPartCost,
  onSaveCorrection,
  onClearCorrection,
  onFeedback,
  onLabel,
  onOutcome,
  onNote,
}) {
  const [expandedIds, setExpandedIds] = useState(() => new Set());

  if (loading) {
    return <div className="empty-state"><span className="empty-state-kicker">Loading</span><strong>Refreshing deal intelligence…</strong></div>;
  }

  if (!items.length) {
    return <div className="empty-state"><span className="empty-state-kicker">Queue clear</span><strong>No listings match this view.</strong><p>Try another queue or broaden the filters.</p></div>;
  }

  return (
    <section className="deal-list">
      {items.map((item) => {
        const expanded = expandedIds.has(item.item_id);
        const reviewReason = primaryReviewReason(item);
        return (
          <article key={item.item_id} className={`${cardClass(item)}${expanded ? " deal-card-expanded" : ""}`}>
            <div className="deal-media">
              {item.image_url ? (
                <img src={item.image_url} alt="" loading="lazy" />
              ) : (
                <div className="image-placeholder">No image</div>
              )}
              <span className="media-model">{item.model && item.model !== "unknown" ? item.model.replace("iPhone ", "") : "?"}</span>
            </div>

            <div className="deal-body">
              <div className="deal-title-row">
                <div className="deal-title-copy">
                  <h3>{item.title}</h3>
                  <div className="subline">
                    <span>{modelStorageText(item)}</span>
                    <span>{sellerText(item)}</span>
                  </div>
                </div>
                <div className="score-block" title="Notifierr score">
                  <span>Score</span>
                  <strong>{formatNumber(item.score)}</strong>
                </div>
              </div>

              <div className="badge-row badge-row-primary">
                {item.alert_tier ? <Badge tone={tierTone(item.alert_tier)}>{item.alert_tier}</Badge> : null}
                <Badge tone={displayStatusTone(item)}>{displayStatusText(item)}</Badge>
                <AvailabilityBadges item={item} />
                <Badge tone={item.stale ? "neutral" : "info"}>{item.item_age_label || "Age unknown"}</Badge>
                <PricingBadge item={item} />
                {item.manual_review_needed ? <Badge tone="warning">Manual review</Badge> : null}
                {item.item_type === "component" ? <Badge tone="danger">Component</Badge> : null}
                {item.feedback_label ? <Badge tone={item.feedback_label === "GOOD" ? "success" : item.feedback_label === "BAD" ? "danger" : "warning"}>{item.feedback_label}</Badge> : null}
              </div>

              <div className="metric-grid">
                <Metric label="Landed cost" value={landedCostText(item)} />
                <Metric label="Repair" value={compactPartsText(item)} />
                <Metric label="Resale" value={compactResaleText(item)} />
                <Metric label="Expected profit" value={compactProfitText(item)} important={item.estimated_profit_available} />
                <Metric label="Floor" value={floorProfitText(item)} />
                {item.actual_net_profit !== null && item.actual_net_profit !== undefined ? <Metric label="Actual net" value={currency(item.actual_net_profit)} important /> : null}
              </div>

              {reviewReason ? (
                <div className="deal-review-reason">
                  <span>Decision note</span>
                  <strong>{reviewReason}</strong>
                </div>
              ) : null}

              {expanded ? (
                <>
                  <div className="expanded-signal-row" aria-label="Detailed listing signals">
                    <StorageBadges item={item} />
                    <ContextBadges item={item} />
                    <ReviewBadges item={item} />
                    {isPreviouslyAlerted(item) ? <Badge tone="neutral">Previously alerted</Badge> : null}
                    {item.stale ? <Badge tone="neutral">Stale archived</Badge> : null}
                    <Badge tone={userTone(item.user_status)}>{item.user_status || "new"}</Badge>
                    {item.outcome_status ? <Badge tone="info">{item.outcome_status}</Badge> : null}
                  </div>
                  <ExpandedDetails
                    item={item}
                    isAdmin={isAdmin}
                    onUpdatePartCost={onUpdatePartCost}
                    onUpdateGlobalPartCost={onUpdateGlobalPartCost}
                    onSaveCorrection={onSaveCorrection}
                    onClearCorrection={onClearCorrection}
                    onOutcome={onOutcome}
                  />
                  <div className="deal-secondary-actions">
                    <div className="secondary-action-buttons">
                      <button type="button" onClick={() => onIgnore(item)}>Ignore item</button>
                      <button type="button" onClick={() => onIgnoreSeller(item)} disabled={!item.seller_username}>Ignore seller</button>
                      <button type="button" onClick={() => onPromote(item)}>Promote / manual alert</button>
                      <button type="button" className="danger-button" onClick={() => onReject(item)}>Reject</button>
                      <button type="button" onClick={() => onNote(item)}>Add note</button>
                    </div>
                    <div className="feedback-group">
                      <span>Your assessment</span>
                      <div className="quick-feedback" aria-label="Your assessment">
                        {['GOOD', 'BAD', 'UNSURE'].map((label) => (
                          <button key={label} type="button" aria-pressed={item.feedback_label === label} onClick={() => onLabel(item, label)}>{label}</button>
                        ))}
                      </div>
                    </div>
                    <div className="feedback-group feedback-group-wide">
                      <span>Why this result is wrong</span>
                      <div className="quick-feedback" aria-label="Deal feedback">
                        <button type="button" onClick={() => onFeedback(item, "good_deal")}>Good deal</button>
                        <button type="button" onClick={() => onFeedback(item, "not_profitable")}>Not profitable</button>
                        <button type="button" onClick={() => onFeedback(item, "wrong_model")}>Wrong model</button>
                        <button type="button" onClick={() => onFeedback(item, "wrong_storage")}>Wrong storage</button>
                        <button type="button" onClick={() => onFeedback(item, "wrong_damage")}>Wrong damage</button>
                        <button type="button" onClick={() => onFeedback(item, "accessory_not_phone")}>Accessory / part</button>
                        <button type="button" onClick={() => onFeedback(item, "too_risky")}>Too risky</button>
                        <button type="button" onClick={() => onFeedback(item, "already_sold")}>Already sold</button>
                        <button type="button" onClick={() => onFeedback(item, "pricing_wrong")}>Pricing wrong</button>
                      </div>
                    </div>
                  </div>
                </>
              ) : null}

              {item.user_note ? <p className={hasNegativeUserNote(item) ? "user-note user-note-warning" : "user-note"}>Note: {item.user_note}</p> : null}
              {item.ignored_reason ? <p className="ignored-note">Ignored: {item.ignored_reason}</p> : null}
            </div>

            <div className="deal-actions">
              {item.item_url ? (
                <a className="action-link action-ebay" href={item.item_url} target="_blank" rel="noreferrer">
                  Open eBay <span aria-hidden="true">↗</span>
                </a>
              ) : null}
              <button type="button" className="action-watch" onClick={() => onWatch(item)}>Watch</button>
              <button type="button" className="action-review" onClick={() => onReview(item)}>Reviewed</button>
              <button type="button" className="action-details" onClick={() => toggleExpanded(item.item_id)} aria-expanded={expanded}>
                {expanded ? "Hide details" : "Details"}
              </button>
            </div>
          </article>
        );
      })}
    </section>
  );

  function toggleExpanded(itemId) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  }
}

function landedCostText(item) {
  const total = Number(item.total_cost ?? 0);
  if (Number.isFinite(total) && total > 0) return currency(total);
  return `${currency(item.price)} + ${currency(item.shipping)}`;
}

function compactPartsText(item) {
  if (item.estimated_parts_cost_available === false) return "Unknown";
  return currency(item.estimated_parts_cost);
}

function compactResaleText(item) {
  return Number(item.resale_mid || item.resale_value || 0) > 0
    ? currency(item.resale_mid || item.resale_value)
    : "Unknown";
}

function compactProfitText(item) {
  if (item.estimated_profit_available === false) return "Unavailable";
  const value = currency(item.profit_mid || item.estimated_profit);
  return item.parts_pricing_label === "Parts estimate not verified" ? `~${value}` : value;
}

function tierTone(tier) {
  return { GEM: "success", PROFITABLE: "info", REVIEW: "warning" }[tier] || "neutral";
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

function DescriptionDetail({ text }) {
  return (
    <div className="detail-block expanded-description-block">
      <span>eBay description</span>
      <div className="description-scroll">{text}</div>
    </div>
  );
}

function PricingBadge({ item }) {
  const label = pricingLabel(item);
  const verified = label === "Verified parts";
  return <Badge tone={verified ? "good" : "warn"}>{label}</Badge>;
}

function StorageBadges({ item }) {
  const badges = [];
  if (!item.storage_capacity) {
    badges.push({ label: "Storage unknown", tone: "warn" });
  }
  if (item.resale_source !== "storage_specific" && Number(item.resale_value || item.resale_mid || 0) > 0) {
    badges.push({ label: "Model resale fallback", tone: "warn" });
  }
  if (item.storage_resale_warning) {
    badges.push({ label: "Storage resale warning", tone: "warn" });
  }
  if ((item.manual_review_reason || "").includes("Profit depends on mint resale")) {
    badges.push({ label: "Mint upside only", tone: "warn" });
  }
  if (item.detail_check_age_minutes !== null && item.detail_check_age_minutes !== undefined) {
    badges.push({ label: "Description checked", tone: "info" });
  }
  if (riskPhraseFlags(item).length) {
    badges.push({ label: "Risk phrase found", tone: "danger" });
  }
  return badges.map((badge) => (
    <Badge key={badge.label} tone={badge.tone}>
      {badge.label}
    </Badge>
  ));
}

function ExpandedDetails({ item, isAdmin, onUpdatePartCost, onUpdateGlobalPartCost, onSaveCorrection, onClearCorrection, onOutcome }) {
  return (
    <div className="expanded-details">
      <DescriptionDetail text={cleanDescription(item.raw_description) || "Description not available from API."} />
      <RepairValuesDetail item={item} isAdmin={isAdmin} onUpdatePartCost={onUpdatePartCost} onUpdateGlobalPartCost={onUpdateGlobalPartCost} />
      <ItemCorrectionDetail item={item} onSaveCorrection={onSaveCorrection} onClearCorrection={onClearCorrection} />
      <OutcomeEditor key={item.item_id} item={item} onSave={onOutcome} />
      <Detail label="Item type reason" text={item.item_type_reason} />
      <Detail label="Good resale range" text={goodResaleRangeText(item)} />
      <Detail label="Good profit range" text={goodProfitRangeText(item)} />
      <Detail label="Mint resale range" text={mintResaleRangeText(item)} />
      <Detail label="Mint profit range" text={mintProfitRangeText(item)} />
      <Detail label="Item specifics" values={itemSpecifics(item)} />
      <Detail label="Availability" text={availabilityText(item)} />
      <Detail label="Buying options" text={buyingOptionText(item)} />
      <Detail label="Item end time" text={item.item_end_at} />
      <Detail label="Detail check age" text={item.detail_check_age_label || "Detail check unknown"} />
      <Detail label="Context labels" values={contextLabelFlags(item)} />
      <Detail label="Risk phrases found" values={riskPhraseFlags(item)} danger />
      <Detail label="Proof signals found" values={item.positive_flags} />
      <Detail label="Classification flags" values={item.listing_classification_flags} />
      <Detail label="Reject flags" values={item.hard_reject_flags} danger />
      <Detail label="Raw warning/review reasons" text={item.manual_review_reason || item.pricing_warning} />
      <Detail label="Storage source" text={storageSourceText(item)} />
      <Detail label="Resale source" text={resaleSourceText(item)} />
      <Detail label="Mint upside only" text={mintUpsideText(item)} />
    </div>
  );
}

const OUTCOME_MONEY_FIELDS = [
  ['purchase_price', 'Purchase price'], ['purchase_tax', 'Purchase tax'],
  ['inbound_shipping', 'Inbound shipping'], ['parts_cost', 'Actual parts cost'],
  ['other_repair_cost', 'Other repair cost'], ['sale_price', 'Sale price'],
  ['selling_fees', 'Selling fees'], ['outbound_shipping', 'Outbound shipping'],
  ['refund_amount', 'Refunds / returns'], ['other_cost', 'Other cost'],
];

function OutcomeEditor({ item, onSave }) {
  const [values, setValues] = useState({ status: item.outcome_status || 'SKIPPED' });
  return (
    <form className="outcome-editor" onSubmit={(event) => {
      event.preventDefault();
      onSave(item, Object.fromEntries(Object.entries(values).filter(([, value]) => value !== '')));
    }}>
      <h4>Business outcome</h4>
      <label>Status <select value={values.status} onChange={(event) => setValues({ ...values, status: event.target.value })}>
        {['SKIPPED', 'PURCHASED', 'SOLD', 'FAILED_REPAIR'].map((status) => <option key={status}>{status}</option>)}
      </select></label>
      {OUTCOME_MONEY_FIELDS.map(([field, label]) => (
        <label key={field}>{label} <input type="number" min="0" step="0.01" placeholder={item[field] ?? ''} value={values[field] ?? ''} onChange={(event) => setValues({ ...values, [field]: event.target.value })} /></label>
      ))}
      <label>Purchase date <input type="date" value={values.purchase_date ?? ''} onChange={(event) => setValues({ ...values, purchase_date: event.target.value })} /></label>
      <label>Sale date <input type="date" value={values.sale_date ?? ''} onChange={(event) => setValues({ ...values, sale_date: event.target.value })} /></label>
      <label>Repair type <input value={values.actual_repair_type ?? ''} onChange={(event) => setValues({ ...values, actual_repair_type: event.target.value })} /></label>
      <label>Note <input value={values.note ?? ''} onChange={(event) => setValues({ ...values, note: event.target.value })} /></label>
      <button type="submit">Save outcome</button>
    </form>
  );
}

function ContextBadges({ item }) {
  const badges = [];
  if (item.user_status === "promoted") {
    badges.push({ label: "Manually promoted", tone: "info" });
  }
  if (item.user_status === "rejected") {
    badges.push({ label: "User rejected", tone: "danger" });
  }
  if (hasActiveCorrection(item)) {
    badges.push({ label: "Item corrected", tone: "warn" });
  }
  for (const flag of contextLabelFlags(item)) {
    badges.push({ label: contextFlagLabel(flag), tone: "neutral" });
  }
  return badges.map((badge) => (
    <Badge key={badge.label} tone={badge.tone}>
      {badge.label}
    </Badge>
  ));
}

function RepairValuesDetail({ item, isAdmin, onUpdatePartCost, onUpdateGlobalPartCost }) {
  const [part, setPart] = useState(defaultPartKey(item));
  const [cost, setCost] = useState(item.estimated_parts_cost ? String(item.estimated_parts_cost) : "");
  const [note, setNote] = useState("");

  useEffect(() => {
    setPart(defaultPartKey(item));
    setCost(item.estimated_parts_cost ? String(item.estimated_parts_cost) : "");
    setNote("");
  }, [item.item_id, item.effective_part_key, item.estimated_parts_cost]);

  function submit(event) {
    event.preventDefault();
    const parsedCost = Number(cost);
    if (!Number.isFinite(parsedCost) || parsedCost < 0) {
      return;
    }
    onUpdatePartCost(item, {
      part,
      cost: parsedCost,
      note,
      source: "manual_dashboard",
    });
    setNote("");
  }

  function submitGlobalBaseline(event) {
    event.preventDefault();
    if (!isAdmin || typeof onUpdateGlobalPartCost !== "function") {
      return;
    }
    const parsedCost = Number(cost);
    if (!Number.isFinite(parsedCost) || parsedCost < 0) {
      return;
    }
    onUpdateGlobalPartCost(item, {
      part,
      cost: parsedCost,
      note,
      source: "admin_dashboard_baseline",
    });
    setNote("");
  }

  return (
    <div className="detail-block repair-values-block">
      <span>Repair values</span>
      <div className="repair-values-summary">
        <strong>{item.model || "Unknown model"} · {item.storage_capacity || "storage unknown"}</strong>
        <p>Part used: {formatFlag(item.effective_part_key || defaultPartKey(item))}</p>
        <p>Baseline cost: {item.baseline_part_cost === null || item.baseline_part_cost === undefined ? "Missing" : currency(item.baseline_part_cost)}</p>
        <p>My override: {item.user_override_part_cost === null || item.user_override_part_cost === undefined ? "None" : currency(item.user_override_part_cost)}</p>
        <p>Effective cost: {item.effective_part_cost === null || item.effective_part_cost === undefined ? "Missing" : currency(item.effective_part_cost)}</p>
        <p>Source: {formatFlag(item.effective_part_source || "global_default")}</p>
        <p>{item.parts_pricing_status || "fallback"} · {item.parts_pricing_note || "No pricing note"}</p>
      </div>
      <form className="part-cost-form" onSubmit={submit}>
        <select value={part} onChange={(event) => setPart(event.target.value)} aria-label="Part to update">
          <option value="screen_budget">Screen budget</option>
          <option value="screen_safe">Screen safe</option>
          <option value="screen_premium">Screen premium</option>
          <option value="battery">Battery</option>
          <option value="back_glass">Back glass</option>
          <option value="camera_lens">Camera lens</option>
          <option value="charging_port">Charging port</option>
        </select>
        <input
          type="number"
          min="0"
          step="0.01"
          value={cost}
          onChange={(event) => setCost(event.target.value)}
          placeholder="Cost"
          aria-label="Part cost"
        />
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Optional note"
          aria-label="Part cost note"
        />
        <button type="submit" disabled={!item.model || item.model === "unknown"}>Save as my part cost</button>
        {isAdmin ? (
          <button type="button" onClick={submitGlobalBaseline} disabled={!item.model || item.model === "unknown"}>
            Update global baseline
          </button>
        ) : null}
      </form>
    </div>
  );
}

function ItemCorrectionDetail({ item, onSaveCorrection, onClearCorrection }) {
  const correction = item.user_item_correction || {};
  const [model, setModel] = useState(correction.corrected_model || "");
  const [storage, setStorage] = useState(correction.corrected_storage_capacity || "");
  const [issueType, setIssueType] = useState(correction.corrected_issue_type || "");
  const [partCost, setPartCost] = useState(correction.corrected_part_cost ?? "");
  const [note, setNote] = useState(correction.note || "");

  useEffect(() => {
    setModel(correction.corrected_model || "");
    setStorage(correction.corrected_storage_capacity || "");
    setIssueType(correction.corrected_issue_type || "");
    setPartCost(correction.corrected_part_cost ?? "");
    setNote(correction.note || "");
  }, [
    item.item_id,
    correction.corrected_model,
    correction.corrected_storage_capacity,
    correction.corrected_issue_type,
    correction.corrected_part_cost,
    correction.note,
  ]);

  function submit(event) {
    event.preventDefault();
    const payload = {
      corrected_model: model.trim() || null,
      corrected_storage_capacity: storage || null,
      corrected_issue_type: issueType || null,
      corrected_part_cost: partCost === "" ? null : Number(partCost),
      note: note.trim(),
    };
    if (payload.corrected_part_cost !== null && (!Number.isFinite(payload.corrected_part_cost) || payload.corrected_part_cost < 0)) {
      return;
    }
    onSaveCorrection(item, payload);
  }

  return (
    <div className="detail-block repair-values-block">
      <span>Item correction</span>
      <div className="repair-values-summary">
        <p>Raw detected model: {item.raw_detected_model || "Unknown model"}</p>
        <p>Raw detected storage: {item.raw_detected_storage_capacity || "Unknown storage"}</p>
        <p>Raw detected issue: {formatFlag(item.raw_detected_issue_type || "unknown")}</p>
        <p>Corrected model: {correction.corrected_model || "None"}</p>
        <p>Corrected storage: {correction.corrected_storage_capacity || "None"}</p>
        <p>Corrected issue: {correction.corrected_issue_type ? formatFlag(correction.corrected_issue_type) : "None"}</p>
        <p>Effective issue: {formatFlag(item.effective_issue_type || "unknown")}</p>
      </div>
      <form className="item-correction-form" onSubmit={submit}>
        <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Corrected model" aria-label="Corrected model" />
        <select value={storage} onChange={(event) => setStorage(event.target.value)} aria-label="Corrected storage">
          <option value="">Storage unchanged</option>
          <option value="64GB">64GB</option>
          <option value="128GB">128GB</option>
          <option value="256GB">256GB</option>
          <option value="512GB">512GB</option>
          <option value="1TB">1TB</option>
        </select>
        <select value={issueType} onChange={(event) => setIssueType(event.target.value)} aria-label="Corrected issue">
          <option value="">Issue unchanged</option>
          <option value="cracked_screen">Cracked screen</option>
          <option value="screen_display_issue">Screen display issue</option>
          <option value="back_glass_cracked">Back glass cracked</option>
          <option value="bad_battery">Bad battery</option>
          <option value="camera_lens_cracked">Camera lens cracked</option>
          <option value="charging_port_issue">Charging port issue</option>
          <option value="multiple_issues">Multiple issues</option>
          <option value="unknown">Unknown</option>
        </select>
        <input
          type="number"
          min="0"
          step="0.01"
          value={partCost}
          onChange={(event) => setPartCost(event.target.value)}
          placeholder="One-off part cost"
          aria-label="One-off part cost"
        />
        <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Correction note" aria-label="Correction note" />
        <button type="submit">Save correction</button>
        <button type="button" onClick={() => onClearCorrection(item)} disabled={!hasActiveCorrection(item)}>Clear correction</button>
      </form>
    </div>
  );
}

function AvailabilityBadges({ item }) {
  const badges = [];
  const status = item.availability_status || "unknown";
  const buyingOption = item.buying_option_summary || "unknown";
  if (["sold", "ended", "unavailable"].includes(status)) {
    badges.push({ label: "Sold/Ended", tone: "danger" });
  } else if (status === "active") {
    badges.push({ label: "Active", tone: "good" });
  }
  if (buyingOption === "auction") {
    badges.push({ label: auctionLabel(item), tone: "warn" });
  } else if (buyingOption === "auction_and_buy_it_now") {
    badges.push({ label: "Auction + Buy It Now", tone: "warn" });
  } else if (buyingOption === "buy_it_now") {
    badges.push({ label: "Buy It Now", tone: "info" });
  } else if (buyingOption === "best_offer") {
    badges.push({ label: "Best Offer", tone: "info" });
  }
  return badges.map((badge) => (
    <Badge key={badge.label} tone={badge.tone}>
      {badge.label}
    </Badge>
  ));
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
  if ((item.manual_review_reason || "").includes("Only upside case works")) {
    badges.push("Only upside case works");
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
  if ((item.manual_review_reason || "").includes("Auction - not urgent")) {
    badges.push("Auction - not urgent");
  }
  if ((item.manual_review_reason || "").includes("Best Offer available")) {
    badges.push("Best Offer available");
  }
  if ((item.manual_review_reason || "").includes("Profit depends on mint resale")) {
    badges.push("Profit depends on mint resale");
  }
  if (hasNegativeUserNote(item)) {
    badges.push("User note warning");
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
  if (item.parts_pricing_label === "Parts estimate not verified") {
    return `Rough ${currency(item.profit_mid || item.estimated_profit)}`;
  }
  return currency(item.profit_mid || item.estimated_profit);
}

function resaleMetricLabel(item) {
  if (item.resale_source === "storage_specific" && item.resale_storage_used) {
    return `Expected resale - ${item.resale_storage_used} ${item.resale_condition_used || "Good"}`;
  }
  if (!item.storage_capacity && item.resale_source === "model_range") {
    return "Expected resale - storage unknown";
  }
  if (item.resale_source === "model_range") {
    return "Expected resale - model estimate";
  }
  return "Expected resale";
}

function primaryReviewReason(item) {
  const reason = item.storage_resale_warning || item.manual_review_reason || item.pricing_warning || "";
  return reason.split(";").map((part) => part.trim()).find(Boolean) || "";
}

function resaleText(item) {
  return Number(item.resale_mid || item.resale_value || 0) > 0 ? currency(item.resale_mid || item.resale_value) : "Resale value missing";
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

function floorProfitText(item) {
  if (item.estimated_profit_available === false) {
    return "Unavailable";
  }
  return currency(item.profit_low || 0);
}

function suggestedPartKey(item) {
  const flags = item.positive_flags || [];
  if (flags.some((flag) => ["cracked_screen", "screen_display_issue", "bad_oled"].includes(flag))) {
    return "screen_safe";
  }
  if (flags.includes("bad_battery")) {
    return "battery";
  }
  if (flags.includes("back_glass_cracked")) {
    return "back_glass";
  }
  if (flags.includes("camera_lens_cracked")) {
    return "camera_lens";
  }
  if (flags.includes("charging_port_issue")) {
    return "charging_port";
  }
  return "";
}

function defaultPartKey(item) {
  return item.effective_part_key || suggestedPartKey(item) || "screen_safe";
}

function hasActiveCorrection(item) {
  const correction = item.user_item_correction || {};
  return Boolean(
    correction.id
    || correction.corrected_model
    || correction.corrected_storage_capacity
    || correction.corrected_issue_type
    || correction.corrected_part_cost !== null && correction.corrected_part_cost !== undefined
    || correction.note
  );
}

function contextLabelFlags(item) {
  return (item.risk_flags || []).filter((flag) => ["for_parts", "as_is"].includes(flag));
}

function riskPhraseFlags(item) {
  return (item.risk_flags || []).filter((flag) => !["for_parts", "as_is"].includes(flag));
}

function contextFlagLabel(flag) {
  if (flag === "for_parts") return "For parts label";
  if (flag === "as_is") return "As-is label";
  return formatFlag(flag);
}

function goodResaleRangeText(item) {
  if (!hasResaleRange(item)) {
    return Number(item.resale_mid || item.resale_value || 0) > 0 ? currency(item.resale_mid || item.resale_value) : "Resale value missing";
  }
  return `${currency(item.resale_low)}-${currency(item.resale_high)} (expected ${currency(item.resale_mid || item.resale_value)})`;
}

function goodProfitRangeText(item) {
  if (!hasProfitRange(item)) {
    return profitText(item);
  }
  return `${currency(item.profit_low)}-${currency(item.profit_high)} (expected ${currency(item.profit_mid || item.estimated_profit)})`;
}

function mintResaleRangeText(item) {
  if (Number(item.mint_resale_mid || 0) <= 0) {
    return "";
  }
  return `${currency(item.mint_resale_low)}-${currency(item.mint_resale_high)} (upside ${currency(item.mint_resale_mid)})`;
}

function mintProfitRangeText(item) {
  if (Number(item.mint_resale_mid || 0) <= 0) {
    return "";
  }
  return `${currency(item.mint_profit_low)}-${currency(item.mint_profit_high)} (upside ${currency(item.mint_profit_mid)})`;
}

function descriptionPreviewText(item) {
  if (item.detail_check_age_label && item.detail_check_age_label !== "Detail check unknown") {
    return item.detail_check_age_label.replace("Details", "Description");
  }
  return "Description not checked";
}

function cardClass(item) {
  const classes = ["deal-card", `deal-${item.status}`, `user-${item.user_status || "new"}`];
  if (["sold", "ended", "unavailable"].includes(item.availability_status || "unknown")) {
    classes.push("deal-unavailable");
  }
  if (hasNegativeUserNote(item)) {
    classes.push("note-warning");
  }
  if (item.manual_review_needed) {
    classes.push("needs-review");
  }
  if (item.status === "candidate" || (item.status === "alerted" && item.alert_eligible === true)) {
    classes.push("high-quality");
  }
  return classes.join(" ");
}

function modelStorageText(item) {
  const model = item.model || "Unknown model";
  return item.storage_capacity ? `${model} · ${item.storage_capacity}` : model;
}

function auctionLabel(item) {
  if ((item.manual_review_reason || "").includes("Auction - not urgent")) {
    return "Auction - not urgent";
  }
  return "Auction";
}

function hasNegativeUserNote(item) {
  const note = (item.user_note || "").toLowerCase();
  return ["sold", "too high", "board damage", "no ic", "auction"].some((phrase) => note.includes(phrase));
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

function availabilityText(item) {
  const status = item.availability_status || "unknown";
  return item.availability_note || status;
}

function buyingOptionText(item) {
  return formatFlag(item.buying_option_summary || "unknown");
}

function storageSourceText(item) {
  const parts = [];
  if (item.storage_capacity) {
    parts.push(`${item.storage_capacity} from ${item.storage_source || "unknown source"}`);
  } else {
    parts.push("Storage not detected");
  }
  if (item.storage_confidence) {
    parts.push(`${item.storage_confidence} confidence`);
  }
  if (item.resale_source) {
    parts.push(`resale source: ${resaleMarketSourceLabel(item.resale_market_source || item.resale_source)}`);
  }
  if (item.storage_resale_warning) {
    parts.push(item.storage_resale_warning);
  }
  return parts.join("; ");
}

function resaleSourceText(item) {
  const source = resaleMarketSourceLabel(item.resale_market_source || item.resale_source || "missing");
  const condition = item.resale_condition_used || "Good";
  return `${source}; condition used: ${condition}`;
}

function mintUpsideText(item) {
  if (Number(item.mint_resale_mid || 0) <= 0) {
    return "";
  }
  const resale = `${currency(item.mint_resale_low)}-${currency(item.mint_resale_high)}`;
  const profit = `${currency(item.mint_profit_low)}-${currency(item.mint_profit_high)}`;
  return `Mint resale ${resale}; mint profit ${profit}`;
}

function resaleMarketSourceLabel(source) {
  if (source === "resale_research") return "resale_research";
  if (source === "repair_values") return "repair_values";
  if (source === "legacy_resale_value") return "legacy resale value";
  return formatFlag(source || "missing");
}

function itemSpecifics(item) {
  const rawJson = item.raw_json || {};
  const details = rawJson.details || {};
  const summary = rawJson.summary || {};
  const values = [];
  for (const source of [details.localizedAspects, details.aspects, details.itemSpecifics, summary.localizedAspects, summary.aspects]) {
    values.push(...specificsFromSource(source));
  }
  return values.length ? values.slice(0, 24) : ["No item specifics available from API."];
}

function specificsFromSource(source) {
  if (!source) {
    return [];
  }
  if (Array.isArray(source)) {
    return source.map((entry) => {
      if (entry && typeof entry === "object") {
        const name = entry.name || entry.localizedName || entry.key;
        const value = entry.value || entry.localizedValue || entry.values;
        return name ? `${name}: ${Array.isArray(value) ? value.join(", ") : value || ""}` : JSON.stringify(entry);
      }
      return String(entry);
    });
  }
  if (typeof source === "object") {
    return Object.entries(source).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`);
  }
  return [String(source)];
}

function cleanDescription(value) {
  if (!value) {
    return "";
  }
  let text = String(value)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<br\s*\/?>|<\/p>|<\/div>|<\/li>|<\/tr>/gi, "\n")
    .replace(/<[^>]+>/g, " ");
  text = decodeEntities(text);
  text = text
    .replace(/\u00a0/g, " ")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t\f\v]+/g, " ")
    .replace(/\n\s*\n\s*\n+/g, "\n\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !isBoilerplateDescriptionLine(line))
    .join("\n")
    .trim();
  return text;
}

function decodeEntities(value) {
  const entities = {
    amp: "&",
    lt: "<",
    gt: ">",
    quot: "\"",
    apos: "'",
    nbsp: " ",
  };
  return value.replace(/&(#(\d+)|#x([0-9a-f]+)|[a-z]+);/gi, (match, token, decimal, hex) => {
    if (decimal) {
      return String.fromCharCode(Number(decimal));
    }
    if (hex) {
      return String.fromCharCode(parseInt(hex, 16));
    }
    return entities[token.toLowerCase()] || match;
  });
}

function isBoilerplateDescriptionLine(line) {
  const lowered = line.toLowerCase();
  return [
    "powered by",
    "supreme widgets",
    "ebay template",
    "thanks for looking",
    "please see my other items",
  ].some((phrase) => lowered.includes(phrase));
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
