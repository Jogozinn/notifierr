import { useCallback, useEffect, useRef, useState } from "react";
import {
  addIgnoredKeyword,
  clearStoredToken,
  createUserRepairOverride,
  createUserKeyword,
  deleteItemCorrection,
  deleteUserRepairOverride,
  deleteUserKeyword,
  getAuthStatus,
  getCurrentUser,
  getDashboardItems,
  getPollingStatus,
  getUserKeywords,
  getUserNotifications,
  getUserRepairOverrides,
  getUserSettings,
  getStats,
  ignoreItem,
  ignoreSeller,
  login,
  register,
  noteItem,
  logout,
  promoteItem,
  rejectItem,
  reviewItem,
  runScan,
  setStoredToken,
  testDiscordNotification,
  updateGlobalPartCost,
  updateItemCorrection,
  updateItemFeedback,
  updateItemOutcome,
  updateUserKeyword,
  updateUserNotifications,
  updateUserSettings,
  updatePartCost,
  watchItem,
} from "./api.js";
import AdminPanel from "./components/AdminPanel.jsx";
import StatsBar from "./components/StatsBar.jsx";
import ItemTable from "./components/ItemTable.jsx";
import PushSettings from "./components/PushSettings.jsx";
import { shouldRefreshDashboard } from "./autoscanStatus.js";
import { notificationStatus } from "./notificationStatus.js";

const TABS = {
  high_quality: "GEM",
  profitable: "PROFITABLE",
  review: "REVIEW",
  unsent_actionable: "Unsent Actionable",
  missed_opportunities: "Missed Opportunities",
  priority_review: "Legacy Priority Review",
  needs_data: "Needs Data",
  watched: "Watched",
  promoted: "Promoted",
  ignored: "Ignored",
  rejected: "Rejected",
  all: "All",
};

const PRIORITY_REVIEW_MIN_PROFIT = 37.5;
const PRIORITY_REVIEW_UPSIDE = 75;
const DASHBOARD_PAGE_SIZE = 50;
const REVIEWABLE_PRICING_REASONS = [
  "Expected profit below threshold",
  "Only upside case works",
  "Low-confidence pricing needs stronger profit",
  "Too cheap without proof",
  "Profit depends on mint resale",
  "Missing part price",
  "Parts estimate not verified",
  "Low-confidence pricing",
  "Pricing confidence prevents Best Pick",
  "Reviewable despite parts/pricing gap",
];

