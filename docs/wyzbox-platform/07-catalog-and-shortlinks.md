# Catalog And Shortlinks

Last updated: 2026-07-20

These are separate functional modules inside `storwyz-platform`, not separate repositories.

## Catalog Module

Purpose:

- Create lightweight public product catalog/brochure pages.
- Upload page/product images through a simple admin UI.
- Serve product/country catalog pages under Storwyz/Catalog routes.
- Catalog brochures no longer enforce a fixed page-count limit; all uploaded valid images are kept.
- Every product brochure stores and displays its product SKU in `manifest.json` and Catalog Admin.
- Catalog Admin includes a POST-only delete action for existing brochures.
- Catalog admin and public viewer UI are in English.
- Supported country codes include `cz` Czechia, `sk` Slovakia, `hr` Croatia and `tr` Turkey.

Main files:

- `superchatsync/catalog_builder.py`
- `config/urls.py`

Routes:

- `/catalog-admin/`
- `/catalog-admin/login/`
- `/catalog-admin/logout/`
- `/catalog-admin/create/`
- `/catalog-admin/delete/<product_slug>/<country_code>/`
- `/catalog/<product_slug>/<country_code>/`
- `/<product_slug>/<country_code>/`

Domain support:

- `catalog.storwyz.com` is included in `ALLOWED_HOSTS`.
- `https://catalog.storwyz.com` is included in `CSRF_TRUSTED_ORIGINS`.

Storage:

- Catalog files are stored under `MEDIA_ROOT/catalog_brochures`.
- Generated media should not be committed to Git.
- Existing brochure SKUs can be populated once from product knowledge on the server with
  `sudo -u nobel /opt/superchat-ai-agent/venv/bin/python manage.py backfill_catalog_product_skus --apply`;
  the command is idempotent,
  skips ambiguous matches and backs up each changed manifest first.

Security notes:

- `CATALOG_ADMIN_USER` and `CATALOG_ADMIN_PASSWORD` are environment variables.
- The GitHub snapshot removed the old hardcoded password fallback.
- If `CATALOG_ADMIN_PASSWORD` is not set, catalog login should not accept any password.
- On 2026-07-10, the live catalog credentials were rotated in `/opt/superchat-ai-agent/.env`; secret values are intentionally not stored in Git.
- A timestamped copy of the previous environment file was written under `/opt/superchat-ai-agent/backups/` before the rotation.
- `superchat-web.service` was restarted and the catalog login redirect plus session flag were verified successfully.

Related chat/work:

- The Facebook Business / catalog work created local brochure outputs and remote `catalog_builder.py` patches.
- Some generated brochure outputs are local artifacts, not source code repositories.

## Shortlink Module

Purpose:

- Generate per-recipient/per-message redirect links.
- Track click attribution for WhatsApp templates and Peeko product/category links.
- Redirect through `storwyz.com/r/<code>/`.
- Optionally send a short thank-you message after click.

Main files:

- `superchatsync/shortlinks.py`
- `superchatsync/views_shortlinks.py`
- `superchatsync/migrations/0010_shortlinks.py`
- `superchatsync/models.py`
- `superchatsync/admin.py`
- `config/urls.py`

Models:

- `ShortLink`
- `ShortLinkClick`

Routes:

- `/r/<code>/` - public redirect and click tracking
- `/shortlinks/` - lightweight dashboard
- Django admin:
  - `/admin/superchatsync/shortlink/`
  - `/admin/superchatsync/shortlinkclick/`

Important behavior:

- Clicks are stored with target metadata, user-agent/IP metadata and conversation/product context where available.
- Peeko templates can use generated shortlink codes/suffixes via Superchat custom attributes.
- Shortlinks solve the "same template sent to many customers" attribution problem.

Environment variables:

- `SHORTLINK_BASE_URL`
- `SHORTLINK_PUBLIC_BASE_URL`
- `SHORTLINK_THANK_YOU_BODY`
- `PEEKO_PRODUCT_TEMPLATE_USES_STORWYZ_SHORTLINK`

Current live counts verified on 2026-07-06:

- Shortlinks: 30
- Shortlink clicks: 15

## Repository Placement

Both modules are included in:

- GitHub: `azyMD/storwyz-platform`
- Local repo: `github_work/storwyz-platform`

They should remain in the main Django repo unless they grow into an independently deployed service.
