import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from superchatsync.models import FitexpressOrderSnapshot


DEFAULT_BASE_URL = "https://analyzer.analyzavr.space/api/fit_exp/orders"


SNAPSHOT_UPDATE_FIELDS = [
    "status_id",
    "product_id",
    "country_id",
    "region_id",
    "product_sku",
    "quantity",
    "quantity_number",
    "cost",
    "shipping_cost",
    "currency_id",
    "payment_type",
    "customer_paid_online",
    "customer_name",
    "customer_location",
    "customer_address",
    "customer_phone",
    "normalized_phone",
    "customer_comment",
    "customer_zipcode",
    "customer_email",
    "customer_age",
    "customer_gender",
    "customer_streetnr",
    "customer_blocknr",
    "customer_appartmentnr",
    "deliver_date",
    "created_at_remote",
    "updated_at_remote",
    "referral",
    "source",
    "curier_id",
    "courier_note",
    "tracking_url",
    "tracking_pdf",
    "approve_method",
    "raw_payload",
    "fetch_params",
    "fetched_at",
    "last_seen_at",
    "updated_at",
]


def clean_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def int_value(value):
    text = clean_value(value)
    if text is None:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def decimal_value(value):
    text = clean_value(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def bool_value(value):
    parsed = int_value(value)
    if parsed is None:
        return None
    return bool(parsed)


def normalized_phone(value):
    text = clean_value(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"+{digits}" if digits else None


def parse_remote_datetime(value):
    text = clean_value(value)
    if not text or text.startswith("0000-00-00"):
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def date_range(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


class Command(BaseCommand):
    help = "Fetch FitExpress orders from analyzer API into a separate snapshot table."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
        parser.add_argument("--id", dest="order_id", help="Fetch one order by API id.")
        parser.add_argument("--date-from", help="Fetch range by created_at date, YYYY-MM-DD.")
        parser.add_argument("--date-to", help="Fetch range end, YYYY-MM-DD. Defaults to --date-from.")
        parser.add_argument("--status", action="append", default=[], help="Optional status filter. Repeatable.")
        parser.add_argument("--country", action="append", default=[], help="Optional country filter. Repeatable.")
        parser.add_argument("--apply", action="store_true", help="Write snapshots. Without this flag the command is dry-run.")
        parser.add_argument("--skip-existing", action="store_true", help="Do not update existing snapshots.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--sleep-seconds", type=float, default=1.5)
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument("--retries", type=int, default=3)
        parser.add_argument("--max-requests", type=int, default=None)

    def handle(self, *args, **options):
        if not options["order_id"] and not options["date_from"]:
            raise CommandError("Use --id or --date-from.")

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        batch_size = max(1, int(options["batch_size"] or 1000))
        requests_done = 0
        total_seen = 0
        total_created = 0
        total_updated = 0
        total_existing = 0

        for params in self._iter_queries(options):
            if options["max_requests"] is not None and requests_done >= options["max_requests"]:
                self.stdout.write(self.style.WARNING("Reached --max-requests; stopping."))
                break

            requests_done += 1
            response = self._fetch_json(options["base_url"], params, options["timeout"], options["retries"])
            orders = response.get("orders") or []
            if not isinstance(orders, list):
                raise CommandError(f"Unexpected API response for params={params}: orders is not a list")

            stats = self._store_orders(
                orders,
                response.get("params") or params,
                batch_size,
                options["apply"],
                options["skip_existing"],
            )
            total_seen += stats["seen"]
            total_created += stats["created"]
            total_updated += stats["updated"]
            total_existing += stats["existing"]

            self.stdout.write(
                f"{mode} request={requests_done} params={params} "
                f"orders={stats['seen']} created={stats['created']} "
                f"updated={stats['updated']} existing={stats['existing']}"
            )

            if options["sleep_seconds"] > 0:
                time.sleep(options["sleep_seconds"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} complete. requests={requests_done}; "
                f"orders seen={total_seen}; created={total_created}; "
                f"updated={total_updated}; existing={total_existing}."
            )
        )

    def _iter_queries(self, options):
        if options["order_id"]:
            yield {"id": options["order_id"]}
            return

        start = self._parse_date(options["date_from"], "--date-from")
        end = self._parse_date(options["date_to"] or options["date_from"], "--date-to")
        if end < start:
            raise CommandError("--date-to must be greater than or equal to --date-from.")

        statuses = options["status"] or [None]
        countries = options["country"] or [None]
        for current in date_range(start, end):
            for status in statuses:
                for country in countries:
                    params = {
                        "datefrom": current.isoformat(),
                        "dateto": current.isoformat(),
                    }
                    if status is not None:
                        params["status"] = str(status)
                    if country is not None:
                        params["country"] = str(country)
                    yield params

    def _parse_date(self, value, name):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise CommandError(f"{name} must be YYYY-MM-DD.")

    def _fetch_json(self, base_url, params, timeout, retries):
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        last_error = None
        for attempt in range(retries + 1):
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "storwyz-crm-fetcher/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = response.read()
                return json.loads(payload.decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(min(30, 2 ** attempt))
        raise CommandError(f"API request failed after {retries + 1} attempts for params={params}: {last_error}")

    def _store_orders(self, orders, fetch_params, batch_size, apply_changes, skip_existing):
        stats = {"seen": 0, "created": 0, "updated": 0, "existing": 0}
        fetched_at = timezone.now()

        by_id = {}
        for row in orders:
            if not isinstance(row, dict):
                continue
            external_id = clean_value(row.get("id"))
            if not external_id:
                continue
            by_id[external_id] = row

        ordered_rows = list(by_id.values())
        stats["seen"] = len(ordered_rows)
        if not ordered_rows:
            return stats

        for batch in chunks(ordered_rows, batch_size):
            external_ids = [clean_value(row.get("id")) for row in batch if clean_value(row.get("id"))]
            existing = {
                snapshot.external_order_id: snapshot
                for snapshot in FitexpressOrderSnapshot.objects.filter(external_order_id__in=external_ids)
            }
            to_create = []
            to_update = []

            for row in batch:
                external_id = clean_value(row.get("id"))
                if not external_id:
                    continue

                snapshot = existing.get(external_id)
                if snapshot is None:
                    snapshot = FitexpressOrderSnapshot(external_order_id=external_id)
                    self._apply_row(snapshot, row, fetch_params, fetched_at)
                    to_create.append(snapshot)
                    stats["created"] += 1
                else:
                    stats["existing"] += 1
                    if skip_existing:
                        continue
                    self._apply_row(snapshot, row, fetch_params, fetched_at)
                    to_update.append(snapshot)
                    stats["updated"] += 1

            if apply_changes:
                with transaction.atomic():
                    if to_create:
                        FitexpressOrderSnapshot.objects.bulk_create(
                            to_create,
                            batch_size=batch_size,
                            ignore_conflicts=True,
                        )
                    if to_update:
                        FitexpressOrderSnapshot.objects.bulk_update(
                            to_update,
                            SNAPSHOT_UPDATE_FIELDS,
                            batch_size=batch_size,
                        )

        return stats

    def _apply_row(self, snapshot, row, fetch_params, fetched_at):
        snapshot.status_id = int_value(row.get("status_id"))
        snapshot.product_id = clean_value(row.get("product_id"))
        snapshot.country_id = int_value(row.get("country_id"))
        snapshot.region_id = int_value(row.get("region_id"))
        snapshot.product_sku = clean_value(row.get("product_sku"))
        snapshot.quantity = clean_value(row.get("quantity"))
        snapshot.quantity_number = int_value(row.get("quantity"))
        snapshot.cost = decimal_value(row.get("cost"))
        snapshot.shipping_cost = decimal_value(row.get("shipping_cost"))
        snapshot.currency_id = int_value(row.get("currency_id"))
        snapshot.payment_type = clean_value(row.get("payment_type"))
        snapshot.customer_paid_online = bool_value(row.get("customer_paid_online"))
        snapshot.customer_name = clean_value(row.get("customer_name"))
        snapshot.customer_location = clean_value(row.get("customer_location"))
        snapshot.customer_address = clean_value(row.get("customer_address"))
        snapshot.customer_phone = clean_value(row.get("customer_phone"))
        snapshot.normalized_phone = normalized_phone(row.get("customer_phone"))
        snapshot.customer_comment = clean_value(row.get("customer_comment"))
        snapshot.customer_zipcode = clean_value(row.get("customer_zipcode"))
        snapshot.customer_email = clean_value(row.get("customer_email"))
        snapshot.customer_age = clean_value(row.get("customer_age"))
        snapshot.customer_gender = clean_value(row.get("customer_gender"))
        snapshot.customer_streetnr = clean_value(row.get("customer_streetnr"))
        snapshot.customer_blocknr = clean_value(row.get("customer_blocknr"))
        snapshot.customer_appartmentnr = clean_value(row.get("customer_appartmentnr"))
        snapshot.deliver_date = clean_value(row.get("deliver_date"))
        snapshot.created_at_remote = parse_remote_datetime(row.get("created_at"))
        snapshot.updated_at_remote = parse_remote_datetime(row.get("updated_at"))
        snapshot.referral = clean_value(row.get("referral"))
        snapshot.source = clean_value(row.get("source"))
        snapshot.curier_id = clean_value(row.get("curier_id"))
        snapshot.courier_note = clean_value(row.get("courier_note"))
        snapshot.tracking_url = clean_value(row.get("tracking_url"))
        snapshot.tracking_pdf = clean_value(row.get("tracking_pdf"))
        snapshot.approve_method = clean_value(row.get("approve_method"))
        snapshot.raw_payload = row
        snapshot.fetch_params = fetch_params
        snapshot.fetched_at = fetched_at
        snapshot.last_seen_at = fetched_at
        snapshot.updated_at = fetched_at
