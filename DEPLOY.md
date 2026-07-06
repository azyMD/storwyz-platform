# Deploy Notes

This project was historically edited directly on Wyzbox. The target workflow is GitHub-first.

## Current Live Path

```text
/opt/superchat-ai-agent/web
```

## Services

```bash
sudo -n systemctl restart superchat-web.service superchat-celery-worker.service
systemctl is-active superchat-web.service superchat-celery-worker.service
```

## Pre-Deploy Checks

On Wyzbox:

```bash
cd /opt/superchat-ai-agent/web
/opt/superchat-ai-agent/venv/bin/python manage.py check
/opt/superchat-ai-agent/venv/bin/python manage.py migrate --check
```

## Desired GitHub Workflow

1. Work locally or in Codex on a branch.
2. Open PR.
3. Review diff and migrations.
4. Merge to `main`.
5. Pull/deploy on Wyzbox.
6. Run `manage.py check` and migrations.
7. Restart services.

## First Migration To Git

Because the live server is not currently a Git repo, do not run `git pull` there until the deploy directory is deliberately converted or replaced with a checked-out repo.

Recommended first production-safe setup:

1. Clone this repo to a new server directory, for example `/opt/storwyz-platform-release`.
2. Copy or point to the existing `.env` outside Git.
3. Run checks from the new directory.
4. Switch systemd `WorkingDirectory` only after validation.

