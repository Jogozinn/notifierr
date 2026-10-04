import assert from "node:assert/strict";

import { notificationStatus } from "../src/notificationStatus.js";

assert.equal(notificationStatus({ notification_ready: true }).label, "Notifications ready");
assert.equal(notificationStatus({ notification_block_reason: "notifications_disabled" }).label, "Notifications disabled");
assert.equal(notificationStatus({ notification_block_reason: "discord_disabled" }).label, "Discord disabled");
assert.equal(notificationStatus({ notification_block_reason: "webhook_missing" }).label, "Webhook missing");
assert.equal(notificationStatus({ notification_ready: true, last_delivery_failed: true }).label, "Delivery error");

console.log("notification status tests passed");
