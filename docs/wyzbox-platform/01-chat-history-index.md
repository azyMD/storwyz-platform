# Chat History Index

Last updated: 2026-07-06

This file records the relevant Codex chats and source artifacts for the Wyzbox work.

## Codex Threads Found

### Customer profile CRM

- Thread id: `019efba4-e158-7261-9bff-a119dee99441`
- Local cwd: `/Users/dumitrugodorog/Documents/Codex/2026-06-24/files-mentioned-by-the-user-export`
- Status on scan: active
- Scope: main Storwyz/Wyzbox CRM, WhatsApp AI agents, Fitexpress/Wyzbox imports, Peeko business client, shortlinks, server ops.

High-level history:

1. Initial server access and Cloudflare/Tailscale/domain diagnostics.
2. Audit of existing Django project `/opt/superchat-ai-agent/web`.
3. Rebuild of product knowledge and WhatsApp AI response pipeline.
4. ButchAxe product agent testing with Superchat.
5. Order webhook integration for Fitexpress.
6. CRM/customer profile design and implementation.
7. Large Wyzbox SQL import analysis and CRM import.
8. Daily Fitexpress order sync and reference mappings.
9. Peeko as a separate business client with independent knowledge base.
10. Peeko WhatsApp template strategy, shortlinks and click tracking.
11. Inbox/channel based agent routing.
12. Documentation handoff requested on 2026-07-06.

### Telegram Bot

- Thread id: `019f18cd-ca4f-7411-860f-d615c6a79777`
- Local cwd: `/Users/dumitrugodorog/Documents/Codex/2026-06-30/vre`
- Status on scan: idle
- Scope: separate Telegram bot on the same server, intentionally not coupled to the Customer Profile CRM codebase except by read-only DB access.

High-level history:

1. User asked whether separate Codex chats/projects can work in parallel on same server.
2. Separate project created for referral/product/country heatmap reports.
3. Bot reads CRM order snapshot tables and sends PNG reports in Telegram.
4. Access control and systemd service added.
5. Later discussion about team-style Codex work and shared project context.

## Local Source Artifacts

Main workspace:

- `outputs/server-audit-storwyz-initial.md`
- `outputs/whatsapp-product-knowledge-platform-blueprint.md`
- `reports/wyzbox_crm_import_plan.md`
- `reports/wyzbox_crm_stage_summary.md`
- `imports/analysis/wyzbox_orders_analysis.md`
- `scan_outputs/google_drive_products/product_scan_report.md`
- `scan_outputs/google_drive_products/live_landing_scan/live_landing_scan_report.md`
- `remote_live/superchatsync/`
- `remote_patch/superchatsync/`
- `remote_edit/superchatsync/`
- `work/`

Attached historical chat export:

- `/Users/dumitrugodorog/.codex/attachments/f42dee4c-6a2b-4f9b-a930-78e8103e947f/pasted-text.txt`

Note: the attached `pasted-text.txt` is broad and contains earlier marketing/chat material. It includes some early Wyzbox/Superchat commands and ButchAxe test prompts, but the current implementation history is better represented by the current Codex thread, local reports and code artifacts.

## Important Context From The Chat

User goals evolved from:

- "platforma pentru product knowledge, customer profiles, customer communication history, AI agent WhatsApp"

to:

- multi-business CRM with separate agents per inbox/channel;
- customer profiles unified by phone;
- Wyzbox/Fitexpress order history imported into CRM;
- WhatsApp AI flows controlled by `handle_status`, inbox route, templates, shortlinks and CTA strategy;
- Peeko business client separate from Fitexpress/ButchAxe;
- shared documentation so multiple Codex agents can work independently.

## Working Assumption For Future Agents

The chat is the product spec. When code and chat disagree, do not blindly trust code. Check:

1. Latest user decision in chat.
2. Current live configuration.
3. Admin-visible behavior.
4. Code.

