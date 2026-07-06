from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


DEFAULT_LOCK_ID = 72320417


class Command(BaseCommand):
    help = "Daily FitExpress order sync: fetch recent orders, import missing CRM records, and refresh EUR estimates."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag all child commands run dry-run.")
        parser.add_argument("--date-from", help="Fetch range start, YYYY-MM-DD. Defaults to today - lookback_days + 1.")
        parser.add_argument("--date-to", help="Fetch range end, YYYY-MM-DD. Defaults to today.")
        parser.add_argument("--lookback-days", type=int, default=2, help="Rolling window when explicit dates are not provided.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--sleep-seconds", type=float, default=2.0)
        parser.add_argument("--timeout", type=int, default=90)
        parser.add_argument("--retries", type=int, default=3)
        parser.add_argument("--max-requests", type=int, default=None)
        parser.add_argument("--analyze", action="store_true", help="Run ANALYZE after CRM import/backfill.")
        parser.add_argument("--skip-import", action="store_true", help="Only fetch snapshots; do not import to CRM.")
        parser.add_argument("--skip-exchange-rates", action="store_true", help="Do not fetch monthly EUR exchange rates.")
        parser.add_argument("--skip-eur-backfill", action="store_true", help="Do not backfill order EUR estimates.")
        parser.add_argument("--full-eur-backfill", action="store_true", help="Run the full historical EUR backfill instead of the daily window.")
        parser.add_argument("--lock-id", type=int, default=DEFAULT_LOCK_ID)

    def handle(self, *args, **options):
        start, end = self._date_bounds(options)
        mode = "APPLY" if options["apply"] else "DRY-RUN"

        if not self._try_lock(options["lock_id"]):
            raise CommandError("Another sync_fitexpress_daily_orders run is already active.")

        self.stdout.write(
            f"{mode}: FitExpress daily sync date_from={start.isoformat()} "
            f"date_to={end.isoformat()} lookback_days={options['lookback_days']}"
        )

        try:
            self._fetch_snapshots(start, end, options)
            if not options["skip_import"]:
                self._import_snapshots(options)
            if not options["skip_exchange_rates"]:
                self._fetch_exchange_rates(start, end, options)
            if not options["skip_eur_backfill"]:
                self._backfill_eur(start, end, options)
        finally:
            self._unlock(options["lock_id"])

        self.stdout.write(self.style.SUCCESS(f"{mode}: FitExpress daily sync complete."))

    def _date_bounds(self, options):
        today = timezone.localdate()
        if options["date_to"]:
            end = self._parse_date(options["date_to"], "--date-to")
        else:
            end = today

        if options["date_from"]:
            start = self._parse_date(options["date_from"], "--date-from")
        else:
            lookback_days = max(1, int(options["lookback_days"] or 1))
            start = end - timedelta(days=lookback_days - 1)

        if end < start:
            raise CommandError("--date-to must be greater than or equal to --date-from.")
        return start, end

    def _parse_date(self, value, name):
        try:
            return timezone.datetime.fromisoformat(value).date()
        except (TypeError, ValueError):
            raise CommandError(f"{name} must be YYYY-MM-DD.")

    def _fetch_snapshots(self, start, end, options):
        kwargs = {
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "apply": options["apply"],
            "batch_size": options["batch_size"],
            "sleep_seconds": options["sleep_seconds"],
            "timeout": options["timeout"],
            "retries": options["retries"],
            "max_requests": options["max_requests"],
            "verbosity": options.get("verbosity", 1),
        }
        self.stdout.write("step=fetch_fitexpress_orders")
        call_command("fetch_fitexpress_orders", **kwargs)

    def _import_snapshots(self, options):
        self.stdout.write("step=import_fitexpress_snapshots_to_crm_fast")
        call_command(
            "import_fitexpress_snapshots_to_crm_fast",
            apply=options["apply"],
            analyze=options["analyze"] if options["apply"] else False,
            verbosity=options.get("verbosity", 1),
        )

    def _fetch_exchange_rates(self, start, end, options):
        self.stdout.write("step=fetch_monthly_exchange_rates")
        call_command(
            "fetch_monthly_exchange_rates",
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            apply=options["apply"],
            sleep_seconds=0.15,
            timeout=options["timeout"],
            verbosity=options.get("verbosity", 1),
        )

    def _backfill_eur(self, start, end, options):
        self.stdout.write("step=backfill_order_eur_estimates")
        if not options["full_eur_backfill"]:
            updated = self._backfill_eur_window(start, end, options)
            self.stdout.write(f"daily_window_eur_updated={updated}")
            return

        call_command(
            "backfill_order_eur_estimates",
            apply=options["apply"],
            analyze=options["analyze"] if options["apply"] else False,
            verbosity=options.get("verbosity", 1),
        )

    def _backfill_eur_window(self, start, end, options):
        if not options["apply"]:
            sql = """
                SELECT COUNT(*)
                FROM crm_customer_orders o
                JOIN crm_currency_monthly_rates r
                    ON r.currency = upper(o.currency)
                    AND r.month = date_trunc('month', o.submitted_at)::date
                WHERE o.source_channel = 'fitexpress'
                    AND o.cost_eur_estimate IS NULL
                    AND o.submitted_at >= %s
                    AND o.submitted_at < (%s::date + INTERVAL '1 day')
            """
            with connection.cursor() as cursor:
                cursor.execute(sql, [start, end])
                return cursor.fetchone()[0]

        sql = """
            UPDATE crm_customer_orders o
            SET
                cost_eur_estimate = round(o.cost * r.rate_to_eur, 2),
                eur_exchange_rate = r.rate_to_eur,
                exchange_rate_month = r.month,
                exchange_rate_source = r.source,
                updated_at = NOW()
            FROM crm_currency_monthly_rates r
            WHERE o.source_channel = 'fitexpress'
                AND o.cost_eur_estimate IS NULL
                AND r.currency = upper(o.currency)
                AND r.month = date_trunc('month', o.submitted_at)::date
                AND o.submitted_at >= %s
                AND o.submitted_at < (%s::date + INTERVAL '1 day')
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, [start, end])
            return cursor.rowcount

    def _try_lock(self, lock_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            return bool(cursor.fetchone()[0])

    def _unlock(self, lock_id):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
