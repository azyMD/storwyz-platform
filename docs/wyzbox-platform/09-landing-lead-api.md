# Landing Lead API

Last updated: 2026-07-15

## Purpose

Receive leads from public landing pages, persist the original request, forward the canonical order payload to Fitspace and expose delivery status in a protected dashboard.

## Endpoints

- Public receiver: `POST https://storwyz.com/api/landing-leads/`
- CORS preflight: `OPTIONS https://storwyz.com/api/landing-leads/`
- Protected dashboard: `https://storwyz.com/landing-leads/`
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
  "product": "2757",
  "referral": "Landing",
  "customer_comment": "Landing URL or campaign context"
}
```

Required fields are name, valid phone, region, address, positive quantity, non-negative cost and product SKU. `referral` defaults to `Landing`; `customer_comment` is optional.

## Processing

1. Store the received payload and request metadata.
2. Validate and normalize the Fitspace payload.
3. Forward synchronously to `LANDING_LEAD_FORWARD_URL`, falling back to `ORDER_WEBHOOK_URL` and then the known Fitspace endpoint.
4. Store the HTTP status, response body, external order ID and delivery timestamp.
5. Return `201` for a successful Fitspace delivery, `400` for validation failure or `502` for upstream failure.

The database status is one of `received`, `sent`, `failed` or `validation_failed`.

## Configuration

Secrets stay only in `/opt/superchat-ai-agent/.env`:

- `LANDING_LEADS_DASHBOARD_USER`
- `LANDING_LEADS_DASHBOARD_PASSWORD_HASH`
- `LANDING_LEAD_FORWARD_URL`
- `LANDING_LEAD_FORWARD_TIMEOUT`

The password is stored as Django's salted PBKDF2 hash, not plaintext. The dashboard session expires after 12 hours.

## Main Files

- `superchatsync/landing_leads.py`
- `superchatsync/models.py` (`LandingLeadSubmission`)
- `superchatsync/migrations/0013_landing_lead_submissions.py`
- `config/urls.py`
