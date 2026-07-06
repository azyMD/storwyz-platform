# Codex Projects Inventory

Last updated: 2026-07-06

This inventory tracks the projects found across local Codex chats and their GitHub status.

## GitHub Repositories

| Chat / Project | Local source | GitHub status | Notes |
| --- | --- | --- | --- |
| Customer profile CRM / Storwyz platform | `2026-06-24/files-mentioned-by-the-user-export/github_work/storwyz-platform` | Uploaded: `azyMD/storwyz-platform` | Main Django CRM + WhatsApp AI platform. |
| Telegram Bot / Referral heatmap | `2026-06-24/files-mentioned-by-the-user-export/github_work/referral-heatmap-bot` | Prepared locally, waiting for GitHub repo | Suggested repo: `azyMD/referral-heatmap-bot`. |
| LiveBid Romania MVP | `2026-06-24/files-mentioned-by-the-user-export/github_work/live-bidding-mvp` | Prepared locally, waiting for GitHub repo | Suggested repo: `azyMD/live-bidding-mvp`. |

## Non-Repo Artifacts Found

| Chat | Local path | Status | Reason |
| --- | --- | --- | --- |
| Add 3 premium wiper claims | `2026-05-28/i-need-3-claims-for-premium/*-assets` | Not uploaded as code repo | Generated creative/media assets, large folders. Use asset storage or Git LFS if needed. |
| Analizeaza ghidul Facebook Business | `2026-06-30/https-business-facebook-com-business-help/outputs` | Not uploaded as code repo | Brochure/output artifacts. Some related source was integrated into `storwyz-platform`. |
| Export logo 1:1 si 4:1 | `2026-05-22/am-nevoie-de-acest-logo-in` | Not uploaded as code repo | Mostly build cache/generated logo work, no standalone app. |
| Genereaza prompt marketing WhatsApp | `2026-06-08/formuleaza-un-prompt-care-o-sa` | Not uploaded as code repo | Prompt/output material, not a software project. |
| Import OpenAI chat | `2026-06-23/how` | Not uploaded as code repo | Support/output material, not a software project. |

## Prepared Local Repos

### `referral-heatmap-bot`

Prepared with:

- `.gitignore`
- `AGENTS.md`
- initial commit: `5b7729e Initial referral heatmap bot`
- tests: `python3 -m unittest` passed, 21 tests

Push blocker:

- GitHub repo `azyMD/referral-heatmap-bot` does not exist yet.
- `gh` cannot create it because local GitHub CLI token is invalid.

### `live-bidding-mvp`

Prepared with:

- `.gitignore`
- `AGENTS.md`
- initial commit: `f21b90f Initial live bidding MVP`
- syntax checks:
  - `node --check server.mjs`
  - `node --check app.js`

Push blocker:

- GitHub repo `azyMD/live-bidding-mvp` does not exist yet.
- `gh` cannot create it because local GitHub CLI token is invalid.

## Recommended Next Step

Create these private repositories on GitHub:

- `azyMD/referral-heatmap-bot`
- `azyMD/live-bidding-mvp`

Then push:

```bash
cd github_work/referral-heatmap-bot
git remote add origin git@github.com:azyMD/referral-heatmap-bot.git
git push -u origin main

cd ../live-bidding-mvp
git remote add origin git@github.com:azyMD/live-bidding-mvp.git
git push -u origin main
```

Alternative:

Run `gh auth login -h github.com`, then Codex can create and push the repos through GitHub CLI.

