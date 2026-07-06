# Peeko Business Agent

Last updated: 2026-07-06

## Purpose

Peeko is a separate business client on the same platform. It should not share the ButchAxe/Fitexpress sales flow.

Peeko goal:

- communicate in English;
- use Peeko website knowledge and product catalog;
- send users to the Peeko website;
- use WhatsApp templates with links;
- use shortlinks to track clicks;
- after click or likely handoff, avoid unnecessary continued AI conversation.

Business client:

- Slug: `peeko`
- Website scanned: `https://peeko.co.uk/`

## Knowledge Base

Peeko knowledge was created as a separate business knowledge base:

- `BusinessClient`
- `BusinessProduct`
- `BusinessProductRanking`
- `BusinessCrawlPage`
- `BusinessKnowledgeItem`
- `BusinessMediaAsset`

Live counts on 2026-07-06:

- Business clients: 1
- Business products: 5,138
- Business knowledge items: 15,751

Website crawl and product media extraction were added through management commands:

- `crawl_business_website.py`
- `download_business_media_assets.py`
- `import_business_sitemap_taxonomy.py`
- `refresh_peeko_trending_rankings.py`

## Agent Routing

Current live route:

- Route name: `Peeko WhatsApp Agent`
- Channel phone: `+15559680919`
- Agent type: `peeko_business_agent`
- `require_handle_status`: false
- Active: true

Behavior:

- Peeko agent can respond to anyone on its dedicated channel.
- If `handle_status=operator`, it must ignore.

## WhatsApp Templates

Current mapped templates:

| Internal use | Superchat template name | Purpose |
| --- | --- | --- |
| browse choice | `peeko_browse_choice` | send Bestsellers/New Deals links and an auxiliary help option |
| bestsellers link | `peeko_bestsellers_shortlink` | send a bestsellers/trending collection link |
| category select | `peeko_category_select` | send Groceries/Beauty links and a more-options option |
| product link | `peeko_product_shortlink` | send one product recommendation with product page link |

Custom attributes used:

- `handle_status`
- `peeko_product_name`
- `peeko_product_price`
- `peeko_product_hook`
- `peeko_product_url_suffix`
- `peeko_product_shortlink_code`

Important link detail:

- Dynamic URL templates need per-recipient shortlink code/suffix.
- Static templates can still use generated shortlinks through template variables.
- Shortlink target should include only minimal UTM, usually `utm_source=whatsapp`.

## Shortlink Tracking

Shortlink service:

- public route: `https://storwyz.com/r/<code>/`
- models: `ShortLink`, `ShortLinkClick`

When a user clicks:

- click is logged;
- target redirects to Peeko product/category page;
- system can send a short thank-you message once.

Live counts on 2026-07-06:

- Shortlinks: 30
- Shortlink clicks: 15

## Marketing Flow Direction

User was unhappy with robotic template-only flow. Current strategic direction:

1. Start conversationally.
2. Offer simple choices: Bestsellers, New Deals, Help me choose.
3. If the user chooses a category, develop that category with relevant sub-options.
4. Use product templates as inventory/link tools only when useful.
5. For undecided users, recommend products from trending with highest discounts.
6. Avoid repeated same messages or irrelevant category jumps.
7. If a link is clicked or presumed clicked after timeout, stop pushing AI and move toward operator/no further action depending on test mode.

Known anti-patterns from tests:

- sending Beauty when user chose Groceries/Snacks;
- repeating the same "snacks" message;
- sending template before discovery;
- treating templates as higher priority than logic;
- CTA mismatch: message says one thing, buttons lead elsewhere.

## Current Test Notes

Hardcoded test numbers were used earlier:

- `+37368200969`
- `+447896887292`

Current routing should now be by Peeko inbox/channel, not only these test phones.

