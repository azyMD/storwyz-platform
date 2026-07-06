import calendar
import json
import time
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min

from superchatsync.models import CurrencyMonthlyRate, CustomerOrder


DEFAULT_BASE_URL = "https://api.frankfurter.app"
FIXED_UNITS_PER_EUR = {
    "EUR": Decimal("1"),
    "BGN": Decimal("1.95583"),
}


def month_start(value):
    return date(value.year, value.month, 1)


def month_end(value):
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def add_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def month_range(start, end):
    current = month_start(start)
    end = month_start(end)
    while current <= end:
        yield current
        current = add_month(current)


def decimal_average(values):
    total = sum(values, Decimal("0"))
    return total / Decimal(len(values))


def quantize(value, scale):
    return value.quantize(Decimal(scale), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Fetch monthly average EUR exchange rates for CRM order currencies."

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
        parser.add_argument("--apply", action="store_true", help="Write rates. Without this flag the command is dry-run.")
        parser.add_argument("--date-from", help="Start month/date YYYY-MM-DD. Defaults to oldest CRM order month.")
        parser.add_argument("--date-to", help="End month/date YYYY-MM-DD. Defaults to newest CRM order month.")
        parser.add_argument("--currency", action="append", default=[], help="Currency code. Repeatable. Defaults to order currencies.")
        parser.add_argument("--sleep-seconds", type=float, default=0.15)
        parser.add_argument("--timeout", type=int, default=60)

    def handle(self, *args, **options):
        start, end = self._date_bounds(options)
        currencies = self._currencies(options)
        supported = self._supported_currencies(options["base_url"], options["timeout"])

        fixed = sorted(currency for currency in currencies if currency in FIXED_UNITS_PER_EUR)
        frankfurter = sorted(currency for currency in currencies if currency in supported and currency not in fixed)
        unsupported = sorted(currency for currency in currencies if currency not in fixed and currency not in supported)

        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            f"{mode}: months={start:%Y-%m}..{end:%Y-%m}; "
            f"fixed={fixed}; frankfurter={frankfurter}; unsupported={unsupported}"
        )

        created = 0
        updated = 0
        seen = 0

        for month in month_range(start, end):
            for currency in fixed:
                stats = self._store_rate(
                    currency=currency,
                    month=month,
                    units_per_eur=FIXED_UNITS_PER_EUR[currency],
                    source="fixed_eur_parity" if currency == "EUR" else "fixed_eur_peg",
                    payload={"note": "Fixed rate configured in command."},
                    apply_changes=options["apply"],
                )
                seen += 1
                created += stats["created"]
                updated += stats["updated"]

            if frankfurter:
                month_rates = self._fetch_month_rates(
                    options["base_url"],
                    month,
                    frankfurter,
                    options["timeout"],
                )
                for currency, values in month_rates.items():
                    if not values:
                        continue
                    units_per_eur = decimal_average(values)
                    stats = self._store_rate(
                        currency=currency,
                        month=month,
                        units_per_eur=units_per_eur,
                        source="frankfurter_ecb_monthly_average",
                        payload={
                            "days": len(values),
                            "month": month.isoformat(),
                            "base": "EUR",
                            "currency": currency,
                        },
                        apply_changes=options["apply"],
                    )
                    seen += 1
                    created += stats["created"]
                    updated += stats["updated"]

            if options["sleep_seconds"] > 0:
                time.sleep(options["sleep_seconds"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} complete. rates seen={seen}; created={created}; updated={updated}; "
                f"unsupported={unsupported}"
            )
        )

    def _date_bounds(self, options):
        if options["date_from"] and options["date_to"]:
            start = self._parse_date(options["date_from"])
            end = self._parse_date(options["date_to"])
        else:
            aggregate = CustomerOrder.objects.aggregate(
                min_date=Min("submitted_at"),
                max_date=Max("submitted_at"),
            )
            if not aggregate["min_date"] or not aggregate["max_date"]:
                raise CommandError("No CRM orders available to infer date bounds.")
            start = self._parse_date(options["date_from"]) if options["date_from"] else aggregate["min_date"].date()
            end = self._parse_date(options["date_to"]) if options["date_to"] else aggregate["max_date"].date()
        if end < start:
            raise CommandError("--date-to must be after --date-from.")
        return month_start(start), month_start(end)

    def _currencies(self, options):
        if options["currency"]:
            values = options["currency"]
        else:
            values = CustomerOrder.objects.exclude(currency__isnull=True).exclude(currency="").values_list(
                "currency",
                flat=True,
            ).distinct()
        currencies = sorted({str(value or "").strip().upper() for value in values if str(value or "").strip()})
        return [currency for currency in currencies if currency not in {"UNK", "UNKNOWN"}]

    def _parse_date(self, value):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise CommandError("Dates must use YYYY-MM-DD.")

    def _supported_currencies(self, base_url, timeout):
        url = f"{base_url.rstrip('/')}/currencies"
        with urllib.request.urlopen(self._request(url), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {str(currency).upper() for currency in payload}

    def _fetch_month_rates(self, base_url, month, currencies, timeout):
        end = month_end(month)
        params = urllib.parse.urlencode(
            {
                "from": "EUR",
                "to": ",".join(currencies),
            }
        )
        url = f"{base_url.rstrip('/')}/{month.isoformat()}..{end.isoformat()}?{params}"
        with urllib.request.urlopen(self._request(url), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rates = payload.get("rates") or {}
        values_by_currency = {currency: [] for currency in currencies}
        for day_rates in rates.values():
            for currency in currencies:
                value = day_rates.get(currency)
                if value is not None:
                    values_by_currency[currency].append(Decimal(str(value)))
        return values_by_currency

    def _request(self, url):
        return urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "storwyz-crm-fx-import/1.0",
            },
        )

    def _store_rate(self, currency, month, units_per_eur, source, payload, apply_changes):
        rate_to_eur = Decimal("1") / units_per_eur
        defaults = {
            "units_per_eur": quantize(units_per_eur, "0.00000001"),
            "rate_to_eur": quantize(rate_to_eur, "0.0000000001"),
            "source": source,
            "source_payload": payload,
        }
        if not apply_changes:
            return {"created": 0, "updated": 0}
        obj, created = CurrencyMonthlyRate.objects.update_or_create(
            currency=currency,
            month=month,
            defaults=defaults,
        )
        return {"created": 1 if created else 0, "updated": 0 if created else 1}
