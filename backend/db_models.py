from __future__ import annotations

import sqlalchemy as sa


metadata = sa.MetaData()


def _timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    ]


def _shared_marketplace_columns() -> list[sa.Column]:
    return [
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("shipping", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("condition", sa.Text()),
        sa.Column("item_url", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("seller_username", sa.Text()),
        sa.Column("seller_feedback_percentage", sa.Float()),
        sa.Column("seller_feedback_score", sa.Integer()),
        sa.Column("raw_description", sa.Text()),
        sa.Column("item_origin_at", sa.Text()),
        sa.Column("found_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("raw_json", sa.Text()),
        sa.Column("availability_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("buying_option_summary", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("item_end_at", sa.Text()),
        sa.Column("last_availability_checked_at", sa.Text()),
        sa.Column("availability_note", sa.Text(), nullable=False, server_default=sa.text("''")),
    ]


def _user_item_state_columns(*, include_created: bool = True) -> list[sa.Column]:
    columns = [
        sa.Column("score", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model", sa.Text()),
        sa.Column("resale_value", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("resale_low", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("resale_mid", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("resale_high", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("profit_low", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("profit_mid", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("profit_high", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("resale_confidence", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("resale_sample_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("resale_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("resale_source", sa.Text(), nullable=False, server_default=sa.text("'missing'")),
        sa.Column("resale_market_source", sa.Text(), nullable=False, server_default=sa.text("'missing'")),
        sa.Column("resale_condition_used", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("resale_storage_used", sa.Text()),
        sa.Column("storage_resale_warning", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("mint_resale_low", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("mint_resale_mid", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("mint_resale_high", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("mint_profit_low", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("mint_profit_mid", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("mint_profit_high", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("storage_capacity", sa.Text()),
        sa.Column("storage_confidence", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("storage_source", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("estimated_parts_cost", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_parts_cost_available", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("risk_buffer", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_profit", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_profit_available", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("parts_pricing_status", sa.Text(), nullable=False, server_default=sa.text("'fallback'")),
        sa.Column("parts_pricing_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("parts_pricing_label", sa.Text(), nullable=False, server_default=sa.text("'Parts estimate not verified'")),
        sa.Column("pricing_warning", sa.Text(), nullable=False, server_default=sa.text("'Parts estimate not verified'")),
        sa.Column("manual_review_allowed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("whole_phone_confidence_passed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("whole_phone_score", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("has_repair_issue", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manual_review_needed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("manual_review_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("alert_eligible", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("listing_classification_flags", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("user_status", sa.Text(), nullable=False, server_default=sa.text("'new'")),
        sa.Column("user_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("reviewed_at", sa.Text()),
        sa.Column("ignored_at", sa.Text()),
        sa.Column("watched_at", sa.Text()),
        sa.Column("promoted_at", sa.Text()),
        sa.Column("rejected_by_user_at", sa.Text()),
        sa.Column("user_reject_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ignored_reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("ignored_seller", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_by_user_at", sa.Text()),
        sa.Column("hard_reject_flags", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("positive_flags", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("risk_flags", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("alerted_at", sa.Text()),
        sa.Column("promoted_notification_sent_at", sa.Text()),
    ]
    if include_created:
        columns.extend(
            [
                sa.Column("created_at", sa.Text(), nullable=False),
                sa.Column("updated_at", sa.Text(), nullable=False),
            ]
        )
    else:
        columns.append(sa.Column("updated_at", sa.Text(), nullable=False))
    return columns


users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("email", sa.Text(), nullable=False, unique=True),
    sa.Column("password_hash", sa.Text(), nullable=False),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column("account_status", sa.Text(), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("plan_name", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("monthly_price", sa.Float(), nullable=False, server_default=sa.text("0")),
    sa.Column("billing_status", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("paid_until", sa.Text()),
    sa.Column("billing_note", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("updated_at", sa.Text(), nullable=False),
    sa.Column("last_login_at", sa.Text()),
)


user_invites = sa.Table(
    "user_invites",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("code", sa.Text(), nullable=False, unique=True),
    sa.Column("email", sa.Text()),
    sa.Column("role", sa.Text(), nullable=False, server_default=sa.text("'user'")),
    sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("used_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("used_at", sa.Text()),
    sa.Column("expires_at", sa.Text()),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.Column("revoked_at", sa.Text()),
)


user_settings = sa.Table(
    "user_settings",
    metadata,
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("min_score_to_alert", sa.Float(), nullable=False),
    sa.Column("min_profit_to_alert", sa.Float(), nullable=False),
    sa.Column("risky_score_min", sa.Float(), nullable=False),
    sa.Column("risky_score_max", sa.Float(), nullable=False),
    sa.Column("max_alert_item_age_minutes", sa.Integer(), nullable=False),
    sa.Column("max_priority_review_item_age_hours", sa.Integer(), nullable=False),
    sa.Column("max_active_queue_item_age_hours", sa.Integer(), nullable=False),
    sa.Column("default_resale_condition", sa.Text(), nullable=False, server_default=sa.text("'good'")),
    sa.Column("allow_mint_for_alerts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("target_min_model_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("background_poll_enabled", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("background_poll_seconds", sa.Integer(), nullable=False, server_default=sa.text("300")),
    sa.Column("active_start", sa.Text()),
    sa.Column("active_end", sa.Text()),
    sa.Column("timezone", sa.Text(), nullable=False, server_default=sa.text("'America/New_York'")),
    *_timestamp_columns(),
)


user_notification_settings = sa.Table(
    "user_notification_settings",
    metadata,
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("discord_webhook", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("discord_enabled", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("alerts_enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
    sa.Column("notify_best_finds", sa.Integer(), nullable=False, server_default=sa.text("1")),
    sa.Column("notify_priority_review", sa.Integer(), nullable=False, server_default=sa.text("1")),
    *_timestamp_columns(),
)


user_keywords = sa.Table(
    "user_keywords",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("keyword", sa.Text(), nullable=False),
    sa.Column("enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
    sa.Column("is_baseline", sa.Integer(), nullable=False, server_default=sa.text("0")),
    *_timestamp_columns(),
    sa.UniqueConstraint("user_id", "keyword", name="uq_user_keywords_user_keyword"),
)


marketplace_items = sa.Table(
    "marketplace_items",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("marketplace", sa.Text(), nullable=False, server_default=sa.text("'ebay'")),
    sa.Column("marketplace_item_id", sa.Text(), nullable=False),
    *_shared_marketplace_columns(),
    sa.Column("item_creation_at", sa.Text()),
    sa.UniqueConstraint("marketplace", "marketplace_item_id", name="uq_marketplace_items_marketplace_item_id"),
)


user_item_states = sa.Table(
    "user_item_states",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("marketplace_item_id", sa.Integer(), sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False),
    *_user_item_state_columns(include_created=True),
    sa.UniqueConstraint("user_id", "marketplace_item_id", name="uq_user_item_states_user_marketplace_item"),
)


user_ignored_sellers = sa.Table(
    "user_ignored_sellers",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("seller_username", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
    *_timestamp_columns(),
    sa.UniqueConstraint("user_id", "seller_username", name="uq_user_ignored_sellers_user_seller"),
)


user_ignored_keywords = sa.Table(
    "user_ignored_keywords",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("keyword", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
    *_timestamp_columns(),
    sa.UniqueConstraint("user_id", "keyword", name="uq_user_ignored_keywords_user_keyword"),
)


user_repair_value_overrides = sa.Table(
    "user_repair_value_overrides",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("part", sa.Text(), nullable=False),
    sa.Column("cost", sa.Float(), nullable=False),
    sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    *_timestamp_columns(),
    sa.UniqueConstraint("user_id", "model", "part", name="uq_user_repair_value_overrides_lookup"),
)


user_resale_research_overrides = sa.Table(
    "user_resale_research_overrides",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("storage_capacity", sa.Text(), nullable=False),
    sa.Column("condition", sa.Text(), nullable=False),
    sa.Column("low", sa.Float(), nullable=False),
    sa.Column("mid", sa.Float(), nullable=False),
    sa.Column("high", sa.Float(), nullable=False),
    sa.Column("confidence", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("''")),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    *_timestamp_columns(),
    sa.UniqueConstraint(
        "user_id",
        "model",
        "storage_capacity",
        "condition",
        name="uq_user_resale_research_overrides_lookup",
    ),
)


user_item_corrections = sa.Table(
    "user_item_corrections",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("marketplace_item_id", sa.Integer(), sa.ForeignKey("marketplace_items.id", ondelete="CASCADE"), nullable=False),
    sa.Column("corrected_model", sa.Text()),
    sa.Column("corrected_storage_capacity", sa.Text()),
    sa.Column("corrected_issue_type", sa.Text()),
    sa.Column("corrected_part_cost", sa.Float()),
    sa.Column("note", sa.Text(), nullable=False, server_default=sa.text("''")),
    *_timestamp_columns(),
    sa.UniqueConstraint("user_id", "marketplace_item_id", name="uq_user_item_corrections_user_item"),
)


shared_scan_runs = sa.Table(
    "shared_scan_runs",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("mode", sa.Text(), nullable=False, server_default=sa.text("'shared'")),
    sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'running'")),
    sa.Column("triggered_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    sa.Column("active_users", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("unique_searches", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("api_calls_made", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("total_items_returned", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("total_users_evaluated", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("total_items_scored", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("total_alerts_sent", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("started_at", sa.Text(), nullable=False),
    sa.Column("finished_at", sa.Text()),
    *_timestamp_columns(),
)


shared_scan_searches = sa.Table(
    "shared_scan_searches",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("scan_run_id", sa.Integer(), sa.ForeignKey("shared_scan_runs.id", ondelete="CASCADE"), nullable=False),
    sa.Column("search_signature", sa.Text(), nullable=False),
    sa.Column("marketplace", sa.Text(), nullable=False, server_default=sa.text("'ebay'")),
    sa.Column("keyword", sa.Text(), nullable=False),
    sa.Column("limit_value", sa.Integer(), nullable=False),
    sa.Column("sort_order", sa.Text(), nullable=False, server_default=sa.text("'newlyListed'")),
    sa.Column("marketplace_id", sa.Text()),
    sa.Column("subscribed_user_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("items_returned", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("api_calls_made", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("created_at", sa.Text(), nullable=False),
)


shared_scan_results = sa.Table(
    "shared_scan_results",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("scan_search_id", sa.Integer(), sa.ForeignKey("shared_scan_searches.id", ondelete="CASCADE"), nullable=False),
    sa.Column("marketplace", sa.Text(), nullable=False, server_default=sa.text("'ebay'")),
    sa.Column("marketplace_item_id", sa.Text(), nullable=False),
    sa.Column("created_at", sa.Text(), nullable=False),
    sa.UniqueConstraint(
        "scan_search_id",
        "marketplace",
        "marketplace_item_id",
        name="uq_shared_scan_results_search_item",
    ),
)


user_usage_daily = sa.Table(
    "user_usage_daily",
    metadata,
    sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("usage_date", sa.Text(), primary_key=True),
    sa.Column("search_signatures_subscribed", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("items_scored", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("alerts_sent", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("detail_refreshes", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("shared_api_calls_attributed", sa.Float(), nullable=False, server_default=sa.text("0")),
    sa.Column("usage_weight", sa.Float(), nullable=False, server_default=sa.text("0")),
    *_timestamp_columns(),
)


sa.Index("idx_users_role", users.c.role)
sa.Index("idx_users_account_status", users.c.account_status)
sa.Index("idx_user_invites_code", user_invites.c.code)
sa.Index("idx_user_invites_created_by_user_id", user_invites.c.created_by_user_id)
sa.Index("idx_user_invites_used_by_user_id", user_invites.c.used_by_user_id)
sa.Index("idx_user_invites_email", user_invites.c.email)
sa.Index("idx_user_keywords_user_id", user_keywords.c.user_id)
sa.Index("idx_user_keywords_enabled", user_keywords.c.user_id, user_keywords.c.enabled)
sa.Index("idx_user_ignored_sellers_user_id", user_ignored_sellers.c.user_id)
sa.Index("idx_user_ignored_sellers_lookup", user_ignored_sellers.c.user_id, user_ignored_sellers.c.seller_username)
sa.Index("idx_user_ignored_keywords_user_id", user_ignored_keywords.c.user_id)
sa.Index("idx_user_ignored_keywords_lookup", user_ignored_keywords.c.user_id, user_ignored_keywords.c.keyword)
sa.Index("idx_user_repair_value_overrides_user_id", user_repair_value_overrides.c.user_id)
sa.Index(
    "idx_user_repair_value_overrides_lookup",
    user_repair_value_overrides.c.user_id,
    user_repair_value_overrides.c.model,
    user_repair_value_overrides.c.part,
)
sa.Index("idx_user_resale_research_overrides_user_id", user_resale_research_overrides.c.user_id)
sa.Index(
    "idx_user_resale_research_overrides_lookup",
    user_resale_research_overrides.c.user_id,
    user_resale_research_overrides.c.model,
    user_resale_research_overrides.c.storage_capacity,
    user_resale_research_overrides.c.condition,
)
sa.Index("idx_user_item_corrections_user_id", user_item_corrections.c.user_id)
sa.Index("idx_user_item_corrections_marketplace_item_id", user_item_corrections.c.marketplace_item_id)
sa.Index("idx_marketplace_items_item_key", marketplace_items.c.marketplace, marketplace_items.c.marketplace_item_id)
sa.Index("idx_marketplace_items_found_at", marketplace_items.c.found_at)
sa.Index("idx_marketplace_items_seller", marketplace_items.c.seller_username)
sa.Index("idx_user_item_states_user_id", user_item_states.c.user_id)
sa.Index("idx_user_item_states_marketplace_item_id", user_item_states.c.marketplace_item_id)
sa.Index("idx_user_item_states_status", user_item_states.c.user_id, user_item_states.c.status)
sa.Index("idx_user_item_states_user_status", user_item_states.c.user_id, user_item_states.c.user_status)
sa.Index("idx_shared_scan_runs_started_at", shared_scan_runs.c.started_at)
sa.Index("idx_shared_scan_runs_finished_at", shared_scan_runs.c.finished_at)
sa.Index("idx_shared_scan_searches_run_id", shared_scan_searches.c.scan_run_id)
sa.Index("idx_shared_scan_searches_signature", shared_scan_searches.c.search_signature)
sa.Index("idx_shared_scan_results_search_id", shared_scan_results.c.scan_search_id)
sa.Index("idx_user_usage_daily_date", user_usage_daily.c.usage_date)


CORE_TABLES = {
    table.name: table
    for table in (
        users,
        user_invites,
        user_settings,
        user_notification_settings,
        user_keywords,
        marketplace_items,
        user_item_states,
        user_ignored_sellers,
        user_ignored_keywords,
        user_repair_value_overrides,
        user_resale_research_overrides,
        user_item_corrections,
        shared_scan_runs,
        shared_scan_searches,
        shared_scan_results,
        user_usage_daily,
    )
}

SERIAL_ID_TABLES = [
    users.name,
    user_invites.name,
    user_keywords.name,
    marketplace_items.name,
    user_item_states.name,
    user_ignored_sellers.name,
    user_ignored_keywords.name,
    user_repair_value_overrides.name,
    user_resale_research_overrides.name,
    user_item_corrections.name,
    shared_scan_runs.name,
    shared_scan_searches.name,
    shared_scan_results.name,
]
