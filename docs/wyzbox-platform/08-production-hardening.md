# Production Hardening

Last updated: 2026-07-09

## Applied On Live

Application security:

- `DJANGO_DEBUG=False` is explicitly configured.
- Django now requires `DJANGO_SECRET_KEY` outside debug mode.
- A new random production secret was generated without printing it to logs or chat.
- HTTPS redirect, secure session cookies and secure CSRF cookies are enabled.
- HSTS is enabled for one hour without subdomain inclusion or browser preload.
- `/healthz/` reports process liveness without dependency details.
- `/readyz/` checks PostgreSQL and Redis and returns `503` when either dependency is unavailable.

Nginx:

- HTTP requests for Storwyz domains redirect to HTTPS.
- HTTP requests for the Cockpit health hostname redirect to HTTPS.
- Cloudflare's forwarded protocol is preserved for Django.
- Cloudflare client addresses are restored only from the trusted local tunnel process.
- The duplicate catch-all server-name conflict was removed.
- The storage service now has the explicit `storage.storwyz.com` server name.
- Configured TLS protocols are limited to TLS 1.2 and TLS 1.3.

Backups:

- Pre-hardening configuration snapshot: `/var/backups/storwyz/config/pre-hardening-20260709.tar.gz`.
- Pre-hardening PostgreSQL archive: `/var/backups/storwyz/postgres/superchat_agent_20260709.dump`.
- The database archive is custom-format, compressed and validated with `pg_restore --list`.
- `storwyz-postgres-backup.service` creates a custom-format database dump, verifies it, writes a SHA-256 checksum, exports PostgreSQL globals and applies retention.
- `storwyz-postgres-backup.timer` runs daily at 02:30 UTC with up to 20 minutes randomized delay.
- Local retention is 14 days.

Verification performed:

- `manage.py test superchatsync.tests`: passed.
- `manage.py check`: passed.
- `manage.py check --deploy`: only the intentionally deferred HSTS subdomain/preload warnings remain.
- `nginx -t`: passed.
- `superchat-web.service`: active.
- `superchat-celery-worker.service`: active and responds to Celery ping.
- Public `/healthz/`: HTTP 200.
- Public `/readyz/`: HTTP 200 with PostgreSQL and Redis ready.
- Public HTTP admin request redirects once to HTTPS.
- HTTPS admin response sets a secure CSRF cookie and HSTS header.

## Remaining Controlled Cutovers

Webhook authentication:

- `SUPERCHAT_WEBHOOK_SECRET` is currently empty.
- Superchat supports authentication on HTTP request actions, but the live Superchat webhook configuration must be updated at the same time as the server secret.
- Do not enable server-side rejection first or inbound AI events will stop.

Network and internal tools:

- UFW is not active.
- SSH password authentication remains enabled.
- Cockpit is publicly reachable through `health.storwyz.com`.
- The legacy Leads API listens on port 8095 and its create endpoint does not enforce the existing API-key middleware.
- Wyzbox Storage still has credentials in its systemd unit and needs migration to a protected environment file or object storage.

Data protection and recovery:

- Local dumps protect against common application mistakes but not physical server loss.
- Add encrypted off-site copies and PostgreSQL WAL archiving for point-in-time recovery.
- Perform a complete restore drill on the DR server.
- Separate private knowledge documents from publicly served creative/catalog media.
