import { useEffect, useMemo, useState } from "react";
import {
  createAdminInvite,
  createAdminUser,
  disableAdminUser,
  enableAdminUser,
  getAdminInvites,
  getAdminScanStats,
  getAdminUserUsage,
  getAdminUsers,
  revokeAdminInvite,
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

export default function AdminPanel({ onClose, onUnauthorized, onError, onNotice }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [users, setUsers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [scanStats, setScanStats] = useState(null);
  const [selectedUserId, setSelectedUserId] = useState(null);
  const [selectedUsage, setSelectedUsage] = useState(null);
  const [createDraft, setCreateDraft] = useState(EMPTY_CREATE_DRAFT);
  const [inviteDraft, setInviteDraft] = useState(EMPTY_INVITE_DRAFT);
  const [editDraft, setEditDraft] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const [usersResult, invitesResult, scanStatsResult] = await Promise.all([
          getAdminUsers(),
          getAdminInvites(),
          getAdminScanStats(),
        ]);
        if (cancelled) {
          return;
        }
        const nextUsers = usersResult.users || [];
        setInvites(invitesResult.invites || []);
        setUsers(nextUsers);
        setScanStats(scanStatsResult);
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
          onUnauthorized();
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
          onUnauthorized();
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

  async function refreshUsers({ notice = "" } = {}) {
    const [usersResult, invitesResult, scanStatsResult] = await Promise.all([
      getAdminUsers(),
      getAdminInvites(),
      getAdminScanStats(),
    ]);
    const nextUsers = usersResult.users || [];
    setUsers(nextUsers);
    setInvites(invitesResult.invites || []);
    setScanStats(scanStatsResult);
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
        onUnauthorized();
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
        onUnauthorized();
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
        onUnauthorized();
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
        onUnauthorized();
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
        onUnauthorized();
        return;
      }
      onError(err.message);
    } finally {
      setSaving(false);
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

        {!loading && scanStats ? (
          <section className="settings-section">
            <div className="settings-section-header">
              <h3>Shared scan status</h3>
              <p>{scanStats.worker_status || scanStats.status || "unknown"}</p>
            </div>
            <div className="admin-stat-grid">
              <AdminMetric label="Last shared scan" value={scanStats.last_shared_scan_time || "Never"} />
              <AdminMetric label="Active users" value={scanStats.active_users ?? 0} />
              <AdminMetric label="Unique searches" value={scanStats.unique_searches ?? 0} />
              <AdminMetric label="Shared API calls" value={scanStats.shared_api_calls ?? 0} />
              <AdminMetric label="Items ingested" value={scanStats.total_items_ingested ?? 0} />
              <AdminMetric label="Alerts sent" value={scanStats.alerts_sent ?? 0} />
            </div>
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
