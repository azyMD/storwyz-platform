# TODO, Risks And Open Decisions

Last updated: 2026-07-06

## Immediate TODO

Documentation/process:

- Keep this folder updated after every meaningful live change.
- Add a short `AGENTS.md` or project context pointer if multiple agents will work in parallel.
- Decide whether this handoff folder should be copied into a Git repo or stay as workspace docs.

WhatsApp routing:

- Continue using `WhatsappAgentInboxRoute` as the source of truth for which agent owns which channel.
- Confirm all live Superchat channel ids and inbox ids after any Superchat configuration change.
- Keep `handle_status=operator` as a hard stop for AI.

Peeko:

- Improve discovery flow before templates.
- Avoid repeated same response.
- Use list CTA only when the customer explicitly asks for a category with many sub-options.
- Use product templates only when a product recommendation is contextually useful.
- Track link clicks through shortlinks and decide final handoff/no-reply behavior after more tests.
- Finalize exact template URL base/extension settings in Superchat.

Fitexpress/ButchAxe:

- Remove remaining product hardcoding where possible.
- Ensure language is detected from conversation and seeded from country phone code.
- Ensure localized creative assets are selected by product and language.
- Confirm order flow uses existing CRM name/address and asks only for missing/correction data.
- Keep webhook idempotency: no duplicate order submit for same product/customer in 24h.

CRM:

- Continue performance tuning admin pages. Profile and order tables are multi-million row.
- Avoid expensive filters that load full profile/order lists.
- Make product and country filters display names, not only ids.
- Improve date filters and saved segment/list workflows.
- Add stronger customer profile detail page for all channels, not WhatsApp-centric.

Data import:

- Verify daily Fitexpress sync timer is installed and enabled on live server.
- Reconcile order counts between Wyzbox dump, Fitexpress snapshots and CRM orders.
- Keep raw status ids visible.
- Maintain monthly EUR estimate fields separately from local currency.

Telegram bot:

- Check live service status when working on analytics bot.
- Keep it read-only.
- Avoid coupling it to WhatsApp AI runtime.

## Risks

Secrets:

- Some credentials were exposed historically. Rotate any password/token that may have appeared in chat or logs.

Scale:

- CRM is already around 2.8M profiles and 3.7M orders.
- Django admin changes can become very slow if they use unbounded counts, subqueries or full table scans.

AI behavior:

- Template-only behavior creates poor UX.
- CTA mismatch causes loops and wrong journey direction.
- Generic recovery can be useful, but only if it changes direction and avoids the previous CTA.

Data quality:

- Wyzbox order statuses are mixed and need raw preservation.
- Phone numbers can include multiple numbers or messy values.
- Addresses exist for many customers, but may be old or incomplete.
- Product ids and SKU must be carefully mapped to product names.

Integrations:

- Superchat template capabilities are limited: quick reply buttons cannot carry arbitrary hidden links like URL buttons.
- Dynamic URL templates require correct variable/custom attribute setup.
- The same template sent to many customers needs per-recipient shortlink if click attribution matters.

## Open Decisions

1. Where should shared docs live long term?
   - Current: `shared_handoff/wyzbox-platform/` in local workspace.
   - Better long term: versioned project docs in repo, with no secrets.

2. How strict should Peeko handoff be after link click?
   - Option A: stop AI immediately after click.
   - Option B: send one thank-you and stop.
   - Option C: continue only if customer writes again.

3. Should Peeko use a full e-commerce/cart concept in WhatsApp?
   - Current decision: no, keep simple and drive users to site.
   - Revisit only after website click/conversion tracking is reliable.

4. How many specialized agents should exist?
   - Current direction: separate by business/inbox/function.
   - Shared source of truth: same CRM/DB.
   - Avoid separate DBs unless there is a strong operational reason.

5. How should customer status/scoring work?
   - User deferred scoring.
   - For now: preserve raw history and statuses, identify bad buyers later.

## Last Known Good Live State

On 2026-07-06:

- `superchat-web.service`: active
- `superchat-celery-worker.service`: active
- `manage.py check`: passed
- Root space: 920G available
- Customer profiles: 2,798,514
- Customer orders: 3,724,669
- Peeko route: active on `+15559680919`
- ButchAxe route: active on `+15559875079`

