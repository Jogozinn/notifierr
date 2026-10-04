import { useEffect, useMemo, useState } from "react";
import {
  createAdminInvite,
  createAdminUser,
  disableAdminUser,
  enableAdminUser,
  getAdminInvites,
  getAdminFreshScanExport,
  getAdminScanCycles,
  getAdminScanStats,
  getAdminSourcesStatus,
  getAdminUserUsage,
  getAdminUsers,
  getAdminWorkerStatus,
  getTraceExport,
  getPushDelivery,
  revokeAdminInvite,
  runTraceReplay,
  updateAdminUser,
} from "../api.js";

const EMPTY_CREATE_DRAFT = {
  email: "",
  password: "",
  display_name: "",
  role: "user",
  account_status: "active",
  plan_name: "",
  monthly_price: "",
  billing_status: "trial",
  paid_until: "",
  billing_note: "",
};

const EMPTY_INVITE_DRAFT = {
  email: "",
  role: "user",
  expires_at: "",
};

const EMPTY_REPLAY_DRAFT = {
  source_cycle_id: "",
  item_ids: "",
  limit: 25,
  active_only: false,
  non_stale_only: false,
  not_ignored_only: false,
  rescore_from_raw: true,
  dry_run: true,
  write_traces: false,
};

const AUDIT_ITEM_IDS = [
  "v1|327253086710|0",
  "v1|257612607490|0",
  "v1|267721455090|0",
  "v1|398154164118|0",
  "v1|327253131790|0",
];

const DEV_FALLBACK_FRESH_CYCLE_ID = "10";

