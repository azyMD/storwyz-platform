from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Backfill approximate EUR value on CRM customer orders using monthly exchange rates."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag only reports pending counts.")
        parser.add_argument("--refresh", action="store_true", help="Recompute already populated EUR estimates.")
        parser.add_argument("--analyze", action="store_true", help="Run ANALYZE on crm_customer_orders after apply.")
        parser.add_argument("--progress-every", type=int, default=50)

    def handle(self, *args, **options):
        before = self._summary()
        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            f"{mode}: orders={before['orders_total']}; with_eur={before['with_eur']}; "
            f"missing_eur={before['missing_eur']}; matchable_missing={before['matchable_missing']}; "
            f"unmatchable_missing={before['unmatchable_missing']}"
        )
        if not options["apply"]:
            self._print_missing_by_currency()
            return

        updated = self._update_orders(
            refresh=options["refresh"],
            progress_every=max(1, int(options["progress_every"] or 50)),
        )
        if options["analyze"]:
            self._execute("ANALYZE crm_customer_orders")
        after = self._summary()
        self.stdout.write(
            self.style.SUCCESS(
                f"APPLY complete. updated={updated}; with_eur={after['with_eur']}; "
                f"missing_eur={after['missing_eur']}; unmatchable_missing={after['unmatchable_missing']}"
            )
        )
        self._print_missing_by_currency()

    def _summary(self):
        return self._fetch_one(
            """
            WITH order_rates AS (
                SELECT
                    o.order_id,
                    o.cost_eur_estimate,
                    r.rate_id
                FROM crm_customer_orders o
                LEFT JOIN crm_currency_monthly_rates r
                    ON r.currency = upper(o.currency)
                    AND r.month = date_trunc('month', o.submitted_at)::date
            )
            SELECT
                COUNT(*) AS orders_total,
                COUNT(*) FILTER (WHERE cost_eur_estimate IS NOT NULL) AS with_eur,
                COUNT(*) FILTER (WHERE cost_eur_estimate IS NULL) AS missing_eur,
                COUNT(*) FILTER (WHERE cost_eur_estimate IS NULL AND rate_id IS NOT NULL) AS matchable_missing,
                COUNT(*) FILTER (WHERE cost_eur_estimate IS NULL AND rate_id IS NULL) AS unmatchable_missing
            FROM order_rates
            """
        )

    def _update_orders(self, refresh, progress_every):
        rates = self._fetch_all(
            """
            SELECT currency, month, rate_to_eur, source
            FROM crm_currency_monthly_rates
            ORDER BY month, currency
            """
        )
        total_updated = 0
        for index, rate in enumerate(rates, start=1):
            updated = self._update_rate_month(rate, refresh)
            total_updated += updated
            if index % progress_every == 0 or updated:
                self.stdout.write(
                    f"progress: rates={index}/{len(rates)}; "
                    f"{rate['currency']} {rate['month']:%Y-%m}; updated={updated}; total={total_updated}"
                )
        return total_updated

    def _update_rate_month(self, rate, refresh):
        condition = "TRUE" if refresh else "cost_eur_estimate IS NULL"
        return self._execute(
            """
            UPDATE crm_customer_orders
            SET
                cost_eur_estimate = round(cost * %s, 2),
                eur_exchange_rate = %s,
                exchange_rate_month = %s,
                exchange_rate_source = %s,
                updated_at = NOW()
            WHERE currency = %s
                AND submitted_at >= %s
                AND submitted_at < (%s::date + INTERVAL '1 month')
                AND """ + condition,
            [
                rate["rate_to_eur"],
                rate["rate_to_eur"],
                rate["month"],
                rate["source"],
                rate["currency"],
                rate["month"],
                rate["month"],
            ],
        )

    def _print_missing_by_currency(self):
        rows = self._fetch_all(
            """
            SELECT
                o.currency,
                COUNT(*) AS count,
                MIN(o.submitted_at::date) AS min_date,
                MAX(o.submitted_at::date) AS max_date
            FROM crm_customer_orders o
            WHERE o.cost_eur_estimate IS NULL
            GROUP BY o.currency
            ORDER BY COUNT(*) DESC, o.currency
            LIMIT 30
            """
        )
        if not rows:
            self.stdout.write("missing_by_currency=none")
            return
        self.stdout.write("missing_by_currency:")
        for row in rows:
            self.stdout.write(f"  {row['currency']}: {row['count']} ({row['min_date']}..{row['max_date']})")

    def _execute(self, sql, params=None):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount

    def _fetch_one(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row))

    def _fetch_all(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
