# Landing Lead API

Last updated: 2026-07-15

## Purpose

Receive leads from public landing pages, persist the original request, forward the canonical order payload to Fitspace and expose delivery status in a protected dashboard.

## Endpoints

- Public receiver: `POST https://storwyz.com/api/landing-leads/`
- CORS preflight: `OPTIONS https://storwyz.com/api/landing-leads/`
- Protected dashboard: `https://storwyz.com/landing-leads/`
- Product/SKU mapping tab: `https://storwyz.com/landing-leads/products/`
- Lead details: `https://storwyz.com/landing-leads/<lead_id>/`

The receiver accepts `application/json` and standard form-encoded POST requests.

## Canonical Payload

```json
{
  "customer_name": "First name Last name",
  "customer_phone": "+37368200969",
  "customer_region": 1044,
  "customer_address": "Full delivery address",
  "quantity": 1,
  "cost": 129,
  "product": "ButchAxe",
  "referral": "Landing",
  "customer_comment": "Landing URL or campaign context"
}
```

Required fields are name, valid phone, region, address, positive quantity, non-negative cost and product name. `referral` defaults to `Landing`; `customer_comment` is optional.

## Processing

1. Store the received payload and request metadata.
2. Normalize the received product name and resolve it by exact Product/SKU mapping.
3. Replace the received product text with the mapped SKU only in the Fitspace payload.
4. Forward synchronously to `LANDING_LEAD_FORWARD_URL`, falling back to `ORDER_WEBHOOK_URL` and then the known Fitspace endpoint.
5. Store the original product text, resolved SKU, HTTP status, response body, external order ID and delivery timestamp.
6. Return `201` for a successful Fitspace delivery, `202` when the lead is stored pending a mapping, `400` for validation failure or `502` for upstream failure.

The database status is one of `received`, `mapping_required`, `sent`, `failed` or `validation_failed`. A lead with `mapping_required` is never forwarded until an active mapping is selected and the operator uses `Send pending` from the mapping tab.

## Product Mapping

`LandingProductMapping` stores one exact normalized product name and its Fitspace SKU. Formatting differences such as spaces, punctuation, accents and letter case are normalized; fuzzy matching is intentionally not used.

Initial mappings are imported from product knowledge and its explicit aliases with:

```bash
python manage.py sync_landing_product_mappings --apply
```

Manual rows can be added or edited in the Product/SKU tab. Existing manual mappings are not overwritten by the seed command.

## Configuration

Secrets stay only in `/opt/superchat-ai-agent/.env`:

- `LANDING_LEADS_DASHBOARD_USER`
- `LANDING_LEADS_DASHBOARD_PASSWORD_HASH`
- `LANDING_LEAD_FORWARD_URL`
- `LANDING_LEAD_FORWARD_TIMEOUT`

The password is stored as Django's salted PBKDF2 hash, not plaintext. The dashboard session expires after 12 hours.

## Main Files

- `superchatsync/landing_leads.py`
- `superchatsync/landing_product_mapping.py`
- `superchatsync/models.py` (`LandingLeadSubmission`)
- `superchatsync/migrations/0013_landing_lead_submissions.py`
- `superchatsync/migrations/0014_landing_product_mappings.py`
- `superchatsync/management/commands/sync_landing_product_mappings.py`
- `config/urls.py`