export default function AdminPanel({ onClose, onUnauthorized, onError, onNotice }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [users, setUsers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [scanStats, setScanStats] = useState(null);
  const [scanCycles, setScanCycles] = useState([]);
  const [workerStatus, setWorkerStatus] = useState(null);
  const [sourcesStatus, setSourcesStatus] = useState(null);
  const [freshScanExport, setFreshScanExport] = useState(null);
  const [pushDelivery, setPushDelivery] = useState(null);
  const [adminAuthMessage, setAdminAuthMessage] = useState("");
  const [selectedUserId, setSelectedUserId] = useState(null);
  const [selectedUsage, setSelectedUsage] = useState(null);
  const [createDraft, setCreateDraft] = useState(EMPTY_CREATE_DRAFT);
  const [inviteDraft, setInviteDraft] = useState(EMPTY_INVITE_DRAFT);
  const [editDraft, setEditDraft] = useState(null);
  const [replayDraft, setReplayDraft] = useState(EMPTY_REPLAY_DRAFT);
  const [replayRunning, setReplayRunning] = useState(false);
  const [replayResult, setReplayResult] = useState(null);
  const [replayExport, setReplayExport] = useState(null);
  const [replayMessage, setReplayMessage] = useState("");
  const [replayError, setReplayError] = useState("");
  const [showReplayJson, setShowReplayJson] = useState(false);
  const [showReplayPayload, setShowReplayPayload] = useState(false);
  const [copyPayloadStatus, setCopyPayloadStatus] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const [usersResult, invitesResult, scanStatsResult, scanCyclesResult, workerStatusResult, sourcesStatusResult, freshScanExportResult, pushDeliveryResult] = await Promise.all([
          getAdminUsers(),
          getAdminInvites(),
          getAdminScanStats(),
          getAdminScanCycles(),
          getAdminWorkerStatus(),
          getAdminSourcesStatus(),
          getAdminFreshScanExport(),
          getPushDelivery(),
        ]);
        if (cancelled) {
          return;
        }
        const nextUsers = usersResult.users || [];
        setAdminAuthMessage("");
        setInvites(invitesResult.invites || []);
        setUsers(nextUsers);
        setScanStats(scanStatsResult);
        setScanCycles(scanCyclesResult.cycles || []);
        setWorkerStatus(workerStatusResult);
        setSourcesStatus(sourcesStatusResult);
        setFreshScanExport(freshScanExportResult);
        setPushDelivery(pushDeliveryResult);
        if (nextUsers.length) {
          const nextSelectedUserId = nextUsers.some((entry) => entry.id === selectedUserId)
            ? selectedUserId
            : nextUsers[0].id;
          setSelectedUserId(nextSelectedUserId);
        } else {
          setSelectedUserId(null);
        }
      } catch (err) {
        if (err.status === 401 || err.status === 403) {
          handleAdminAuthFailure(err);
          return;
        }
        onError(err.message);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [onError, onUnauthorized, selectedUserId]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedUserId) {
      setSelectedUsage(null);
      setEditDraft(null);
      return;
    }

    const selectedUser = users.find((entry) => entry.id === selectedUserId);
    if (selectedUser) {
      setEditDraft({
        display_name: selectedUser.display_name || "",
        role: selectedUser.role || "user",
        account_status: selectedUser.account_status || "active",
        plan_name: selectedUser.plan_name || "",
        monthly_price: selectedUser.monthly_price ?? 0,
        billing_status: selectedUser.billing_status || "trial",
        paid_until: selectedUser.paid_until || "",
        billing_note: selectedUser.billing_note || "",
      });
    }

    async function loadUsage() {
      try {
        const usage = await getAdminUserUsage(selectedUserId);
        if (!cancelled) {
          setSelectedUsage(usage);
        }
      } catch (err) {
        if (err.status === 401 || err.status === 403) {
          handleAdminAuthFailure(err);
          return;
        }
        if (!cancelled) {
          onError(err.message);
        }
      }
    }

    loadUsage();
    return () => {
      cancelled = true;
    };
  }, [onError, onUnauthorized, selectedUserId, users]);

  const selectedUser = useMemo(
    () => users.find((entry) => entry.id === selectedUserId) || null,
    [selectedUserId, users],
  );
  const latestCycle = scanCycles[0] || null;
  const backgroundWorker = workerStatus?.workers?.[0] || null;
  const pollingStatus = workerStatus?.polling_status || {};
  const ebaySource = sourcesStatus?.sources?.find((entry) => entry.source === "ebay") || null;
  const freshCycle = freshScanExport?.latest_successful_fresh_scan_cycle || null;
  const failedOrSkippedCycle = freshScanExport?.latest_failed_or_skipped_scan_cycle
    || scanCycles.find((entry) => ["failed", "skipped"].includes(entry.status))
    || null;
  const latestFreshCycleId = freshCycle?.id || freshCycle?.cycle_id || "";
  const replayPresetCycleLabel = latestFreshCycleId
    ? `Latest fresh cycle ${latestFreshCycleId}`
    : `Fallback dev cycle ${DEV_FALLBACK_FRESH_CYCLE_ID}`;
  const replayPreviewRows = buildReplayPreviewRows(replayExport, replayResult);

  async function refreshUsers({ notice = "" } = {}) {
    const [usersResult, invitesResult, scanStatsResult, scanCyclesResult, workerStatusResult, sourcesStatusResult, freshScanExportResult, pushDeliveryResult] = await Promise.all([
      getAdminUsers(),
      getAdminInvites(),
      getAdminScanStats(),
      getAdminScanCycles(),
      getAdminWorkerStatus(),
      getAdminSourcesStatus(),
      getAdminFreshScanExport(),
      getPushDelivery(),
    ]);
    const nextUsers = usersResult.users || [];
    setUsers(nextUsers);
    setInvites(invitesResult.invites || []);
    setScanStats(scanStatsResult);
    setScanCycles(scanCyclesResult.cycles || []);
    setWorkerStatus(workerStatusResult);
    setSourcesStatus(sourcesStatusResult);
    setFreshScanExport(freshScanExportResult);
    setPushDelivery(pushDeliveryResult);
    setAdminAuthMessage("");
    if (notice) {
      onNotice(notice);
    }
  }

  async function handleCreateUser(event) {
    event.preventDefault();
    setSaving(true);
    try {
      const payload = {
        ...createDraft,
        monthly_price: Number(createDraft.monthly_price || 0),
        paid_until: createDraft.paid_until || null,
      };
      const result = await createAdminUser(payload);
      await refreshUsers({ notice: "User created." });
      setCreateDraft(EMPTY_CREATE_DRAFT);
      setSelectedUserId(result.user.id);
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveUser(event) {
    event.preventDefault();
    if (!selectedUser || !editDraft) {
      return;
    }
    setSaving(true);
    try {
      await updateAdminUser(selectedUser.id, {
        ...editDraft,
        monthly_price: Number(editDraft.monthly_price || 0),
        paid_until: editDraft.paid_until || null,
      });
      await refreshUsers({ notice: "User updated." });
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateInvite(event) {
    event.preventDefault();
    setSaving(true);
    try {
      await createAdminInvite({
        email: inviteDraft.email || null,
        role: inviteDraft.role,
        expires_at: inviteDraft.expires_at ? new Date(`${inviteDraft.expires_at}T00:00:00`).toISOString() : null,
      });
      await refreshUsers({ notice: "Invite created." });
      setInviteDraft(EMPTY_INVITE_DRAFT);
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleRevokeInvite(inviteId) {
    setSaving(true);
    try {
      await revokeAdminInvite(inviteId);
      await refreshUsers({ notice: "Invite revoked." });
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleToggleUser(enabled) {
    if (!selectedUser) {
      return;
    }
    setSaving(true);
    try {
      if (enabled) {
        await enableAdminUser(selectedUser.id);
        await refreshUsers({ notice: "User enabled." });
      } else {
        await disableAdminUser(selectedUser.id);
        await refreshUsers({ notice: "User disabled." });
      }
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
    }
  }

  function handleAdminAuthFailure(err) {
    const message = err.status === 401
      ? "Admin endpoints require an admin sign-in. For local authenticated debugging, configure AUTH_REQUIRED, AUTH_SECRET_KEY, ADMIN_EMAIL, and ADMIN_PASSWORD, then sign in as that admin."
      : "Admin endpoints require an active admin account.";
    setAdminAuthMessage(message);
    onError(message);
    onUnauthorized();
  }

  function updateReplayDraft(patch) {
    setReplayDraft((current) => {
      const next = { ...current, ...patch };
      if (patch.dry_run === true) {
        next.write_traces = false;
      }
      return next;
    });
  }

  function applyLatestFreshReplayPreset() {
    const cycleId = latestFreshCycleId || DEV_FALLBACK_FRESH_CYCLE_ID;
    setReplayDraft({
      source_cycle_id: String(cycleId),
      item_ids: AUDIT_ITEM_IDS.join("\n"),
      limit: 25,
      active_only: false,
      non_stale_only: false,
      not_ignored_only: false,
      rescore_from_raw: true,
      dry_run: true,
      write_traces: false,
    });
    setReplayMessage(latestFreshCycleId
      ? `Preset filled for latest fresh cycle ${cycleId}. Replay has not been run.`
      : `Preset filled with fallback dev cycle ${cycleId}. Replay has not been run.`);
    setReplayError("");
  }

  function applyKnownAuditItemsPreset() {
    setReplayDraft({
      source_cycle_id: "",
      item_ids: AUDIT_ITEM_IDS.join("\n"),
      limit: 10,
      active_only: false,
      non_stale_only: false,
      not_ignored_only: false,
      rescore_from_raw: true,
      dry_run: true,
      write_traces: false,
    });
    setReplayMessage("Preset filled for known audit items only. Replay has not been run.");
    setReplayError("");
  }

  function replayPayload() {
    const itemIds = replayDraft.item_ids
      .split(/[\n,]+/)
      .map((entry) => entry.trim())
      .filter(Boolean);
    const payload = {
      user_id: selectedUserId || undefined,
      limit: Math.max(1, Math.min(500, Number(replayDraft.limit || 25))),
      active_only: Boolean(replayDraft.active_only),
      non_stale_only: Boolean(replayDraft.non_stale_only),
      not_ignored_only: Boolean(replayDraft.not_ignored_only),
      rescore_from_raw: Boolean(replayDraft.rescore_from_raw),
      dry_run: Boolean(replayDraft.dry_run),
      write_traces: replayDraft.dry_run ? false : Boolean(replayDraft.write_traces),
    };
    if (replayDraft.source_cycle_id) {
      payload.source_cycle_id = Number(replayDraft.source_cycle_id);
    }
    if (itemIds.length) {
      payload.item_ids = itemIds;
    }
    return payload;
  }

  async function handleCopyReplayPayload() {
    const payloadText = JSON.stringify(replayPayload(), null, 2);
    setCopyPayloadStatus("");
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(payloadText);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = payloadText;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (!copied) {
          throw new Error("Clipboard copy failed");
        }
      }
      setCopyPayloadStatus("Copied");
    } catch {
      setCopyPayloadStatus("Copy failed");
    }
  }

  async function handleRunReplay(event) {
    event.preventDefault();
    setReplayRunning(true);
    setReplayError("");
    setReplayMessage("");
    setReplayResult(null);
    setReplayExport(null);
    try {
      const payload = replayPayload();
      const result = await runTraceReplay(payload);
      let exportResult = null;
      if (result.scan_cycle_id && payload.write_traces && !payload.dry_run) {
        exportResult = await getTraceExport(result.scan_cycle_id);
      }
      setReplayResult(result);
      setReplayExport(exportResult);
      setReplayMessage(payload.dry_run ? "Dry run complete. No trace rows were written." : "Replay complete.");
      if (!payload.dry_run && !payload.write_traces) {
        setReplayMessage("Replay complete. Trace rows were not written.");
      }
      onNotice(payload.dry_run ? "Trace replay dry run complete." : "Trace replay complete.");
    } catch (err) {
      if (err.status === 401 || err.status === 403) {
        handleAdminAuthFailure(err);
        return;
      }
      setReplayError(err.message);
      onError(err.message);
    } finally {
      setReplayRunning(false);
    }
  }

  return (
    <section className="settings-overlay" aria-label="Admin panel">
      <div className="settings-panel admin-panel">
        <div className="settings-header">
          <div>
            <h2>Admin</h2>
            <p>Users, manual billing, usage, and shared scan status.</p>
          </div>
          <button type="button" onClick={onClose}>Close</button>
        </div>

        {loading ? <div className="empty-state">Loading admin data...</div> : null}
        {adminAuthMessage ? <div className="empty-state">{adminAuthMessage}</div> : null}

        {!loading && scanStats ? (
          <section className="settings-section">
            <div className="settings-section-header">
              <h3>Scan status</h3>
              <p>{backgroundWorker?.status || latestCycle?.status || scanStats.worker_status || scanStats.status || "unknown"}</p>
            </div>
            <div className="admin-stat-grid">
              <AdminMetric label="Worker status" value={backgroundWorker?.status || "unknown"} />
              <AdminMetric label="Last heartbeat" value={backgroundWorker?.last_seen_at || "Never"} />
              <AdminMetric label="Config poll seconds" value={pollingStatus.background_poll_config_seconds ?? "n/a"} />
              <AdminMetric label="User poll seconds" value={pollingStatus.background_poll_user_seconds ?? "n/a"} />
              <AdminMetric label="Final poll seconds" value={pollingStatus.last_sleep_seconds ?? "n/a"} />
              <AdminMetric label="Poll interval source" value={pollingStatus.background_poll_interval_source || "n/a"} />
              <AdminMetric label="Last scan cycle" value={latestCycle?.cycle_id || latestCycle?.id || "Never"} />
              <AdminMetric label="Last scan status" value={latestCycle?.status || "unknown"} />
              <AdminMetric label="Last skip/failure" value={latestCycle?.skip_reason || latestCycle?.error_message || "None"} />
              <AdminMetric label="Last found/scored/alerted" value={latestCycle ? `${latestCycle.items_found ?? 0}/${latestCycle.items_scored ?? 0}/${latestCycle.alerts_sent ?? 0}` : "0/0/0"} />
              <AdminMetric label="eBay source" value={ebaySource?.status || "ok"} />
              <AdminMetric label="eBay cooldown" value={ebaySource?.cooldown_until || "None"} />
              <AdminMetric label="Last eBay error" value={ebaySource?.last_error_message_preview || "None"} />
              <AdminMetric label="Latest fresh cycle" value={freshCycle?.cycle_id || freshCycle?.id || "None"} />
              <AdminMetric label="Latest failed/skipped" value={failedOrSkippedCycle ? `${failedOrSkippedCycle.id} · ${failedOrSkippedCycle.status}` : "None"} />
              <AdminMetric label="Last shared scan" value={scanStats.last_shared_scan_time || "Never"} />
              <AdminMetric label="Active users" value={scanStats.active_users ?? 0} />
              <AdminMetric label="Unique searches" value={scanStats.unique_searches ?? 0} />
              <AdminMetric label="Shared API calls" value={scanStats.shared_api_calls ?? 0} />
              <AdminMetric label="Items ingested" value={scanStats.total_items_ingested ?? 0} />
              <AdminMetric label="Alerts sent" value={scanStats.alerts_sent ?? 0} />
              <AdminMetric label="Push devices" value={pushDelivery?.metrics?.active_subscriptions ?? 0} />
              <AdminMetric label="Push accepted" value={pushDelivery?.metrics?.accepted ?? 0} />
              <AdminMetric label="Push failed/invalid" value={`${pushDelivery?.metrics?.failed ?? 0}/${pushDelivery?.metrics?.invalid ?? 0}`} />
            </div>
            <div className="live-summary" aria-label="Needs Data reason counts">
              <span>Needs Data reasons: <strong>{formatReasonCounts(latestCycle?.missing_data_reason_counts)}</strong></span>
              {freshCycle ? (
                <a href={`/admin/scan/cycles/${encodeURIComponent(freshCycle.id)}/trace-export`} target="_blank" rel="noreferrer">Fresh trace export</a>
              ) : null}
            </div>
          </section>
        ) : null}

        {!loading ? (
          <section className="settings-section admin-card">
            <div className="settings-section-header">
              <div>
                <h3>Trace Replay / Rescore</h3>
                <p>Replay uses stored listing data only. It does not call eBay, send alerts, mark alerted, or mutate item status.</p>
              </div>
              <div className="admin-user-meta">
                <span>{selectedUser ? `User ${selectedUser.id}` : "No user"}</span>
                <span>{replayDraft.dry_run ? "dry run" : "write mode"}</span>
              </div>
            </div>

            <div className="replay-preset-row" aria-label="Trace replay presets">
              <button type="button" onClick={applyLatestFreshReplayPreset}>
                Preset: Latest fresh cycle raw rescore
              </button>
              <button type="button" onClick={applyKnownAuditItemsPreset}>
                Preset: Known audit items only
              </button>
              <button type="button" onClick={handleCopyReplayPayload}>
                Copy replay payload
              </button>
              <button type="button" onClick={() => setShowReplayPayload((current) => !current)}>
                {showReplayPayload ? "Hide payload" : "Payload preview"}
              </button>
              <span>{replayPresetCycleLabel}</span>
              {copyPayloadStatus ? <span>{copyPayloadStatus}</span> : null}
            </div>
            <div className="replay-safety-text">
              <span>Presets only fill the form.</span>
              <span>They do not run replay automatically.</span>
              <span>Dry run does not write trace rows.</span>
              <span>Raw rescore does not call eBay or send alerts.</span>
            </div>
            {showReplayPayload ? (
              <pre className="raw-json-block replay-payload-preview">
                {JSON.stringify(replayPayload(), null, 2)}
              </pre>
            ) : null}

            <form className="settings-form" onSubmit={handleRunReplay}>
              <div className="settings-grid admin-settings-grid replay-settings-grid">
                <label>
                  <span>Source cycle id</span>
                  <input
                    type="number"
                    min="1"
                    value={replayDraft.source_cycle_id}
                    onChange={(event) => updateReplayDraft({ source_cycle_id: event.target.value })}
                    placeholder={freshCycle?.id ? String(freshCycle.id) : "Optional"}
                  />
                </label>
                <label>
                  <span>Limit</span>
                  <input
                    type="number"
                    min="1"
                    max="500"
                    value={replayDraft.limit}
                    onChange={(event) => updateReplayDraft({ limit: event.target.value })}
                  />
                </label>
                <label className="admin-wide-field">
                  <span>Item ids</span>
                  <textarea
                    rows="3"
                    value={replayDraft.item_ids}
                    onChange={(event) => updateReplayDraft({ item_ids: event.target.value })}
                    placeholder="One per line or comma-separated"
                  />
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.active_only}
                    onChange={(event) => updateReplayDraft({ active_only: event.target.checked })}
                  />
                  <span>Active only</span>
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.non_stale_only}
                    onChange={(event) => updateReplayDraft({ non_stale_only: event.target.checked })}
                  />
                  <span>Non-stale only</span>
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.not_ignored_only}
                    onChange={(event) => updateReplayDraft({ not_ignored_only: event.target.checked })}
                  />
                  <span>Not ignored only</span>
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.rescore_from_raw}
                    onChange={(event) => updateReplayDraft({ rescore_from_raw: event.target.checked })}
                  />
                  <span>Rescore from raw</span>
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.dry_run}
                    onChange={(event) => updateReplayDraft({ dry_run: event.target.checked })}
                  />
                  <span>Dry run</span>
                </label>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={replayDraft.write_traces && !replayDraft.dry_run}
                    disabled={replayDraft.dry_run}
                    onChange={(event) => updateReplayDraft({ write_traces: event.target.checked })}
                  />
                  <span>Write traces</span>
                </label>
              </div>
              <div className="settings-actions-row replay-actions">
                <button className="primary-button" type="submit" disabled={replayRunning || !selectedUserId}>
                  {replayRunning ? "Running..." : replayDraft.dry_run ? "Dry Run Rescore" : "Run Replay"}
                </button>
                {replayResult?.scan_cycle_id ? (
                  <a
                    href={`/admin/scan/cycles/${encodeURIComponent(replayResult.scan_cycle_id)}/trace-export`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Trace export
                  </a>
                ) : null}
                <button type="button" onClick={() => setShowReplayJson((current) => !current)}>
                  {showReplayJson ? "Hide JSON" : "Raw JSON"}
                </button>
              </div>
            </form>

            <div className="replay-safety-text">
              <span>Rescore from raw reruns the current deterministic scorer on stored marketplace data.</span>
              <span>Dry run does not write trace rows.</span>
              <span>Write traces creates replay traces for export review.</span>
            </div>

            {replayMessage ? <div className="empty-state replay-status">{replayMessage}</div> : null}
            {replayError ? <div className="empty-state replay-status">{replayError}</div> : null}

            {replayResult ? (
              <>
                <div className="admin-stat-grid replay-stat-grid">
                  <AdminMetric label="Replay cycle" value={replayResult.scan_cycle_id || "None"} />
                  <AdminMetric label="Dry run" value={String(Boolean(replayResult.dry_run))} />
                  <AdminMetric label="Traces written" value={replayResult.traces_written ?? 0} />
                  <AdminMetric label="Items rescored" value={replayExport?.total_rescored_items ?? replayResult.replayed ?? 0} />
                  <AdminMetric label="Changed status" value={replayExport?.changed_status_count ?? "n/a"} />
                  <AdminMetric label="Changed alerts" value={replayExport?.changed_alert_eligibility_count ?? "n/a"} />
                  <AdminMetric label="Accessory/display rejects" value={replayExport?.display_screen_assembly_reject_count ?? "n/a"} />
                  <AdminMetric label="High-resale gated" value={replayExport?.high_resale_new_model_gated_count ?? "n/a"} />
                  <AdminMetric label="Storage review routed" value={replayExport?.storage_unknown_review_routed_count ?? "n/a"} />
                  <AdminMetric label="Carrier unknown" value={replayExport?.carrier_unknown_warning_count ?? "n/a"} />
                  <AdminMetric label="Alert eligible" value={replayExport?.alert_eligible_count ?? "n/a"} />
                  <AdminMetric label="Alert blocked" value={replayExport?.alert_blocked_count ?? "n/a"} />
                </div>
                {replayResult.dry_run || replayResult.traces_written === 0 ? (
                  <div className="empty-state replay-status">No trace rows were written for this run.</div>
                ) : null}
              </>
            ) : null}

            {replayPreviewRows.length ? (
              <div className="replay-preview">
                <table>
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th>Title</th>
                      <th>Persisted</th>
                      <th>Rescored</th>
                      <th>Score</th>
                      <th>Alert</th>
                      <th>Bucket</th>
                      <th>Changed</th>
                      <th>Top reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {replayPreviewRows.map((row) => (
                      <tr key={row.item_id}>
                        <td className="mono-cell">{row.item_id}</td>
                        <td>{row.title}</td>
                        <td>{row.persisted_status || "n/a"}</td>
                        <td>{row.rescored_status || "n/a"}</td>
                        <td>{formatScorePair(row.persisted_score, row.rescored_score)}</td>
                        <td>{formatBooleanPair(row.persisted_alert_eligible, row.rescored_alert_eligible)}</td>
                        <td>{row.rescored_normalized_bucket || row.normalized_bucket || "n/a"}</td>
                        <td>{row.changed_status || row.changed_alert_eligibility ? "yes" : "no"}</td>
                        <td>{topReplayReason(row)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}

            {showReplayJson && (replayResult || replayExport) ? (
              <pre className="raw-json-block">
                {JSON.stringify({ replay: replayResult, export: replayExport }, null, 2)}
              </pre>
            ) : null}
          </section>
        ) : null}

        {!loading ? (
          <section className="settings-section admin-layout">
            <div className="admin-users-column">
              <div className="settings-section-header">
                <h3>Users</h3>
                <p>{users.length} total</p>
              </div>
              <div className="admin-user-list">
                {users.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    className={entry.id === selectedUserId ? "admin-user-row admin-user-row-active" : "admin-user-row"}
                    onClick={() => setSelectedUserId(entry.id)}
                  >
                    <div>
                      <strong>{entry.display_name || entry.email}</strong>
                      <p>{entry.email}</p>
                    </div>
                    <div className="admin-user-meta">
                      <span>{entry.role}</span>
                      <span>{entry.account_status}</span>
                      <span>{entry.billing_status || "unbilled"}</span>
                    </div>
                  </button>
                ))}
              </div>
            </div>

            <div className="admin-detail-column">
              <section className="settings-section admin-card">
                <div className="settings-section-header">
                  <h3>Create user</h3>
                  <p>Invite-only manual account creation.</p>
                </div>
                <form className="settings-form" onSubmit={handleCreateUser}>
                  <div className="settings-grid admin-settings-grid">
                    <label>
                      <span>Email</span>
                      <input value={createDraft.email} onChange={(event) => setCreateDraft((current) => ({ ...current, email: event.target.value }))} />
                    </label>
                    <label>
                      <span>Temporary password</span>
                      <input value={createDraft.password} onChange={(event) => setCreateDraft((current) => ({ ...current, password: event.target.value }))} />
                    </label>
                    <label>
                      <span>Display name</span>
                      <input value={createDraft.display_name} onChange={(event) => setCreateDraft((current) => ({ ...current, display_name: event.target.value }))} />
                    </label>
                    <label>
                      <span>Role</span>
                      <select value={createDraft.role} onChange={(event) => setCreateDraft((current) => ({ ...current, role: event.target.value }))}>
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                      </select>
                    </label>
                    <label>
                      <span>Plan</span>
                      <input value={createDraft.plan_name} onChange={(event) => setCreateDraft((current) => ({ ...current, plan_name: event.target.value }))} />
                    </label>
                    <label>
                      <span>Monthly price</span>
                      <input type="number" min="0" step="0.01" value={createDraft.monthly_price} onChange={(event) => setCreateDraft((current) => ({ ...current, monthly_price: event.target.value }))} />
                    </label>
                  </div>
                  <button className="primary-button" type="submit" disabled={saving}>
                    {saving ? "Saving..." : "Create user"}
                  </button>
                </form>
              </section>

              <section className="settings-section admin-card">
                <div className="settings-section-header">
                  <h3>Invites</h3>
                  <p>Generate one-time private registration codes.</p>
                </div>
                <form className="settings-form" onSubmit={handleCreateInvite}>
                  <div className="settings-grid admin-settings-grid">
                    <label>
                      <span>Email lock</span>
                      <input value={inviteDraft.email} onChange={(event) => setInviteDraft((current) => ({ ...current, email: event.target.value }))} placeholder="Optional" />
                    </label>
                    <label>
                      <span>Role</span>
                      <select value={inviteDraft.role} onChange={(event) => setInviteDraft((current) => ({ ...current, role: event.target.value }))}>
                        <option value="user">user</option>
                        <option value="admin">admin</option>
                      </select>
                    </label>
                    <label>
                      <span>Expires on</span>
                      <input type="date" value={inviteDraft.expires_at} onChange={(event) => setInviteDraft((current) => ({ ...current, expires_at: event.target.value }))} />
                    </label>
                  </div>
                  <button className="primary-button" type="submit" disabled={saving}>
                    {saving ? "Saving..." : "Create invite"}
                  </button>
                </form>
                <div className="admin-invite-list">
                  {invites.length ? invites.map((invite) => (
                    <div key={invite.id} className="admin-invite-row">
                      <div>
                        <strong>{invite.email || "Open invite"}</strong>
                        <div className="admin-invite-code">{invite.code}</div>
                        <div className="admin-invite-meta">
                          <span>{invite.role}</span>
                          <span>{invite.is_used ? "used" : "unused"}</span>
                          <span>{invite.is_revoked ? "revoked" : "active"}</span>
                          {invite.is_expired ? <span>expired</span> : null}
                        </div>
                      </div>
                      {!invite.is_used && !invite.is_revoked ? (
                        <button type="button" onClick={() => handleRevokeInvite(invite.id)} disabled={saving}>Revoke</button>
                      ) : null}
                    </div>
                  )) : <div className="empty-state">No invites yet.</div>}
                </div>
              </section>

              {selectedUser && editDraft ? (
                <section className="settings-section admin-card">
                  <div className="settings-section-header">
                    <div>
                      <h3>{selectedUser.display_name || selectedUser.email}</h3>
                      <p>{selectedUser.email}</p>
                    </div>
                    <div className="admin-user-meta">
                      <span>{selectedUser.role}</span>
                      <span>{selectedUser.account_status}</span>
                    </div>
                  </div>
                  <form className="settings-form" onSubmit={handleSaveUser}>
                    <div className="settings-grid admin-settings-grid">
                      <label>
                        <span>Display name</span>
                        <input value={editDraft.display_name} onChange={(event) => setEditDraft((current) => ({ ...current, display_name: event.target.value }))} />
                      </label>
                      <label>
                        <span>Role</span>
                        <select value={editDraft.role} onChange={(event) => setEditDraft((current) => ({ ...current, role: event.target.value }))}>
                          <option value="user">user</option>
                          <option value="admin">admin</option>
                        </select>
                      </label>
                      <label>
                        <span>Account status</span>
                        <select value={editDraft.account_status} onChange={(event) => setEditDraft((current) => ({ ...current, account_status: event.target.value }))}>
                          <option value="active">active</option>
                          <option value="disabled">disabled</option>
                          <option value="invited">invited</option>
                        </select>
                      </label>
                      <label>
                        <span>Plan name</span>
                        <input value={editDraft.plan_name} onChange={(event) => setEditDraft((current) => ({ ...current, plan_name: event.target.value }))} />
                      </label>
                      <label>
                        <span>Monthly price</span>
                        <input type="number" min="0" step="0.01" value={editDraft.monthly_price} onChange={(event) => setEditDraft((current) => ({ ...current, monthly_price: event.target.value }))} />
                      </label>
                      <label>
                        <span>Billing status</span>
                        <select value={editDraft.billing_status} onChange={(event) => setEditDraft((current) => ({ ...current, billing_status: event.target.value }))}>
                          <option value="trial">trial</option>
                          <option value="active">active</option>
                          <option value="past_due">past_due</option>
                          <option value="manual">manual</option>
                          <option value="comped">comped</option>
                        </select>
                      </label>
                      <label>
                        <span>Paid until</span>
                        <input type="date" value={editDraft.paid_until || ""} onChange={(event) => setEditDraft((current) => ({ ...current, paid_until: event.target.value }))} />
                      </label>
                      <label className="admin-wide-field">
                        <span>Billing note</span>
                        <input value={editDraft.billing_note} onChange={(event) => setEditDraft((current) => ({ ...current, billing_note: event.target.value }))} />
                      </label>
                    </div>
                    <div className="settings-actions-row">
                      <button className="primary-button" type="submit" disabled={saving}>
                        {saving ? "Saving..." : "Save user"}
                      </button>
                      {selectedUser.account_status === "active" ? (
                        <button type="button" onClick={() => handleToggleUser(false)} disabled={saving}>Disable user</button>
                      ) : (
                        <button type="button" onClick={() => handleToggleUser(true)} disabled={saving}>Enable user</button>
                      )}
                    </div>
                  </form>

                  <div className="admin-stat-grid admin-inline-stats">
                    <AdminMetric label="Created" value={selectedUser.created_at || "Unknown"} />
                    <AdminMetric label="Last login" value={selectedUser.last_login_at || "Never"} />
                    <AdminMetric label="Settings" value={selectedUser.has_settings ? "Configured" : "Missing"} />
                    <AdminMetric label="Alerts" value={selectedUser.discord_configured ? "Webhook set" : "No webhook"} />
                    <AdminMetric label="Keywords" value={selectedUser.keyword_count ?? 0} />
                    <AdminMetric label="Access" value={selectedUser.billing_access_limited ? (selectedUser.billing_access_reason || "Limited") : "Normal"} />
                  </div>

                  {selectedUsage ? (
                    <div className="admin-usage-block">
                      <div className="settings-section-header">
                        <h3>Usage</h3>
                        <p>{selectedUsage.summary?.latest_usage_date || "No usage yet"}</p>
                      </div>
                      <div className="admin-stat-grid">
                        <AdminMetric label="Searches subscribed" value={selectedUsage.summary?.search_signatures_subscribed ?? 0} />
                        <AdminMetric label="Items scored" value={selectedUsage.summary?.items_scored ?? 0} />
                        <AdminMetric label="Alerts sent" value={selectedUsage.summary?.alerts_sent ?? 0} />
                        <AdminMetric label="Detail refreshes" value={selectedUsage.summary?.detail_refreshes ?? 0} />
                        <AdminMetric label="Shared API calls" value={Number(selectedUsage.summary?.shared_api_calls_attributed ?? 0).toFixed(2)} />
                        <AdminMetric label="Usage weight" value={Number(selectedUsage.summary?.usage_weight ?? 0).toFixed(2)} />
                      </div>
                    </div>
                  ) : null}
                </section>
              ) : null}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}

function AdminMetric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{String(value)}</strong>
    </div>
  );
}

function formatReasonCounts(counts) {
  if (!counts || !Object.keys(counts).length) {
    return "None";
  }
  return Object.entries(counts)
    .sort((first, second) => Number(second[1] || 0) - Number(first[1] || 0))
    .slice(0, 5)
    .map(([key, value]) => `${key}: ${value}`)
    .join(", ");
}

function buildReplayPreviewRows(replayExport, replayResult) {
  const exportSamples = replayExport?.samples || {};
  const rows = [
    ...(exportSamples.changed_traces || []),
    ...(exportSamples.possible_false_positives || []),
    ...(exportSamples.possible_missed_gems || []),
    ...(exportSamples.needs_data || []),
    ...(exportSamples.bucket_disagreements || []),
  ];
  if (rows.length) {
    return dedupeReplayRows(rows).slice(0, 10);
  }
  const dryRunTraces = replayResult?.dry_run_traces || [];
  return dryRunTraces.map(traceToPreviewRow).slice(0, 10);
}

function traceToPreviewRow(trace) {
  const comparison = trace?.comparison || {};
  const verdict = trace?.verdict || {};
  const reasons = trace?.reasons || {};
  return {
    item_id: trace?.listing_id || "",
    title: trace?.title || "",
    persisted_status: comparison.persisted_status,
    rescored_status: comparison.rescored_status || verdict.current_app_status,
    persisted_score: comparison.persisted_score,
    rescored_score: comparison.rescored_score ?? verdict.score,
    persisted_alert_eligible: comparison.persisted_alert_eligible,
    rescored_alert_eligible: comparison.rescored_alert_eligible ?? verdict.alert_eligible,
    rescored_normalized_bucket: comparison.rescored_normalized_bucket || verdict.normalized_bucket || verdict.bucket,
    changed_status: comparison.changed_status,
    changed_alert_eligibility: comparison.changed_alert_eligibility,
    changed_reasons: comparison.changed_reasons || [],
    blocking_rules: reasons.blocking_rules || [],
    missing_data: reasons.missing_data || [],
  };
}

function dedupeReplayRows(rows) {
  const seen = new Set();
  const result = [];
  for (const row of rows) {
    const key = row.item_id || row.title || JSON.stringify(row);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(row);
  }
  return result;
}

function formatScorePair(persisted, rescored) {
  const left = persisted === undefined || persisted === null ? "n/a" : Number(persisted).toFixed(0);
  const right = rescored === undefined || rescored === null ? "n/a" : Number(rescored).toFixed(0);
  return `${left} -> ${right}`;
}

function formatBooleanPair(persisted, rescored) {
  const left = persisted === undefined || persisted === null ? "n/a" : String(Boolean(persisted));
  const right = rescored === undefined || rescored === null ? "n/a" : String(Boolean(rescored));
  return `${left} -> ${right}`;
}

function topReplayReason(row) {
  return (
    row.changed_reasons?.[0]
    || row.blocking_rules?.[0]
    || row.missing_data?.[0]
    || "none"
  );
}
