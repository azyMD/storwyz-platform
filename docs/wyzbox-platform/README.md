# Wyzbox Platform Handoff

Last updated: 2026-07-06

This folder is the shared handoff pack for agents working on the Wyzbox / Storwyz server and related projects.

## Scope

Server:

- Hostname: `wyzbox`
- Tailscale IP: `100.97.234.55`
- Tailscale DNS: `wyzbox.tail1a5d40.ts.net`
- Public domain: `storwyz.com`

Main live app:

- Remote path: `/opt/superchat-ai-agent/web`
- Django apps: `productfeed`, `superchatsync`
- Primary services: `superchat-web.service`, `superchat-celery-worker.service`

Separate app on same server:

- Remote path: `/opt/referral-heatmap-bot`
- Service: `referral-heatmap-bot.service`

## Current Verified State

Verified read-only on 2026-07-06:

- `superchat-web.service`: active
- `superchat-celery-worker.service`: active
- `manage.py check`: no issues
- Root filesystem: 1008G total, 46G used, 920G available

Live object counts:

| Area | Count |
| --- | ---: |
| Customer profiles | 2,798,514 |
| Customer orders | 3,724,669 |
| Business clients | 1 |
| Business products | 5,138 |
| Business knowledge items | 15,751 |
| Shortlinks | 30 |
| Shortlink clicks | 15 |

Active WhatsApp agent routes:

| Route | Channel | Agent type | Requires `handle_status=agent` | Active |
| --- | --- | --- | --- | --- |
| ButchAxe WhatsApp Agent | `+15559875079` | `fitexpress_product_agent` | yes | yes |
| Peeko WhatsApp Agent | `+15559680919` | `peeko_business_agent` | no | yes |

## Document Map

- [00-server-access-and-ops.md](00-server-access-and-ops.md) - server, access, services, deploy hygiene.
- [01-chat-history-index.md](01-chat-history-index.md) - history of the Codex chats and source artifacts.
- [02-crm-whatsapp-platform.md](02-crm-whatsapp-platform.md) - main CRM and WhatsApp AI platform.
- [03-fitexpress-wyzbox-data.md](03-fitexpress-wyzbox-data.md) - Wyzbox/Fitexpress imports, orders, products, countries, currencies.
- [04-peeko-business-agent.md](04-peeko-business-agent.md) - Peeko business client, knowledge base, templates, shortlinks.
- [05-referral-heatmap-telegram-bot.md](05-referral-heatmap-telegram-bot.md) - separate Telegram analytics bot.
- [06-todo-risks-decisions.md](06-todo-risks-decisions.md) - next work, risks, open decisions.

## How To Use This Pack

1. Start with this `README.md`.
2. Read the file for the project you are touching.
3. Check `06-todo-risks-decisions.md` before coding.
4. If you change live behavior, update this folder in the same turn.

## Important Rule

Do not paste or store secrets here. No passwords, API tokens, Cloudflare tunnel tokens, Telegram bot tokens, Superchat tokens, or `.env` contents.

