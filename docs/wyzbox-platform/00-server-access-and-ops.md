# Server, Access And Ops

Last updated: 2026-07-06

## Server Identity

- Hostname: `wyzbox`
- Tailscale IP: `100.97.234.55`
- Tailscale DNS: `wyzbox.tail1a5d40.ts.net`
- Local LAN IP seen on server: `192.168.1.70`
- Public domain: `storwyz.com`

## Main Application

Remote paths:

- App root: `/opt/superchat-ai-agent`
- Django project: `/opt/superchat-ai-agent/web`
- Media/data/logs/backups:
  - `/opt/superchat-ai-agent/media`
  - `/opt/superchat-ai-agent/data`
  - `/opt/superchat-ai-agent/logs`
  - `/opt/superchat-ai-agent/backups`

Main services:

- `superchat-web.service`
- `superchat-celery-worker.service`
- `storwyz-postgres-backup.timer`

Health endpoints:

- `/healthz/` - process liveness.
- `/readyz/` - PostgreSQL and Redis readiness.

Earlier runtime used Gunicorn on:

- `127.0.0.1:8001`

Important Django entry points:

- Admin: `/admin/`
- Superchat webhook: `/superchat/webhook/`
- Shortlink redirect: `/r/<code>/`

## Access Model

Codex access uses SSH key authentication, not a password.

Local key path used by this workspace:

```bash
work/ssh/codex_storwyz_ed25519
```

Typical command from this workspace:

```bash
ssh -i work/ssh/codex_storwyz_ed25519 -o BatchMode=yes codex@wyzbox.tail1a5d40.ts.net
```

For another agent or teammate, do not share a password in chat. Add that person's public SSH key to the server instead.

## Read-Only Health Checks

Useful checks:

```bash
hostname
hostname -I
df -h /
systemctl is-active superchat-web.service superchat-celery-worker.service
cd /opt/superchat-ai-agent/web
/opt/superchat-ai-agent/venv/bin/python manage.py check
```

Verified on 2026-07-06:

```text
hostname: wyzbox
tailscale ip: 100.97.234.55
root filesystem: 1008G total, 920G available
superchat-web.service: active
superchat-celery-worker.service: active
manage.py check: no issues
```

Production hardening and backup verification performed on 2026-07-09 is documented in
`08-production-hardening.md`.

## Deploy Hygiene Used So Far

Before editing remote files:

1. Copy current remote file to a timestamped `.bak_*` file.
2. Copy candidate file to `/tmp/`.
3. Install into the app path with `sudo -n install -m 644`.
4. Run syntax checks and `manage.py check`.
5. Run migrations if needed.
6. Restart `superchat-web.service` and `superchat-celery-worker.service`.
7. Verify service status.

Typical restart:

```bash
sudo -n systemctl restart superchat-web.service superchat-celery-worker.service
systemctl is-active superchat-web.service superchat-celery-worker.service
```

## Security Notes

Known historical issues:

- A server password was pasted in an earlier chat.
- A Cloudflare tunnel token appeared in system output during early audit.

Recommended:

- Rotate any password/token that was ever pasted or displayed.
- Keep `.env` out of documentation.
- Use per-person SSH keys.
- Use least-privilege service users where possible.
