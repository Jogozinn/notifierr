export function notificationStatus(settings) {
  if (!settings) {
    return { label: "Webhook missing", tone: "warning" };
  }
  if (settings.last_delivery_failed) {
    return { label: "Delivery error", tone: "danger" };
  }
  if (settings.notification_ready) {
    return { label: "Notifications ready", tone: "success" };
  }
  if (settings.notification_block_reason === "notifications_disabled") {
    return { label: "Notifications disabled", tone: "muted" };
  }
  if (settings.notification_block_reason === "discord_disabled") {
    return { label: "Discord disabled", tone: "muted" };
  }
  return { label: "Webhook missing", tone: "warning" };
}
