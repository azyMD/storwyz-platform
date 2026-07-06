# Fitexpress / Wyzbox Data

Last updated: 2026-07-06

## Purpose

This part of the system imports and normalizes customer/order data from Wyzbox and Fitexpress into the CRM.

It supports:

- historical Wyzbox SQL dump import;
- Fitexpress order snapshot API import;
- customer profile creation by phone;
- order history on each profile;
- product/country reference mappings;
- daily new order sync;
- currency and EUR estimate support.

## Historical SQL Dump

Source:

- `imports/wyzbox_customers_dump.sql`

Local analysis:

- `imports/analysis/wyzbox_orders_analysis.md`
- `reports/wyzbox_crm_import_plan.md`
- `reports/wyzbox_crm_stage_summary.md`

Important aggregate results:

| Metric | Value |
| --- | ---: |
| Rows parsed | 3,176,790 |
| Date range | 2013-12-26 to 2026-06-29 |
| Unique normalized phones | 2,405,852 |
| Rows with phone | 99.33% |
| Rows with address | 65.72% |
| Rows with referral | 85.82% |

Stage database:

- `imports/analysis/wyzbox_crm_stage.sqlite`

Stage result:

| Metric | Count |
| --- | ---: |
| Orders imported into staging | 3,176,790 |
| Phone-based profiles | 2,384,389 |
| Order-phone links | 3,167,625 |
| Orders without normalized phone | 9,858 |
| Orders with multiple normalized phones | 658 |

## Import Rules

Rules agreed in chat:

- Import all orders.
- Do not remove spam/fishy/rejected/abandoned records.
- Keep raw phone values.
- Normalize phone only for matching/unification.
- Link one order to all detected phone numbers if multiple are present.
- Use phone as the unique customer profile identifier.
- Preserve source status id and raw DB status.
- Keep local cost/currency and add EUR estimate separately.
- Do not implement phone trust/scoring yet.

## Fitexpress API

API endpoint discussed:

- `https://analyzer.analyzavr.space/api/fit_exp/orders`

Supported modes:

- order by id: `?id=fs_...`
- order array by date/status/country:
  - `datefrom` required
  - `dateto` optional
  - `status` optional
  - `country` optional

Important source fields from API:

- `id`
- `status_id`
- `product_id`
- `country_id`
- `region_id`
- `product_sku`
- `quantity`
- `cost`
- `customer_name`
- `customer_address`
- `customer_phone`
- `customer_comment`
- `created_at`
- `updated_at`
- `referral`
- `shipping_cost`
- `currency_id`
- `payment_type`
- `customer_paid_online`

## Product And Country Mapping

Country mapping was provided by user and imported into reference tables. Examples:

- `1001` Romania
- `1044` Moldova
- `1081` Bulgaria
- `1109` Czech
- `1186` Poland
- `1300` Slovakia
- `1400` Ukraine
- `1500` Hungary
- `1700` Germany
- `1800` Turkey
- `1801` Italy
- `1802` Spain

Product mapping source:

- user-provided large product id list;
- Google Drive scan outputs;
- Fitexpress reference tables.

Important note:

- UI should display product names, not only SKU/product ids.
- Product ids still matter for API/webhook matching.

## Daily Sync

Files found:

- `systemd_units/superchat-fitexpress-daily-sync.service`
- `systemd_units/superchat-fitexpress-daily-sync.timer`
- `remote_edit/superchatsync/management/commands/sync_fitexpress_daily_orders.py`

Intent:

- pull new Fitexpress orders every 24h;
- import separately first;
- then add/update CRM profiles and order history;
- avoid overloading upstream server.

## Currency

User requested:

- local currency must remain correct per country/order;
- remove misleading approximate markers from UI;
- add approximate EUR equivalent using average exchange rate for the month of the order.

Relevant model/commands:

- `CurrencyMonthlyRate`
- `fetch_monthly_exchange_rates.py`
- `backfill_order_eur_estimates.py`

## Current Live Counts

Read-only live counts on 2026-07-06:

- Customer profiles: 2,798,514
- Customer orders: 3,724,669

These counts are larger than the first SQL dump because later Fitexpress/API imports added orders.

