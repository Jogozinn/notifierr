import { useEffect, useState } from "react";
import {
  disableBackgroundPush,
  enableBackgroundPush,
  getLocalPushSubscription,
  getPushConfig,
  pushCapability,
  testPushNotification,
} from "../push.js";

export default function PushSettings({ onChanged }) {
  const [capability, setCapability] = useState(null);
  const [config, setConfig] = useState(null);
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setCapability(pushCapability());
    const [nextConfig, local] = await Promise.all([
      getPushConfig(),
      getLocalPushSubscription().catch(() => null),
    ]);
    setConfig(nextConfig);
    setSubscribed(Boolean(local));
  }

  useEffect(() => {
    refresh().catch((reason) => setError(reason.message));
  }, []);

  async function run(action, success) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await action();
      await refresh();
      await onChanged?.();
      setMessage(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Notification action failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!capability || !config) return <p className="muted-copy">Checking push support…</p>;

  return (
    <div className="push-settings" aria-label="Background notifications">
      <div className="settings-section-header">
        <div>
          <h4>Phone and browser push</h4>
          <p>{config.active_subscriptions} active device{config.active_subscriptions === 1 ? "" : "s"}</p>
        </div>
        <span className={`push-state ${subscribed ? "push-state-on" : ""}`}>
          {subscribed ? "This device is on" : "This device is off"}
        </span>
      </div>
      {capability.ios && !capability.standalone ? (
        <div className="push-guidance">
          On iPhone, open this site in Safari, tap <strong>Share → Add to Home Screen</strong>,
          then open the installed Notifierr app and return to Settings.
        </div>
      ) : null}
      {!config.enabled ? <div className="push-guidance">Server VAPID keys still need to be configured.</div> : null}
      <div className="settings-actions-row">
        {!subscribed ? (
          <button
            type="button"
            disabled={busy || !config.enabled || !capability.supported}
            onClick={() => run(enableBackgroundPush, "This device is subscribed. Send a test to verify delivery.")}
          >
            {busy ? "Enabling…" : "Enable on this device"}
          </button>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => run(testPushNotification, "Test push accepted by the provider.")}>Send test push</button>
            <button type="button" disabled={busy} onClick={() => run(disableBackgroundPush, "Push disabled on this device.")}>Disable this device</button>
          </>
        )}
      </div>
      {message ? <p className="push-message push-message-ok">{message}</p> : null}
      {error ? <p className="push-message push-message-error">{error}</p> : null}
    </div>
  );
}
