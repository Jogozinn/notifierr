import {
  createPushSubscription,
  deletePushSubscription,
  getPushConfig,
  testPushNotification,
} from "./api.js";

export function pushCapability() {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches === true ||
    Boolean(navigator.standalone);
  const supported =
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window &&
    (!ios || standalone);
  return {
    supported,
    ios,
    standalone,
    permission: "Notification" in window ? Notification.permission : "unsupported",
  };
}

export function urlBase64ToUint8Array(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replaceAll("-", "+").replaceAll("_", "/");
  const raw = window.atob(base64);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function activeRegistration() {
  const existing = await navigator.serviceWorker.getRegistration();
  if (existing) return existing;
  return navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" });
}

export async function getLocalPushSubscription() {
  if (!("serviceWorker" in navigator)) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  return registration?.pushManager.getSubscription() ?? null;
}

export async function enableBackgroundPush() {
  const capability = pushCapability();
  if (!capability.supported) {
    throw new Error(
      capability.ios && !capability.standalone
        ? "On iPhone, add Notifierr to the Home Screen and open it there before enabling notifications."
        : "Background notifications are not supported in this browser.",
    );
  }
  const config = await getPushConfig();
  if (!config.enabled || !config.vapid_public_key) {
    throw new Error("Background notifications are not configured on the server yet.");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notification permission was not granted.");
  const registration = await activeRegistration();
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(config.vapid_public_key),
    });
  }
  const value = subscription.toJSON();
  if (!value.endpoint || !value.keys?.p256dh || !value.keys?.auth) {
    throw new Error("The browser returned an incomplete push subscription.");
  }
  await createPushSubscription({
    endpoint: value.endpoint,
    keys: value.keys,
    device_label: capability.ios ? "iPhone/iPad Home Screen" : "Browser/PWA",
    user_agent: navigator.userAgent,
  });
  return subscription;
}

export async function disableBackgroundPush() {
  const subscription = await getLocalPushSubscription();
  if (!subscription) return;
  await deletePushSubscription(subscription.endpoint).catch(() => undefined);
  await subscription.unsubscribe();
}

export { getPushConfig, testPushNotification };