export default function App() {
  const [activeTab, setActiveTab] = useState("priority_review");
  const [userSelectedTab, setUserSelectedTab] = useState(false);
  const [stats, setStats] = useState(null);
  const [pollingStatus, setPollingStatus] = useState(null);
  const lastBackgroundCycleRef = useRef(null);
  const [items, setItems] = useState([]);
  const [dashboardCounts, setDashboardCounts] = useState({});
  const [dashboardTotal, setDashboardTotal] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [authLoading, setAuthLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [authUser, setAuthUser] = useState(null);
  const [registrationAvailable, setRegistrationAvailable] = useState(false);
  const [firstUserSetupRequired, setFirstUserSetupRequired] = useState(false);
  const [inviteRequired, setInviteRequired] = useState(false);
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginPending, setLoginPending] = useState(false);
  const [registerEmail, setRegisterEmail] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [registerDisplayName, setRegisterDisplayName] = useState("");
  const [registerInviteCode, setRegisterInviteCode] = useState("");
  const [registerPending, setRegisterPending] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [keywordSaving, setKeywordSaving] = useState(false);
  const [userSettings, setUserSettings] = useState(null);
  const [notificationSettings, setNotificationSettings] = useState(null);
  const [userKeywords, setUserKeywords] = useState([]);
  const [repairOverrides, setRepairOverrides] = useState([]);
  const [repairOverrideDraft, setRepairOverrideDraft] = useState({
    model: "",
    part: "screen_safe",
    cost: "",
    note: "",
  });
  const [newKeyword, setNewKeyword] = useState("");
  const [discordWebhookInput, setDiscordWebhookInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshingDashboard, setRefreshingDashboard] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [includeIgnored, setIncludeIgnored] = useState(false);
  const [includeStale, setIncludeStale] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [sortBy, setSortBy] = useState("newest");
  const [keyword, setKeyword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dashboardRequestRef = useRef(0);

  useEffect(() => {
    const linkedItem = new URLSearchParams(window.location.search).get("item");
    if (linkedItem) {
      setActiveTab("all");
      setUserSelectedTab(true);
      setIncludeStale(true);
      setSearchText(linkedItem);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchText), 250);
    return () => clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    setPageOffset(0);
  }, [activeTab, sortBy, debouncedSearch, includeIgnored, includeStale]);

  const handleAuthRequired = useCallback(() => {
    clearStoredToken();
    setAuthUser(null);
    setAuthRequired(true);
    setLoading(false);
    setScanning(false);
  }, []);

  const applyAuthStatus = useCallback((status) => {
    setAuthRequired(Boolean(status?.auth_required));
    setRegistrationAvailable(Boolean(status?.registration_available));
    setFirstUserSetupRequired(Boolean(status?.first_user_setup_required));
    setInviteRequired(Boolean(status?.invite_required));
    setAuthUser(status?.current_user || null);
  }, []);

  const loadDashboard = useCallback(async (options = {}) => {
    const { background = false } = options;
    const requestId = dashboardRequestRef.current + 1;
    dashboardRequestRef.current = requestId;
    setError("");
    if (background) {
      setRefreshingDashboard(true);
    } else {
      setLoading(true);
    }
    try {
      const [nextStats, nextPage] = await Promise.all([
        getStats(),
        getDashboardItems({
          queue: activeTab,
          sort: sortBy,
          search: debouncedSearch,
          offset: pageOffset,
          limit: DASHBOARD_PAGE_SIZE,
          includeIgnored: includeIgnored || activeTab === "ignored",
          includeStale: includeStale || activeTab === "all",
        }),
      ]);
      if (dashboardRequestRef.current !== requestId) {
        return;
      }
      setStats(nextStats);
      setItems(nextPage.items);
      setDashboardCounts(nextPage.counts);
      setDashboardTotal(nextPage.total);
      if (pageOffset > 0 && pageOffset >= nextPage.total) {
        setPageOffset(Math.max(0, Math.floor((nextPage.total - 1) / DASHBOARD_PAGE_SIZE) * DASHBOARD_PAGE_SIZE));
      }
    } catch (err) {
      if (dashboardRequestRef.current !== requestId) {
        return;
      }
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      if (dashboardRequestRef.current === requestId) {
        if (background) {
          setRefreshingDashboard(false);
        } else {
          setLoading(false);
        }
      }
    }
  }, [activeTab, debouncedSearch, handleAuthRequired, includeIgnored, includeStale, pageOffset, sortBy]);

  const loadSettingsPanel = useCallback(async () => {
    setError("");
    setSettingsLoading(true);
    try {
      const [settingsResult, keywordsResult, notificationsResult, repairOverridesResult] = await Promise.all([
        getUserSettings(),
        getUserKeywords(),
        getUserNotifications(),
        getUserRepairOverrides(),
      ]);
      setUserSettings(settingsResult.settings);
      setUserKeywords(keywordsResult.keywords || []);
      setNotificationSettings(notificationsResult.notifications);
      setRepairOverrides(repairOverridesResult.repair_overrides || []);
      setDiscordWebhookInput("");
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setSettingsLoading(false);
    }
  }, [handleAuthRequired]);

  useEffect(() => {
    let cancelled = false;

    async function initializeAuth() {
      setError("");
      setAuthLoading(true);
      try {
        const status = await getAuthStatus();
        if (cancelled) {
          return;
        }
        applyAuthStatus(status);
        const nextAuthRequired = Boolean(status.auth_required);
        if (!nextAuthRequired) {
          return;
        }
        if (status.current_user) {
          return;
        }
        try {
          const current = await getCurrentUser();
          if (!cancelled) {
            setAuthUser(current.user);
          }
        } catch (err) {
          if (!cancelled) {
            clearStoredToken();
            setAuthUser(null);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message);
        }
      } finally {
        if (!cancelled) {
          setAuthLoading(false);
        }
      }
    }

    initializeAuth();
    return () => {
      cancelled = true;
    };
  }, [applyAuthStatus]);

  useEffect(() => {
    if (authLoading || (authRequired && !authUser)) {
      return;
    }
    loadDashboard();
  }, [authLoading, authRequired, authUser, loadDashboard]);

  useEffect(() => {
    if (authLoading || (authRequired && !authUser)) {
      return undefined;
    }
    let cancelled = false;
    async function refreshPollingStatus() {
      if (document.hidden || navigator.onLine === false) {
        return;
      }
      try {
        const next = await getPollingStatus();
        if (cancelled) return;
        setPollingStatus(next);
        const completedCycle = next.last_background_cycle_id ?? next.last_background_succeeded_at;
        if (shouldRefreshDashboard(lastBackgroundCycleRef.current, next)) {
          await loadDashboard({ background: true });
        }
        lastBackgroundCycleRef.current = completedCycle || lastBackgroundCycleRef.current;
      } catch (err) {
        if (!cancelled && err.status === 401) handleAuthRequired();
      }
    }
    refreshPollingStatus();
    const timer = window.setInterval(refreshPollingStatus, 45000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [authLoading, authRequired, authUser, handleAuthRequired, loadDashboard]);

  useEffect(() => {
    if (!stats || userSelectedTab) {
      return;
    }
    if ((stats.best_finds ?? 0) > 0) {
      setActiveTab("high_quality");
    } else if ((stats.priority_review ?? 0) > 0) {
      setActiveTab("priority_review");
    } else {
      setActiveTab("needs_data");
    }
  }, [stats, userSelectedTab]);

  async function handleLogin(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setLoginPending(true);
    try {
      const result = await login(loginEmail, loginPassword);
      setStoredToken(result.access_token);
      setAuthUser(result.user);
      setAuthRequired(true);
      setLoginPassword("");
      await loadDashboard();
    } catch (err) {
      setError(err.message);
    } finally {
      setLoginPending(false);
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setRegisterPending(true);
    try {
      const result = await register({
        email: registerEmail,
        password: registerPassword,
        display_name: registerDisplayName,
        invite_code: inviteRequired ? registerInviteCode : undefined,
      });
      setStoredToken(result.access_token);
      setAuthUser(result.user);
      setAuthRequired(true);
      setRegisterPassword("");
      setRegisterInviteCode("");
      const status = await getAuthStatus();
      applyAuthStatus(status);
      await loadDashboard();
    } catch (err) {
      setError(err.message);
    } finally {
      setRegisterPending(false);
    }
  }

  async function handleLogout() {
    setError("");
    setNotice("");
    try {
      await logout();
    } catch {
      // Clearing the local token is enough for the current stateless session flow.
    }
    clearStoredToken();
    setAuthUser(null);
    setStats(null);
    setItems([]);
    try {
      const status = await getAuthStatus();
      applyAuthStatus(status);
    } catch {
      setRegistrationAvailable(false);
      setFirstUserSetupRequired(false);
      setInviteRequired(false);
    }
  }

  async function openSettingsPanel() {
    setAdminOpen(false);
    setSettingsOpen(true);
    await loadSettingsPanel();
  }

  function openAdminPanel() {
    setSettingsOpen(false);
    setAdminOpen(true);
  }

  function openDashboardPanel() {
    setSettingsOpen(false);
    setAdminOpen(false);
  }

  async function handleSaveUserSettings(event) {
    event.preventDefault();
    if (!userSettings) {
      return;
    }
    setError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      const result = await updateUserSettings(userSettings);
      setUserSettings(result.settings);
      setNotice("Settings saved.");
      await loadDashboard();
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function handleSaveNotifications(event) {
    event.preventDefault();
    if (!notificationSettings) {
      return;
    }
    setError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      const result = await updateUserNotifications({
        ...notificationSettings,
        discord_webhook: discordWebhookInput.trim() || undefined,
      });
      setNotificationSettings(result.notifications);
      setDiscordWebhookInput("");
      setNotice("Notification settings saved.");
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function handleTestDiscord() {
    setError("");
    setNotice("");
    try {
      const result = await testDiscordNotification();
      const refreshed = await getUserNotifications();
      setNotificationSettings(refreshed.notifications);
      if (result.sent) {
        setNotice("Discord test notification sent.");
      } else {
        setError(result.error || "Discord delivery failed.");
      }
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    }
  }

  async function handleCreateKeyword(event) {
    event.preventDefault();
    const keywordValue = newKeyword.trim();
    if (!keywordValue) {
      return;
    }
    setError("");
    setKeywordSaving(true);
    try {
      const result = await createUserKeyword({ keyword: keywordValue, enabled: true });
      setUserKeywords((current) => [...current, result.keyword]);
      setNewKeyword("");
      setNotice("Keyword added.");
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setKeywordSaving(false);
    }
  }

  async function handleUpdateKeyword(keywordId, payload) {
    setError("");
    setKeywordSaving(true);
    try {
      const result = await updateUserKeyword(keywordId, payload);
      setUserKeywords((current) => current.map((entry) => (
        entry.id === keywordId ? result.keyword : entry
      )));
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setKeywordSaving(false);
    }
  }

  async function handleDeleteKeyword(keywordId) {
    setError("");
    setKeywordSaving(true);
    try {
      await deleteUserKeyword(keywordId);
      setUserKeywords((current) => current.filter((entry) => entry.id !== keywordId));
      setNotice("Keyword removed.");
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setKeywordSaving(false);
    }
  }

  async function handleCreateRepairOverride(event) {
    event.preventDefault();
    const model = repairOverrideDraft.model.trim();
    const cost = Number(repairOverrideDraft.cost);
    if (!model || !Number.isFinite(cost) || cost < 0) {
      return;
    }
    setError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      const result = await createUserRepairOverride({
        model,
        part: repairOverrideDraft.part,
        cost,
        note: repairOverrideDraft.note.trim(),
        source: "settings_manual_override",
      });
      setRepairOverrides((current) => {
        const next = current.filter((entry) => !(
          entry.model === result.repair_override.model && entry.part === result.repair_override.part
        ));
        next.push(result.repair_override);
        return next.sort((a, b) => `${a.model}:${a.part}`.localeCompare(`${b.model}:${b.part}`));
      });
      setRepairOverrideDraft((current) => ({ ...current, cost: "", note: "" }));
      setNotice("Repair override saved.");
      await loadDashboard();
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function handleDeleteRepairOverride(overrideId) {
    setError("");
    setNotice("");
    setSettingsSaving(true);
    try {
      await deleteUserRepairOverride(overrideId);
      setRepairOverrides((current) => current.filter((entry) => entry.id !== overrideId));
      setNotice("Repair override removed.");
      await loadDashboard();
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setSettingsSaving(false);
    }
  }

  async function handleRunScan() {
    setError("");
    setNotice("");
    setScanning(true);
    try {
      const result = await runScan();
      setNotice(
        `Scan complete: ${result.scanned} scanned, ${result.new_items_found ?? 0} new, ${result.fresh_items_found ?? 0} fresh, ${result.best_finds ?? result.candidates} best finds, ${result.priority_review ?? 0} priority review, ${result.rejected} rejected, ${result.alerts_sent ?? result.alerted} alerts sent.`,
      );
      await loadDashboard();
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    } finally {
      setScanning(false);
    }
  }

  async function handleAddIgnoredKeyword(event) {
    event.preventDefault();
    const value = keyword.trim();
    if (!value) {
      return;
    }
    await runAction(() => addIgnoredKeyword(value, "Ignored from dashboard"));
    setKeyword("");
  }

  async function runAction(action, options = {}) {
    const { updateItem = false, refreshDashboard = true } = options;
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (updateItem && result?.item_id) {
        setItems((current) => current.map((item) => (
          item.item_id === result.item_id ? { ...item, ...result } : item
        )));
      }
      if (result?.discord_sent) {
        setNotice("Manual promotion sent to Discord.");
      } else if (result?.promotion_error) {
        setNotice(`Item promoted. Discord send failed: ${result.promotion_error}`);
      } else if (result?.correction && result?.item) {
        setNotice("Item correction saved and listing re-scored.");
      } else if (result?.correction === null && result?.item) {
        setNotice("Item correction cleared and listing re-scored.");
      } else if (result?.scope === "global_baseline") {
        setNotice("Global repair baseline updated.");
      } else if (result?.item) {
        setNotice("Part cost updated and item re-scored.");
      }
      if (refreshDashboard || updateItem) {
        await loadDashboard();
      } else {
        try {
          setStats(await getStats());
        } catch (statsErr) {
          if (statsErr.status === 401) {
            handleAuthRequired();
          }
        }
      }
    } catch (err) {
      if (err.status === 401) {
        handleAuthRequired();
        return;
      }
      setError(err.message);
    }
  }

  const visibleItems = items;
  const tabCounts = dashboardCounts;
  const effectiveNotificationStatus = notificationStatus(notificationSettings);

  if (authLoading) {
    return <main className="app-shell"><div className="empty-state">Loading workspace...</div></main>;
  }

  if (authRequired && !authUser) {
    return (
      <main className="app-shell auth-shell">
        <section className="auth-panel auth-panel-wide" aria-label="Authentication">
          <div className="auth-copy auth-brand">
            <div className="brand-mark" aria-hidden="true"><span>N</span></div>
            <div>
              <h1>Notifierr</h1>
              <p>{firstUserSetupRequired ? "Create the owner account for this workspace." : "Private access to your acquisition intelligence workspace."}</p>
            </div>
          </div>
          {error ? <div className="alert alert-error">{error}</div> : null}
          <div className="auth-grid">
            <section className="auth-section">
              <div className="settings-section-header">
                <div>
                  <h2>Login</h2>
                  <p>Use your existing account.</p>
                </div>
              </div>
              <form className="auth-form" onSubmit={handleLogin}>
                <input
                  type="email"
                  value={loginEmail}
                  onChange={(event) => setLoginEmail(event.target.value)}
                  placeholder="Email"
                  aria-label="Email"
                />
                <input
                  type="password"
                  value={loginPassword}
                  onChange={(event) => setLoginPassword(event.target.value)}
                  placeholder="Password"
                  aria-label="Password"
                />
                <button className="primary-button" type="submit" disabled={loginPending}>
                  {loginPending ? "Signing in..." : "Sign in"}
                </button>
              </form>
            </section>
            {registrationAvailable ? (
              <section className="auth-section">
                <div className="settings-section-header">
                  <div>
                    <h2>{firstUserSetupRequired ? "Create owner account" : "Register"}</h2>
                    <p>{inviteRequired ? "Registration requires a valid invite code." : "First-user setup is open for the owner account."}</p>
                  </div>
                </div>
                <form className="auth-form" onSubmit={handleRegister}>
                  <input
                    type="email"
                    value={registerEmail}
                    onChange={(event) => setRegisterEmail(event.target.value)}
                    placeholder="Email"
                    aria-label="Register email"
                  />
                  <input
                    type="text"
                    value={registerDisplayName}
                    onChange={(event) => setRegisterDisplayName(event.target.value)}
                    placeholder="Display name"
                    aria-label="Display name"
                  />
                  <input
                    type="password"
                    value={registerPassword}
                    onChange={(event) => setRegisterPassword(event.target.value)}
                    placeholder="Password"
                    aria-label="Register password"
                  />
                  {inviteRequired ? (
                    <input
                      type="text"
                      value={registerInviteCode}
                      onChange={(event) => setRegisterInviteCode(event.target.value)}
                      placeholder="Invite code"
                      aria-label="Invite code"
                    />
                  ) : null}
                  <button className="primary-button" type="submit" disabled={registerPending}>
                    {registerPending ? "Creating account..." : firstUserSetupRequired ? "Create owner account" : "Create account"}
                  </button>
                </form>
              </section>
            ) : null}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar workspace-topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true"><span>N</span></div>
          <div>
            <h1>Notifierr</h1>
            <p>Repairable iPhone acquisition intelligence</p>
          </div>
        </div>
        <div className="topbar-actions">
          <div className="topbar-nav">
            <button type="button" className={!settingsOpen && !adminOpen ? "nav-button nav-button-active" : "nav-button"} onClick={openDashboardPanel}>Dashboard</button>
            <button type="button" className={settingsOpen ? "nav-button nav-button-active" : "nav-button"} onClick={openSettingsPanel}>Settings</button>
            {authUser?.role === "admin" ? (
              <button type="button" className={adminOpen ? "nav-button nav-button-active" : "nav-button"} onClick={openAdminPanel}>Admin</button>
            ) : null}
          </div>
          {authUser ? <div className="session-chip">{authUser.display_name || authUser.email}</div> : null}
          <form className="keyword-form" onSubmit={handleAddIgnoredKeyword}>
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="Ignore keyword"
              aria-label="Ignore keyword"
            />
            <button type="submit">Add</button>
          </form>
          <button className="primary-button" onClick={handleRunScan} disabled={scanning}>
            {scanning ? "Scanning..." : "Run Scan"}
          </button>
          {authUser ? <button type="button" onClick={handleLogout}>Logout</button> : null}
        </div>
      </header>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-info">{notice}</div> : null}

      <StatsBar
        stats={stats}
        pollingStatus={pollingStatus}
        counts={tabCounts}
        activeStatus={activeTab}
        onChange={(tab) => {
          setUserSelectedTab(true);
          setActiveTab(tab);
        }}
      />

      <section className="workspace-controls">
        <div className="utility-controls">
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder="Search title, model, seller, flags"
            aria-label="Search listings"
          />
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value)} aria-label="Sort listings">
            <option value="newest">Newest</option>
            <option value="profit">Estimated profit</option>
            <option value="score">Score</option>
            <option value="price">Price</option>
          </select>
          <label className="toggle-control">
            <input
              type="checkbox"
              checked={includeIgnored}
              onChange={(event) => setIncludeIgnored(event.target.checked)}
            />
            Include ignored
          </label>
          <label className="toggle-control">
            <input
              type="checkbox"
              checked={includeStale}
              onChange={(event) => setIncludeStale(event.target.checked)}
            />
            Include stale
          </label>
        </div>
      </section>

      <section className="content-header compact-header">
        <div>
          <h2>{TABS[activeTab]} listings</h2>
          <p>{dashboardTotal} matching listings{refreshingDashboard ? " · refreshing…" : ""}</p>
        </div>
      </section>

      <ItemTable
        items={visibleItems}
        loading={loading}
        isAdmin={authUser?.role === "admin"}
        onWatch={(item) => runAction(() => watchItem(item.item_id), { updateItem: true, refreshDashboard: false })}
        onReview={(item) => runAction(() => reviewItem(item.item_id), { updateItem: true, refreshDashboard: false })}
        onIgnore={(item) => {
          const reason = window.prompt("Reason for ignoring this item?", item.ignored_reason || "");
          if (reason !== null) {
            runAction(() => ignoreItem(item.item_id, reason), { updateItem: true, refreshDashboard: false });
          }
        }}
        onIgnoreSeller={(item) => {
          const reason = window.prompt("Reason for ignoring this seller?", `Seller: ${item.seller_username || ""}`);
          if (reason !== null) {
            runAction(() => ignoreSeller(item.item_id, reason), { updateItem: true, refreshDashboard: false });
          }
        }}
        onPromote={(item) => runAction(() => promoteItem(item.item_id), { updateItem: true, refreshDashboard: false })}
        onReject={(item) => {
          const reason = window.prompt("Reason for rejecting this item?", item.user_reject_reason || "");
          if (reason !== null) {
            runAction(() => rejectItem(item.item_id, reason), { updateItem: true, refreshDashboard: false });
          }
        }}
        onUpdatePartCost={(item, payload) => runAction(() => updatePartCost(item.model, { ...payload, item_id: item.item_id }))}
        onUpdateGlobalPartCost={(item, payload) => runAction(() => updateGlobalPartCost(item.model, { ...payload, item_id: item.item_id }))}
        onSaveCorrection={(item, payload) => runAction(() => updateItemCorrection(item.item_id, payload))}
        onClearCorrection={(item) => runAction(() => deleteItemCorrection(item.item_id))}
        onFeedback={(item, feedbackCode) => runAction(
          () => updateItemCorrection(item.item_id, { feedback_code: feedbackCode }),
          { updateItem: true, refreshDashboard: false },
        )}
        onLabel={(item, label) => runAction(
          () => updateItemFeedback(item.item_id, label),
          { refreshDashboard: true },
        )}
        onOutcome={(item, payload) => runAction(
          () => updateItemOutcome(item.item_id, payload),
          { refreshDashboard: true },
        )}
        onNote={(item) => {
          const note = window.prompt("Note for this listing", item.user_note || "");
          if (note !== null) {
            runAction(() => noteItem(item.item_id, note));
          }
        }}
      />
      {dashboardTotal > DASHBOARD_PAGE_SIZE ? (
        <nav className="dashboard-pagination" aria-label="Listing pages">
          <button type="button" disabled={pageOffset === 0} onClick={() => setPageOffset((current) => Math.max(0, current - DASHBOARD_PAGE_SIZE))}>Previous</button>
          <span>{pageOffset + 1}–{Math.min(pageOffset + DASHBOARD_PAGE_SIZE, dashboardTotal)} of {dashboardTotal}</span>
          <button type="button" disabled={pageOffset + DASHBOARD_PAGE_SIZE >= dashboardTotal} onClick={() => setPageOffset((current) => current + DASHBOARD_PAGE_SIZE)}>Next</button>
        </nav>
      ) : null}

      {settingsOpen ? (
        <section className="settings-overlay" aria-label="User settings">
          <div className="settings-panel">
            <div className="settings-header">
              <div>
                <h2>Settings</h2>
                <p>Thresholds, polling, keywords, and notifications.</p>
              </div>
              <button type="button" onClick={() => setSettingsOpen(false)}>Close</button>
            </div>

            {settingsLoading ? <div className="empty-state">Loading settings...</div> : null}

            {!settingsLoading && userSettings ? (
              <form className="settings-form" onSubmit={handleSaveUserSettings}>
                <div className="settings-grid">
                  <label>
                    <span>Min score to alert</span>
                    <input type="number" min="0" max="100" step="0.01" value={userSettings.min_score_to_alert} onChange={(event) => setUserSettings((current) => ({ ...current, min_score_to_alert: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Min profit to alert</span>
                    <input type="number" min="0" step="0.01" value={userSettings.min_profit_to_alert} onChange={(event) => setUserSettings((current) => ({ ...current, min_profit_to_alert: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Risky score min</span>
                    <input type="number" min="0" max="100" step="0.01" value={userSettings.risky_score_min} onChange={(event) => setUserSettings((current) => ({ ...current, risky_score_min: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Risky score max</span>
                    <input type="number" min="0" max="100" step="0.01" value={userSettings.risky_score_max} onChange={(event) => setUserSettings((current) => ({ ...current, risky_score_max: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Max alert age (minutes)</span>
                    <input type="number" min="1" value={userSettings.max_alert_item_age_minutes} onChange={(event) => setUserSettings((current) => ({ ...current, max_alert_item_age_minutes: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Priority review age (hours)</span>
                    <input type="number" min="1" value={userSettings.max_priority_review_item_age_hours} onChange={(event) => setUserSettings((current) => ({ ...current, max_priority_review_item_age_hours: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Active queue age (hours)</span>
                    <input type="number" min="1" value={userSettings.max_active_queue_item_age_hours} onChange={(event) => setUserSettings((current) => ({ ...current, max_active_queue_item_age_hours: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Default resale condition</span>
                    <select value={userSettings.default_resale_condition} onChange={(event) => setUserSettings((current) => ({ ...current, default_resale_condition: event.target.value }))}>
                      <option value="good">Good</option>
                      <option value="mint">Mint</option>
                    </select>
                  </label>
                  <label>
                    <span>Target min model generation</span>
                    <input type="number" min="0" max="30" value={userSettings.target_min_model_generation} onChange={(event) => setUserSettings((current) => ({ ...current, target_min_model_generation: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Background poll seconds</span>
                    <input type="number" min="0" value={userSettings.background_poll_seconds} onChange={(event) => setUserSettings((current) => ({ ...current, background_poll_seconds: Number(event.target.value) }))} />
                  </label>
                  <label>
                    <span>Active start</span>
                    <input value={userSettings.active_start || ""} onChange={(event) => setUserSettings((current) => ({ ...current, active_start: event.target.value || null }))} placeholder="08:00" />
                  </label>
                  <label>
                    <span>Active end</span>
                    <input value={userSettings.active_end || ""} onChange={(event) => setUserSettings((current) => ({ ...current, active_end: event.target.value || null }))} placeholder="23:00" />
                  </label>
                  <label>
                    <span>Timezone</span>
                    <input value={userSettings.timezone || ""} onChange={(event) => setUserSettings((current) => ({ ...current, timezone: event.target.value }))} placeholder="America/New_York" />
                  </label>
                </div>
                <div className="settings-checks">
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(userSettings.allow_mint_for_alerts)} onChange={(event) => setUserSettings((current) => ({ ...current, allow_mint_for_alerts: event.target.checked }))} />
                    Allow mint resale for alerts
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(userSettings.background_poll_enabled)} onChange={(event) => setUserSettings((current) => ({ ...current, background_poll_enabled: event.target.checked }))} />
                    Background poll enabled
                  </label>
                </div>
                <button className="primary-button" type="submit" disabled={settingsSaving}>
                  {settingsSaving ? "Saving..." : "Save settings"}
                </button>
              </form>
            ) : null}

            {!settingsLoading && notificationSettings ? (
              <form className="settings-form settings-section" onSubmit={handleSaveNotifications}>
                <div className="settings-section-header">
                  <h3>Notifications</h3>
                  <p className={`notification-readiness notification-readiness-${effectiveNotificationStatus.tone}`}>
                    {effectiveNotificationStatus.label}
                  </p>
                </div>
                <div className="settings-grid">
                  <label>
                    <span>Discord webhook</span>
                    <input value={discordWebhookInput} onChange={(event) => setDiscordWebhookInput(event.target.value)} placeholder={notificationSettings.discord_webhook_configured ? "Configured - enter a new webhook to replace" : "Paste Discord webhook"} />
                  </label>
                </div>
                <div className="settings-checks">
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.discord_enabled)} onChange={(event) => setNotificationSettings((current) => ({ ...current, discord_enabled: event.target.checked }))} />
                    Discord enabled
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.push_enabled)} onChange={(event) => setNotificationSettings((current) => ({ ...current, push_enabled: event.target.checked }))} />
                    Phone and browser push enabled
                  </label>
                  {notificationSettings.global_discord_webhook_configured ? (
                    <label className="toggle-control">
                      <input type="checkbox" checked={Boolean(notificationSettings.use_global_discord_webhook)} onChange={(event) => setNotificationSettings((current) => ({ ...current, use_global_discord_webhook: event.target.checked }))} />
                      Use configured global Discord destination
                    </label>
                  ) : null}
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.alerts_enabled)} onChange={(event) => setNotificationSettings((current) => ({ ...current, alerts_enabled: event.target.checked }))} />
                    Alerts enabled
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.notify_best_finds)} onChange={(event) => setNotificationSettings((current) => ({ ...current, notify_best_finds: event.target.checked }))} />
                    Notify best finds
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.notify_priority_review)} onChange={(event) => setNotificationSettings((current) => ({ ...current, notify_priority_review: event.target.checked }))} />
                    Notify priority review
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.send_gem_immediately)} onChange={(event) => setNotificationSettings((current) => ({ ...current, send_gem_immediately: event.target.checked }))} />
                    Send GEM immediately
                  </label>
                  <label className="toggle-control">
                    <input type="checkbox" checked={Boolean(notificationSettings.send_profitable_immediately)} onChange={(event) => setNotificationSettings((current) => ({ ...current, send_profitable_immediately: event.target.checked }))} />
                    Send PROFITABLE immediately
                  </label>
                </div>
                <div className="settings-grid">
                  <label><span>REVIEW delivery</span><select value={notificationSettings.review_delivery_mode || "immediate"} onChange={(event) => setNotificationSettings((current) => ({ ...current, review_delivery_mode: event.target.value }))}><option value="immediate">Immediate</option><option value="digest">Digest</option><option value="off">Off</option></select></label>
                  <NumberSetting label="Max REVIEW alerts/hour" field="max_review_alerts_per_hour" value={notificationSettings.max_review_alerts_per_hour} setSettings={setNotificationSettings} />
                  <NumberSetting label="Duplicate suppression hours" field="duplicate_suppression_hours" value={notificationSettings.duplicate_suppression_hours} setSettings={setNotificationSettings} />
                  <NumberSetting label="Meaningful price drop ($)" field="meaningful_price_drop_amount" value={notificationSettings.meaningful_price_drop_amount} setSettings={setNotificationSettings} />
                  <NumberSetting label="Meaningful price drop (ratio)" field="meaningful_price_drop_percent" value={notificationSettings.meaningful_price_drop_percent} setSettings={setNotificationSettings} />
                  <NumberSetting label="Meaningful profit increase ($)" field="meaningful_profit_increase_amount" value={notificationSettings.meaningful_profit_increase_amount} setSettings={setNotificationSettings} />
                  <NumberSetting label="Meaningful profit increase (ratio)" field="meaningful_profit_increase_percent" value={notificationSettings.meaningful_profit_increase_percent} setSettings={setNotificationSettings} />
                  <NumberSetting label="Meaningful ROI increase (ratio)" field="meaningful_roi_increase" value={notificationSettings.meaningful_roi_increase} setSettings={setNotificationSettings} />
                  <NumberSetting label="Catch-up batch size" field="catchup_batch_size" value={notificationSettings.catchup_batch_size} setSettings={setNotificationSettings} />
                  <NumberSetting label="GEM minimum profit" field="gem_min_expected_profit" value={notificationSettings.gem_min_expected_profit} setSettings={setNotificationSettings} />
                  <NumberSetting label="PROFITABLE minimum profit" field="profitable_min_expected_profit" value={notificationSettings.profitable_min_expected_profit} setSettings={setNotificationSettings} />
                  <NumberSetting label="REVIEW minimum expected profit" field="review_min_expected_profit" value={notificationSettings.review_min_expected_profit} setSettings={setNotificationSettings} />
                  <NumberSetting label="REVIEW minimum upside" field="review_min_upside_profit" value={notificationSettings.review_min_upside_profit} setSettings={setNotificationSettings} />
                  <NumberSetting label="Maximum listing age (minutes)" field="max_listing_age_minutes" value={notificationSettings.max_listing_age_minutes} setSettings={setNotificationSettings} />
                </div>
                <div className="settings-actions-row">
                  <button className="primary-button" type="submit" disabled={settingsSaving}>
                    {settingsSaving ? "Saving..." : "Save notifications"}
                  </button>
                  <button type="button" onClick={handleTestDiscord} disabled={!notificationSettings.notification_ready}>
                    Test Discord
                  </button>
                </div>
                <PushSettings onChanged={async () => {
                  const refreshed = await getUserNotifications();
                  setNotificationSettings(refreshed.notifications);
                }} />
              </form>
            ) : null}

            {!settingsLoading ? (
              <section className="settings-section">
                <div className="settings-section-header">
                  <h3>Keywords</h3>
                  <p>Baseline keywords stay attached to your account and can be disabled.</p>
                </div>
                <form className="keyword-form settings-keyword-form" onSubmit={handleCreateKeyword}>
                  <input value={newKeyword} onChange={(event) => setNewKeyword(event.target.value)} placeholder="Add keyword" aria-label="Add keyword" />
                  <button type="submit" disabled={keywordSaving}>Add keyword</button>
                </form>
                <div className="settings-keyword-list">
                  {userKeywords.map((entry) => (
                    <div className="settings-keyword-row" key={entry.id}>
                      <div>
                        <strong>{entry.keyword}</strong>
                        <p>{entry.is_baseline ? "Baseline keyword" : "Custom keyword"}</p>
                      </div>
                      <div className="settings-actions-row">
                        <label className="toggle-control">
                          <input type="checkbox" checked={Boolean(entry.enabled)} onChange={(event) => handleUpdateKeyword(entry.id, { enabled: event.target.checked })} />
                          Enabled
                        </label>
                        {!entry.is_baseline ? <button type="button" onClick={() => handleDeleteKeyword(entry.id)}>Delete</button> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            {!settingsLoading ? (
              <section className="settings-section">
                <div className="settings-section-header">
                  <h3>Repair overrides</h3>
                  <p>Your private part-cost overrides sit on top of the global baseline.</p>
                </div>
                <form className="settings-form" onSubmit={handleCreateRepairOverride}>
                  <div className="settings-grid">
                    <label>
                      <span>Model</span>
                      <input
                        value={repairOverrideDraft.model}
                        onChange={(event) => setRepairOverrideDraft((current) => ({ ...current, model: event.target.value }))}
                        placeholder="iPhone 14"
                      />
                    </label>
                    <label>
                      <span>Part</span>
                      <select
                        value={repairOverrideDraft.part}
                        onChange={(event) => setRepairOverrideDraft((current) => ({ ...current, part: event.target.value }))}
                      >
                        <option value="screen_budget">Screen budget</option>
                        <option value="screen_safe">Screen safe</option>
                        <option value="screen_premium">Screen premium</option>
                        <option value="battery">Battery</option>
                        <option value="back_glass">Back glass</option>
                        <option value="camera_lens">Camera lens</option>
                        <option value="charging_port">Charging port</option>
                      </select>
                    </label>
                    <label>
                      <span>Cost</span>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={repairOverrideDraft.cost}
                        onChange={(event) => setRepairOverrideDraft((current) => ({ ...current, cost: event.target.value }))}
                        placeholder="95.00"
                      />
                    </label>
                    <label>
                      <span>Note</span>
                      <input
                        value={repairOverrideDraft.note}
                        onChange={(event) => setRepairOverrideDraft((current) => ({ ...current, note: event.target.value }))}
                        placeholder="Optional source or note"
                      />
                    </label>
                  </div>
                  <button className="primary-button" type="submit" disabled={settingsSaving}>
                    {settingsSaving ? "Saving..." : "Save repair override"}
                  </button>
                </form>
                <div className="settings-keyword-list">
                  {repairOverrides.length ? repairOverrides.map((entry) => (
                    <div className="settings-keyword-row" key={entry.id}>
                      <div>
                        <strong>{entry.model} · {entry.part}</strong>
                        <p>{Number(entry.cost).toFixed(2)}{entry.note ? ` · ${entry.note}` : ""}</p>
                      </div>
                      <div className="settings-actions-row">
                        <button type="button" onClick={() => handleDeleteRepairOverride(entry.id)}>Delete</button>
                      </div>
                    </div>
                  )) : <div className="empty-state">No repair overrides saved.</div>}
                </div>
              </section>
            ) : null}
          </div>
        </section>
      ) : null}

      {adminOpen && authUser?.role === "admin" ? (
        <AdminPanel
          onClose={openDashboardPanel}
          onUnauthorized={handleAuthRequired}
          onError={setError}
          onNotice={setNotice}
        />
      ) : null}
    </main>
  );
}

function NumberSetting({ label, field, value, setSettings }) {
  return (
    <label>
      <span>{label}</span>
      <input type="number" min="0" value={value ?? 0} onChange={(event) => setSettings((current) => ({ ...current, [field]: Number(event.target.value) }))} />
    </label>
  );
}

function tabMatches(item, tab) {
  if (tab === "action_needed") {
    return isReviewQueueItem(item);
  }
  if (tab === "priority_review") {
    return isPriorityReviewItem(item);
  }
  if (tab === "high_quality") {
    return item.alert_tier === "GEM";
  }
  if (tab === "profitable") {
    return item.alert_tier === "PROFITABLE";
  }
  if (tab === "review") {
    return item.alert_tier === "REVIEW";
  }
  if (tab === "unsent_actionable") {
    return item.never_notified_actionable === true;
  }
  if (tab === "missed_opportunities") {
    return item.alert_tier === "REVIEW" || item.alert_tier === "PROFITABLE" || (
      Number(item.profit_mid || 0) > 0 && (item.alert_decision?.blocking_reasons || []).length <= 2
    );
  }
  if (tab === "needs_data") {
    return isNeedsDataItem(item);
  }
  if (tab === "watched") {
    return item.user_status === "watched";
  }
  if (tab === "promoted") {
    return item.user_status === "promoted" && Boolean(item.promoted_at);
  }
  if (tab === "rejected") {
    return (item.status === "rejected" || item.user_status === "rejected") && item.user_status !== "ignored";
  }
  if (tab === "ignored") {
    return item.user_status === "ignored";
  }
  if (tab === "all") {
    return item.user_status !== "ignored";
  }
  return false;
}

function isBestFind(item) {
  return ["candidate", "alerted"].includes(item.status)
    && item.alert_eligible === true
    && !["ignored", "rejected"].includes(item.user_status)
    && !isUnavailable(item)
    && item.buying_option_summary !== "auction"
    && item.fresh_for_alert !== false
    && item.stale !== true;
}

function isReviewQueueItem(item) {
  if (item.user_status !== "new" || item.user_status === "ignored" || item.status === "rejected") {
    return false;
  }
  if (item.alert_eligible === true) {
    return false;
  }
  if (!item.whole_phone_confidence_passed || !item.has_repair_issue || !hasKnownModel(item)) {
    return false;
  }
  const strongManual = isStrongManualCandidate(item);
  if (hasExcludedHardReject(item) || isAccessoryOrPartListing(item) || (isNeedsDataItem(item) && !strongManual)) {
    return false;
  }
  return isBestFind(item) || hasProfitData(item) || strongManual;
}

function isPriorityReviewItem(item) {
  if (isUnavailable(item)) {
    return false;
  }
  if (item.user_status !== "new" || item.user_status === "ignored" || item.status === "rejected") {
    return false;
  }
  if (item.alert_eligible === true) {
    return false;
  }
  if (item.fresh_for_priority_review === false || item.stale === true) {
    return false;
  }
  if (!item.whole_phone_confidence_passed || !item.has_repair_issue || !hasKnownModel(item)) {
    return false;
  }
  if (hasExcludedHardReject(item) || isAccessoryOrPartListing(item)) {
    return false;
  }
  if (usesModelResaleWithoutStorage(item) && !storageFallbackPriorityException(item)) {
    return false;
  }
  return (
    Number(item.profit_mid || item.estimated_profit || 0) >= PRIORITY_REVIEW_MIN_PROFIT
    || Number(item.profit_high || 0) >= PRIORITY_REVIEW_UPSIDE
    || hasManualReason(item, ["Too cheap without proof"])
    || hasManualReason(item, ["Profit depends on mint resale"])
    || (
      item.estimated_parts_cost_available === false
      && Number(item.resale_mid || item.resale_value || 0) > 0
    )
    || isReviewableDespitePricingGap(item)
  );
}

function isNeedsDataItem(item) {
  if (isUnavailable(item)) {
    return false;
  }
  if (["ignored", "rejected"].includes(item.user_status) || item.status === "rejected") {
    return false;
  }
  if (item.stale === true) {
    return false;
  }
  if (isPriorityReviewItem(item) || isReviewableDespitePricingGap(item)) {
    return false;
  }
  return (
    !hasKnownModel(item)
    || (usesModelResaleWithoutStorage(item) && !storageFallbackPriorityException(item))
    || (Boolean(item.storage_resale_warning) && item.resale_source !== "storage_specific")
    || Number(item.resale_value || item.resale_mid || 0) <= 0
    || item.estimated_parts_cost_available === false
    || item.has_repair_issue === false
    || hasManualReason(item, [
      "Parts-only ambiguous",
      "Read description listing",
      "Expected profit below threshold",
      "Only upside case works",
      "Low-confidence pricing needs stronger profit",
      "Too cheap without proof",
      "Profit depends on mint resale",
      "Parts-only listing lacks power/iCloud/IMEI proof",
      "Model/spec mismatch",
      "Missing part price",
      "Model unknown",
      "No specific repair issue detected",
    ])
  );
}

function hasReviewableDescriptionEvidence(item) {
  const proofFlags = new Set(item.positive_flags || []);
  const classificationFlags = new Set(item.listing_classification_flags || []);
  const strongProofCount = ["powers_on", "clean_imei", "face_id_works", "unlocked"]
    .filter((flag) => proofFlags.has(flag)).length;
  const hasRawDetail = Boolean((item.raw_description || "").trim());
  const hasDescriptionEvidence = [
    "description_functionality_evidence",
    "normal_accessory_exclusions",
  ].some((flag) => classificationFlags.has(flag))
    || (classificationFlags.has("description_whole_phone_evidence") && hasRawDetail);
  return item.whole_phone_confidence_passed === true
    && item.has_repair_issue === true
    && hasKnownModel(item)
    && Boolean(item.storage_capacity)
    && (
      strongProofCount >= 2
      || (strongProofCount >= 1 && hasDescriptionEvidence)
      || (hasDescriptionEvidence && Number(item.whole_phone_score || 0) >= 7)
    );
}

function isReviewableDespitePricingGap(item) {
  if (!hasReviewableDescriptionEvidence(item)) {
    return false;
  }
  if (hasExcludedHardReject(item) || isAccessoryOrPartListing(item)) {
    return false;
  }
  if (usesModelResaleWithoutStorage(item) && !storageFallbackPriorityException(item)) {
    return false;
  }
  if (Number(item.resale_value || item.resale_mid || 0) <= 0) {
    return false;
  }
  return item.estimated_parts_cost_available === false
    || item.estimated_profit_available === false
    || hasManualReason(item, REVIEWABLE_PRICING_REASONS);
}

function usesModelResaleWithoutStorage(item) {
  return !item.storage_capacity && ["model_range", "legacy_resale_value"].includes(item.resale_source);
}

function storageFallbackPriorityException(item) {
  if (["watched", "promoted"].includes(item.user_status)) {
    return true;
  }
  if (Number(item.profit_mid || item.estimated_profit || 0) >= 150) {
    return true;
  }
  const proofFlags = new Set(item.positive_flags || []);
  const strongProofCount = ["powers_on", "clean_imei", "face_id_works", "unlocked"]
    .filter((flag) => proofFlags.has(flag)).length;
  return item.has_repair_issue === true
    && item.estimated_profit_available === true
    && strongProofCount >= 2;
}

function isUnavailable(item) {
  return ["sold", "ended", "unavailable"].includes(item.availability_status || "unknown");
}

function hasKnownModel(item) {
  return Boolean(item.model && item.model !== "unknown");
}

function hasProfitData(item) {
  return item.estimated_profit_available === true
    && Number(item.resale_value || item.resale_mid || 0) > 0
    && item.estimated_parts_cost_available !== false;
}

function isStrongManualCandidate(item) {
  return item.whole_phone_confidence_passed === true
    && item.has_repair_issue === true
    && hasKnownModel(item)
    && hasProfitData(item)
    && hasManualReason(item, ["Read description listing"])
    && Number(item.estimated_profit || item.profit_mid || 0) > 0;
}

function isAccessoryOrPartListing(item) {
  const flags = [...(item.listing_classification_flags || []), ...(item.hard_reject_flags || [])];
  return flags.some((flag) => flag.endsWith("_not_phone") || flag === "lot_not_single_phone");
}

function hasExcludedHardReject(item) {
  const flags = item.hard_reject_flags || [];
  return flags.some((flag) => [
    "old_model_ignored",
    "lot_not_single_phone",
    "no_power",
    "does_not_turn_on",
    "icloud_locked",
    "activation_locked",
    "mdm_locked",
    "water_damage",
    "liquid_damage",
    "baseband",
  ].includes(flag));
}

function hasManualReason(item, reasons) {
  const text = item.manual_review_reason || "";
  return reasons.some((reason) => text.includes(reason));
}

function textMatches(item, searchText) {
  const query = searchText.trim().toLowerCase();
  if (!query) {
    return true;
  }
  const haystack = [
    item.title,
    item.model,
    item.seller_username,
    item.manual_review_reason,
    item.pricing_warning,
    ...(item.positive_flags || []),
    ...(item.risk_flags || []),
    ...(item.hard_reject_flags || []),
    ...(item.listing_classification_flags || []),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function sortItems(items, sortBy, activeTab) {
  return [...items].sort((a, b) => {
    if (activeTab === "priority_review" && sortBy === "newest") {
      return prioritySortValue(b) - prioritySortValue(a)
        || Number(b.score || 0) - Number(a.score || 0)
        || new Date(b.found_at || 0).getTime() - new Date(a.found_at || 0).getTime();
    }
    if (sortBy === "profit") {
      return sortProfitValue(b) - sortProfitValue(a);
    }
    if (sortBy === "score") {
      return Number(b.score || 0) - Number(a.score || 0);
    }
    if (sortBy === "price") {
      return Number(a.total_cost || 0) - Number(b.total_cost || 0);
    }
    return new Date(b.found_at || 0).getTime() - new Date(a.found_at || 0).getTime();
  });
}

function sortProfitValue(item) {
  return item.estimated_profit_available ? Number(item.estimated_profit || 0) : -Infinity;
}

function prioritySortValue(item) {
  return Math.max(Number(item.profit_high || 0), Number(item.estimated_profit || 0), Number(item.profit_mid || 0));
}

function buildTabCounts(items) {
  return Object.fromEntries(
    Object.keys(TABS).map((tab) => [tab, items.filter((item) => tabMatches(item, tab)).length]),
  );
}
