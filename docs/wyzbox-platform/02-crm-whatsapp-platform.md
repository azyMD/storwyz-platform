# CRM And WhatsApp AI Platform

Last updated: 2026-07-08

## Purpose

The main platform is a Django CRM and WhatsApp AI communication system. It is intended to manage:

- product knowledge;
- customer profiles;
- customer communication history;
- customer order history;
- conversion events;
- AI assisted WhatsApp conversations;
- handoff between AI and operators.

The main Django app is `superchatsync`.

## Main Models

Current important model groups in `superchatsync.models`:

CRM and identity:

- `CustomerProfile`
- `CustomerChannelIdentity`
- `CustomerCommunicationEvent`
- `CustomerOrder`
- `CustomerOrderPhoneLink`
- `CustomerConversionEvent`
- `CustomerSegment`
- `CustomerSegmentMembership`

Fitexpress/Wyzbox reference/import:

- `FitexpressCountry`
- `FitexpressProductMapping`
- `FitexpressOrderSnapshot`
- `CurrencyMonthlyRate`

WhatsApp/Superchat:

- `Conversation`
- `Message`
- `ConversationAnalysis`
- `AiResponseProcessRun`
- `AiResponseProcessStep`
- `AiDecisionRoadmap`
- `AiLlmCallLog`
- `WhatsappAgentInboxRoute`
- `KnowledgeCenterLink`
- `PeekoWorkspaceLink`

Knowledge and creative assets:

- `ProductKnowledgeImport`
- `ProductKnowledgeItem`
- `ProductCreativeAsset`
- `BusinessClient`
- `BusinessProduct`
- `BusinessProductRanking`
- `BusinessCrawlPage`
- `BusinessKnowledgeItem`
- `BusinessMediaAsset`

Shortlinks:

- `ShortLink`
- `ShortLinkClick`

Catalog:

- `catalog_builder.py` module for public product brochure/catalog pages.
- Catalog routes are Django views inside `superchatsync`, not a separate app.

## CRM Design Decisions

Customer identity:

- Primary unification key is phone number.
- Phones are normalized into a consistent `+digits` style where possible.
- Raw phones must be preserved.
- Do not delete profiles or orders because a phone looks suspicious.
- Multiple phone numbers in one order are preserved through `CustomerOrderPhoneLink`.

Orders:

- Import all orders, including spam, rejected, fishy, not shipped, returned, abandoned and intermediate states.
- Status should keep the raw source status id and/or raw DB status, not only a simplified mapping.
- Approved orders are especially important because their phone numbers are usually reliable.
- Order value should preserve local currency.
- EUR estimate is useful as a separate column/field, not a replacement for local value.

Customer profile page:

- Should be cross-channel CRM, not WhatsApp-only.
- Must show order history with order id, raw status, mapped status, source and channel.
- Must show communication history across WhatsApp, SMS, phone, email, push, web and other channels.
- Filters must support country, product, date, channel, segment, stage, activity and status.
- The list must be paginated and must not load all profiles at once.

## WhatsApp Agent Routing

Routing is now channel/inbox based through `WhatsappAgentInboxRoute`.

Current live routes:

| Route | Channel phone | Agent type | Rule |
| --- | --- | --- | --- |
| ButchAxe WhatsApp Agent | `+15559875079` | `fitexpress_product_agent` | reply only when `handle_status=agent`; ignore `operator` |
| Peeko WhatsApp Agent | `+15559680919` | `peeko_business_agent` | reply to anyone on this channel unless `handle_status=operator` |

The global rule:

- If Superchat custom attribute `handle_status=operator`, AI must ignore.
- For routes with `require_handle_status=True`, AI replies only when `handle_status=agent`.

## ButchAxe/Fitexpress Agent

Initial product focus:

- Product id: `2757`
- Product: `ButchAxe`

Important fixes/behavior:

- Agent should not be hardcoded to ButchAxe long term.
- Product must be identified dynamically.
- Language should follow customer language, with phone country code as initial hint.
- Creative assets should match product and language.
- Repetitive CTA loops were a known problem and were iteratively reduced.
- Order flow should use known profile name/address when available instead of asking again.
- When lead/order webhook is sent successfully, `handle_status` should become `operator`.

## AI Flow Principle

The agent should not be a generic chatbot. It should operate like a structured commerce assistant:

1. capture attention;
2. discovery/interest;
3. desire/value;
4. action/lead;
5. handoff/operator where needed.

CTA buttons should guide the journey. They must match the message content and should shrink in number as the customer moves closer to order.

Known anti-patterns:

- asking for price too early;
- repeating the same CTA set;
- presenting unrelated category choices;
- using template messages without context;
- asking for data already available in CRM;
- looping when the customer asks free text.

## Admin And Debug

Important admin areas:

- Knowledge Center unified workspace
- Customer profiles CRM
- Orders
- Events
- Segments
- AI Roadmap / AI Decision Roadmap
- Product knowledge imports/items
- Product creative assets
- Business clients and business knowledge
- Shortlinks and clicks
- WhatsApp agent inbox routes
- Catalog admin and public catalog pages
- Peeko workspace at `/peeko-admin/`

Knowledge Center is the preferred entry point for day-to-day knowledge work. It combines product document imports, extracted knowledge items, business website knowledge, business products, media assets, conversation-derived suggestions, shortlinks/catalog links and agent route readiness into one admin workspace. The underlying technical tables remain available for audit/debug.

Peeko has a separate Django admin site at `/peeko-admin/`. It uses the same Django auth database, but access is limited to superusers or staff users in the `Peeko Team` / `Peeko Admin` group. The Peeko site registers only Peeko-scoped admin models and filters querysets server-side by `business.slug=peeko`, Peeko shortlink `business_slug`, and Peeko WhatsApp route/conversation metadata. Customer profiles are intentionally not exposed there yet because customer profiles are still global and need an explicit tenant/business mapping before Peeko staff can safely see them.

The admin UI was repeatedly adjusted for mobile usability and performance. Keep future changes lightweight and query-aware because profile/order counts are now in the millions.
