import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addIgnoredKeyword,
  getItems,
  getStats,
  ignoreItem,
  ignoreSeller,
  noteItem,
  promoteItem,
  reviewItem,
  runScan,
  watchItem,
} from "./api.js";
import StatsBar from "./components/StatsBar.jsx";
import ItemTable from "./components/ItemTable.jsx";

const TABS = {
  high_quality: "Best Finds",
  priority_review: "Priority Review",
  needs_data: "Needs Data",
  watched: "Watched",
  ignored: "Ignored",
  rejected: "Rejected",
  all: "All",
};

const PRIORITY_REVIEW_MIN_PROFIT = 37.5;
const PRIORITY_REVIEW_UPSIDE = 75;

export default function App() {
  const [activeTab, setActiveTab] = useState("priority_review");
  const [userSelectedTab, setUserSelectedTab] = useState(false);
  const [stats, setStats] = useState(null);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [includeIgnored, setIncludeIgnored] = useState(false);
  const [includeStale, setIncludeStale] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [sortBy, setSortBy] = useState("newest");
  const [keyword, setKeyword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadDashboard = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const [nextStats, nextItems] = await Promise.all([
        getStats(),
        getItems({
          includeIgnored: includeIgnored || activeTab === "ignored",
          includeStale: includeStale || activeTab === "all",
        }),
      ]);
      setStats(nextStats);
      setItems(nextItems);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [activeTab, includeIgnored, includeStale]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

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

  async function runAction(action) {
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (result?.discord_sent) {
        setNotice("Manual promotion sent to Discord.");
      }
      await loadDashboard();
    } catch (err) {
      setError(err.message);
    }
  }

  const visibleItems = useMemo(() => {
    const filtered = items
      .filter((item) => tabMatches(item, activeTab))
      .filter((item) => textMatches(item, searchText));
    return sortItems(filtered, sortBy, activeTab);
  }, [activeTab, items, searchText, sortBy]);

  const tabCounts = useMemo(() => buildTabCounts(items), [items]);

  return (
    <main className="app-shell">
      <header className="topbar workspace-topbar">
        <div>
          <h1>Notifierr</h1>
          <p>Local broken-iPhone deal review workspace</p>
        </div>
        <div className="topbar-actions">
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
        </div>
      </header>

      {error ? <div className="alert alert-error">{error}</div> : null}
      {notice ? <div className="alert alert-info">{notice}</div> : null}

      <StatsBar
        stats={stats}
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
          <p>{visibleItems.length} visible after filters</p>
        </div>
      </section>

      <ItemTable
        items={visibleItems}
        loading={loading}
        onWatch={(item) => runAction(() => watchItem(item.item_id))}
        onReview={(item) => runAction(() => reviewItem(item.item_id))}
        onIgnore={(item) => {
          const reason = window.prompt("Reason for ignoring this item?", item.ignored_reason || "");
          if (reason !== null) {
            runAction(() => ignoreItem(item.item_id, reason));
          }
        }}
        onIgnoreSeller={(item) => {
          const reason = window.prompt("Reason for ignoring this seller?", `Seller: ${item.seller_username || ""}`);
          if (reason !== null) {
            runAction(() => ignoreSeller(item.item_id, reason));
          }
        }}
        onPromote={(item) => runAction(() => promoteItem(item.item_id))}
        onNote={(item) => {
          const note = window.prompt("Note for this listing", item.user_note || "");
          if (note !== null) {
            runAction(() => noteItem(item.item_id, note));
          }
        }}
      />
    </main>
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
    return isBestFind(item);
  }
  if (tab === "needs_data") {
    return isNeedsDataItem(item);
  }
  if (tab === "watched") {
    return item.user_status === "watched";
  }
  if (tab === "rejected") {
    return item.status === "rejected" && item.user_status !== "ignored";
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
    && item.user_status !== "ignored"
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
  return (
    Number(item.profit_mid || item.estimated_profit || 0) >= PRIORITY_REVIEW_MIN_PROFIT
    || Number(item.profit_high || 0) >= PRIORITY_REVIEW_UPSIDE
    || hasManualReason(item, ["Too cheap without proof"])
    || (
      item.estimated_parts_cost_available === false
      && Number(item.resale_mid || item.resale_value || 0) > 0
    )
  );
}

function isNeedsDataItem(item) {
  if (item.user_status === "ignored" || item.status === "rejected") {
    return false;
  }
  if (item.stale === true) {
    return false;
  }
  return (
    !hasKnownModel(item)
    || Number(item.resale_value || item.resale_mid || 0) <= 0
    || item.estimated_parts_cost_available === false
    || item.has_repair_issue === false
    || hasManualReason(item, [
      "Parts-only ambiguous",
      "Read description listing",
      "Expected profit below threshold",
      "Only optimistic profit clears threshold",
      "Low-confidence pricing needs stronger profit",
      "Too cheap without proof",
      "Parts-only listing lacks power/iCloud/IMEI proof",
      "Model/spec mismatch",
      "Missing part price",
      "Model unknown",
      "No specific repair issue detected",
    ])
  );
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
