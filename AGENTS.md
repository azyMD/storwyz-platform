# Agent Instructions

This repository is the source of truth for the Storwyz/Wyzbox Django platform.

## Before Editing

1. Read `docs/wyzbox-platform/README.md`.
2. Read the project-specific document for the area you are touching.
3. Check `docs/wyzbox-platform/06-todo-risks-decisions.md`.
4. Never rely on chat memory alone for live state.

## Safety

- Never commit secrets, `.env`, API keys, SSH keys, SQL dumps, SQLite databases, media, logs or backups.
- Keep customer PII out of docs and commits unless explicitly required and sanitized.
- Do not run destructive server commands unless explicitly requested.
- Before changing live server code, back up the remote file or deploy through Git once that flow is established.

## Workflow

- Use branches for changes.
- Keep commits small and named by feature/fix.
- Update docs when behavior, routes, services, templates or data flows change.
- For review requests, lead with bugs/risks and file references.

## Live Server

- Host: `wyzbox.tail1a5d40.ts.net`
- App path: `/opt/superchat-ai-agent/web`
- Services: `superchat-web.service`, `superchat-celery-worker.service`
- Main command: `/opt/superchat-ai-agent/venv/bin/python manage.py check`

## Current Architecture Notes

- CRM identity is phone-first.
- `handle_status=operator` is a hard stop for WhatsApp AI.
- WhatsApp agent ownership is routed through `WhatsappAgentInboxRoute`.
- Peeko and Fitexpress/ButchAxe are separate business/agent flows.
- Shortlinks are used for click attribution.

