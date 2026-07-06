# Referral Heatmap Telegram Bot

Last updated: 2026-07-06

## Purpose

This is a separate Telegram bot project on the same server. It is intentionally not part of the WhatsApp AI agent codebase.

It reads CRM/Fitexpress order snapshot tables and sends PNG analytics reports in Telegram.

Local project path:

- `/Users/dumitrugodorog/Documents/Codex/2026-06-30/vre/referral-heatmap-bot`

Remote path:

- `/opt/referral-heatmap-bot`

Service:

- `referral-heatmap-bot.service`

Systemd config:

```ini
[Service]
User=nobel
Group=nobel
WorkingDirectory=/opt/referral-heatmap-bot
Environment="REFERRAL_HEATMAP_ENV_FILE=/opt/referral-heatmap-bot/.env"
ExecStart=/opt/superchat-ai-agent/venv/bin/python -m referral_heatmap_bot
Restart=always
RestartSec=5
```

## Features

### Referral Heatmap

Flow:

1. Open Telegram bot menu.
2. Choose `Referral Heatmap`.
3. Choose benchmark period: `2 weeks` or `4 weeks`.
4. Choose a referral with more than `MIN_LAST_WEEK_ORDERS` orders in the last 7 complete days.
5. Receive PNG heatmap.

Referral grouping:

- `lw_1651_4140` and `lw_1651_4325` are grouped as `lw_1651`.
- Anything after the second underscore is ignored.

Date logic:

- Timezone: `Europe/Madrid` by default.
- Current day excluded.
- Last 7 days ends at local midnight today.
- Benchmark period is either 14 or 28 days before that.

### Daily Leads

Flow:

1. Open Telegram bot menu.
2. Choose `Daily Leads`, or run `/daily_leads`.
3. Choose a recent date, or run `/daily_leads YYYY-MM-DD`.
4. Receive PNG table.

## Tables Read

The bot reads:

- `fitexpress_order_snapshots`
- `fitexpress_countries`
- `fitexpress_product_mappings`
- `products`

It should remain read-only against the CRM database.

## Access Control

Access is restricted by default.

Config examples:

- `TELEGRAM_REQUIRE_ACCESS=true`
- `TELEGRAM_ADMIN_USERNAMES=balmornow`
- `ACCESS_STATE_FILE=/opt/referral-heatmap-bot/access.json`

Admin commands:

- `/access`
- `/allow @username`
- `/revoke @username`

## Relationship To Main Platform

Shared:

- Same server.
- Same Python venv path in service.
- Reads CRM/Fitexpress DB tables.

Separate:

- Separate source directory.
- Separate systemd service.
- No WhatsApp/Superchat sending.
- No customer communication logic.

