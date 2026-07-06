# Storwyz / Wyzbox Platform

Django CRM and WhatsApp AI platform deployed on the Wyzbox server.

## Live Environment

- Server: `wyzbox`
- Tailscale IP: `100.97.234.55`
- Domain: `storwyz.com`
- Remote app path: `/opt/superchat-ai-agent/web`
- Main services: `superchat-web.service`, `superchat-celery-worker.service`

## Main Apps

- `productfeed` - product catalog, offers, FAQ, sales rules and assets.
- `superchatsync` - Superchat sync, CRM, AI agent runtime, product/business knowledge, shortlinks and admin UI.

## Documentation

Start here:

- [docs/wyzbox-platform/README.md](docs/wyzbox-platform/README.md)

That folder contains the current handoff pack with context, history, TODOs, risks and project-specific notes.

## Local Checks

From the project root:

```bash
python manage.py check
python manage.py test
```

On the server, use the virtualenv:

```bash
/opt/superchat-ai-agent/venv/bin/python manage.py check
```

## Repository Rules

- Do not commit `.env`, keys, SQL dumps, SQLite files, media, logs or backups.
- Keep live deploy notes in `docs/wyzbox-platform/`.
- Treat GitHub as source of truth for code and docs.
- Treat Wyzbox as deploy target and production data host.

