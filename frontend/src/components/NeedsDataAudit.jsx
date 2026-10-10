import { useEffect, useRef, useState } from "react";
import { getDashboardItems, getNeedsDataDiagnostics } from "../api.js";
import { GAP_DESCRIPTIONS, summarizeNeedsData } from "../needsDataAudit.js";

const PAGE_SIZE = 50;

export default function NeedsDataAudit() {
  const [report, setReport] = useState(null);
  const [deepReport, setDeepReport] = useState(null);
  const [deepLoading, setDeepLoading] = useState(false);
  const [deepError, setDeepError] = useState("");
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const activeRef = useRef(0);
  useEffect(() => () => { activeRef.current++; }, []);

  async function runAudit(cap) {
    const run = ++activeRef.current;
    setReport(null);
    setError("");
    setLoading(true);
    setProgress("Loading first page of the Needs Data queue…");
    try {
      const rows = [];
      let total = Infinity;
      while (rows.length < cap && rows.length < total) {
        const page = await getDashboardItems({
          queue: "needs_data", sort: "newest", search: "", includeIgnored: false,
          includeStale: false, limit: Math.min(PAGE_SIZE, cap - rows.length), offset: rows.length,
        });
        if (activeRef.current !== run) return;
        const next = Array.isArray(page.items) ? page.items : [];
        const parsed = Number(page.total);
        if (Number.isFinite(parsed) && parsed >= 0) total = parsed;
        rows.push(...next);
        setProgress(`Examined ${rows.length} of ${Number.isFinite(total) ? total : "?"} items…`);
        if (!next.length || page.has_more === false) break;
      }
      if (activeRef.current !== run) return;
      const summary = summarizeNeedsData(rows, Number.isFinite(total) ? total : rows.length, cap);
      setReport(summary);
      setProgress("");
    } catch (err) {
      if (activeRef.current === run) setError(err.message || "Unable to load the queue.");
    } finally {
      if (activeRef.current === run) setLoading(false);
    }
  }

  async function runDeepAudit() {
    const run = ++activeRef.current;
    setDeepLoading(true);
    setDeepError("");
    setDeepReport(null);
    try {
      const result = await getNeedsDataDiagnostics(500);
      if (activeRef.current === run) setDeepReport(result);
    } catch (err) {
      if (activeRef.current === run) setDeepError(err.message || "Unable to run detailed diagnostics. Deploy the updated API first.");
    } finally {
      if (activeRef.current === run) setDeepLoading(false);
    }
  }

  function downloadDeepReport() {
    if (!deepReport) return;
    const blob = new Blob([JSON.stringify(deepReport, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `notifierr-needs-data-detailed-${deepReport.computed_at.slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  function downloadReport() {
    if (!report) return;
    const blob = new Blob([JSON.stringify({ scope: "User-scoped, current Needs Data dashboard queue; no mutations", report }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `notifierr-needs-data-audit-${report.recordedAt.slice(0,10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return <section className="settings-section needs-audit-panel" aria-label="Needs Data investigation">
    <div>
      <h3>Where are the incomplete listings going?</h3>
      <p className="needs-audit-intro">This read-only check examines the current user-scoped Needs Data queue. It separates missing fields from low-profit thresholds and uncertain evidence. Counts may overlap. It does not rescore, reject, alert, or purchase phones.</p>
    </div>
    <div className="needs-audit-actions">
      <button type="button" disabled={loading} onClick={() => runAudit(100)}>Quick audit · up to 100</button>
      <button type="button" className="primary-button" disabled={loading} onClick={() => runAudit(500)}>Full audit · up to 500</button>
      {report ? <button type="button" onClick={downloadReport}>Export findings for Codex</button> : null}
      <button type="button" disabled={loading || deepLoading} onClick={runDeepAudit}>{deepLoading ? "Inspecting decisions…" : "Analyze every decision gate"}</button>
      {deepReport ? <button type="button" className="primary-button" onClick={downloadDeepReport}>Export detailed records ({deepReport.records_returned})</button> : null}
    </div>
    {loading ? <p role="status" className="needs-audit-status">{progress} The scan of this queue can take time; normal dashboard loading is unaffected.</p> : null}
    {error ? <p role="alert" className="detail-load-error">{error}</p> : null}
    {deepError ? <p role="alert" className="detail-load-error">{deepError}</p> : null}
    {deepReport ? <div className="needs-audit-detailed">
      <h3>First blocking stage · complete decision records</h3>
      <p className="needs-audit-status">{deepReport.records_returned} / {deepReport.queue_total_within_source} current Needs Data records exported. {deepReport.complete ? "Full visible queue captured." : "Partial sample: export is capped; do not generalize to all listings."} Counts represent stored decisions, not verified purchase outcomes. No listing status was changed.</p>
      <div className="needs-audit-reasons">
        {Object.entries(deepReport.primary_gate_counts || {}).sort((a, b) => b[1] - a[1]).map(([stage, count]) => <div className="needs-audit-reason" key={stage}>
          <div><strong>{stage.replaceAll("_", " ")}</strong><span className="needs-audit-bar"><i style={{ width: `${100 * count / Math.max(1, deepReport.queue_total_within_source)}%` }} /></span></div>
          <span>{count}</span>
        </div>)}
      </div>
      {Object.keys(deepReport.cross_queue_consistency?.counts || {}).length > 0 ? <div>
        <h3>Potential tier or pricing conflicts</h3>
        <p className="needs-audit-status">These flags are diagnostic only. A scoring threshold and a notification threshold can differ legitimately.</p>
        <div className="needs-audit-reasons">{Object.entries(deepReport.cross_queue_consistency.counts).map(([code, count]) => <div className="needs-audit-reason" key={code}><div><strong>{code.replaceAll("_", " ")}</strong></div><span>{count}</span></div>)}</div>
      </div> : null}
      <p className="needs-audit-status">Export detailed records for a listing-by-listing investigation. Unlike the original summary, it includes every inspected listing's decision gates, flags, and pricing availability, but excludes full seller descriptions.</p>
    </div> : null}
    {report ? <>
      <div className="needs-audit-grid">
        <div className="needs-audit-metric"><span>Examined</span><strong>{report.evaluated}</strong></div>
        <div className="needs-audit-metric"><span>Queue reported</span><strong>{report.reportedTotal}</strong></div>
        <div className="needs-audit-metric"><span>Positive estimated profit</span><strong>{report.positiveEstimate}</strong></div>
      </div>
      <p className="needs-audit-status">{report.complete ? "Full reported queue reviewed." : `Partial snapshot (${report.evaluated}/${report.reportedTotal}); don't generalize sample rates to all listings.`} Estimated profit does not establish realized earnings. Reason groups overlap; they are diagnostic observations, not proven root causes.</p>
      <div>
        <h3>Observed gaps and decision patterns</h3>
        <div className="needs-audit-reasons">
          {Object.entries(report.reasons).sort((a,b) => b[1]-a[1]).filter(([,count]) => count>0).map(([key,count]) => {
            const [label,description] = GAP_DESCRIPTIONS[key];
            return <div className="needs-audit-reason" key={key}>
              <div><strong>{label}</strong><small>{description}</small><span className="needs-audit-bar"><i style={{width: `${(count/Math.max(report.evaluated,1))*100}%`}}/></span></div>
              <span>{count}</span>
            </div>;
          })}
        </div>
      </div>
      {report.examples.length ? <div>
        <h3>Examples for human or Codex review</h3>
        <div className="needs-audit-examples">
          {report.examples.map((item,i) => <div className="needs-audit-example" key={`${item.item_id}-${i}`}><strong>{item.title}</strong><span>{item.item_id} · {item.tags.map(tag => GAP_DESCRIPTIONS[tag][0]).join(" · ")}{item.profit == null ? "" : ` · estimated net $${Number(item.profit).toFixed(2)}`}</span><span>{item.manual_review_reason || "No explicit manual reason available"}</span></div>)}
        </div>
      </div> : null}
    </> : null}
  </section>;
}
