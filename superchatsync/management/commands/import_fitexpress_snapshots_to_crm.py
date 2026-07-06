import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from superchatsync.models import (
    CustomerChannelIdentity,
    CustomerOrder,
    CustomerOrderPhoneLink,
    CustomerProfile,
    FitexpressOrderSnapshot,
)


CUSTOMER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://storwyz.com/crm/customer-profile")

CURRENCY_BY_COUNTRY_ID = {
    1001: "RON",
    1044: "MDL",
    1081: "BGN",
    1109: "CZK",
    1186: "PLN",
    1300: "EUR",
    1400: "UAH",
    1500: "HUF",
    1600: "GBP",
    1700: "EUR",
    1800: "TRY",
    1801: "EUR",
    1802: "EUR",
    1803: "RSD",
    1804: "VND",
    1805: "SGD",
    1806: "MXN",
    1807: "IDR",
    1808: "EUR",
    1809: "VND",
    1811: "EUR",
    1812: "EUR",
    1813: "EUR",
    1814: "EUR",
    1815: "EUR",
    1816: "EGP",
    1817: "NGN",
    1818: "KES",
    1819: "SAR",
    1820: "USD",
    1821: "EUR",
    1822: "EUR",
    1823: "EUR",
    1824: "CHF",
    1825: "COP",
}

CURRENCY_BY_CURRENCY_ID = {
    2: "CZK",
    3: "BGN",
    4: "TRY",
    5: "EUR",
    6: "RON",
    10: "PLN",
    12: "MXN",
}


def clean_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def profile_key_for_phone(phone):
    phone = clean_value(phone)
    return f"phone:{phone}" if phone else None


def customer_id_for_profile_key(profile_key):
    return uuid.uuid5(CUSTOMER_NAMESPACE, profile_key)


def decimal_or_zero(value):
    if value is None:
        return Decimal("0.00")
    return value


def int_or_one(value):
    return value if value and value > 0 else 1


def currency_for_snapshot(snapshot):
    if snapshot.country_id in CURRENCY_BY_COUNTRY_ID:
        return CURRENCY_BY_COUNTRY_ID[snapshot.country_id]
    if snapshot.currency_id in CURRENCY_BY_CURRENCY_ID:
        return CURRENCY_BY_CURRENCY_ID[snapshot.currency_id]
    return "UNKNOWN"


def order_datetime(snapshot):
    return snapshot.created_at_remote or snapshot.updated_at_remote or timezone.now()


def parse_metadata_datetime(value):
    text = clean_value(value)
    if not text:
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def chunks(iterator, size):
    batch = []
    for item in iterator:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class Command(BaseCommand):
    help = "Import FitExpress order snapshots into CRM customer profiles and order history."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag the command is dry-run.")
        parser.add_argument("--limit", type=int, default=None, help="Limit processed snapshots for a safe test run.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--date-from", help="Filter snapshot created_at_remote date from YYYY-MM-DD.")
        parser.add_argument("--date-to", help="Filter snapshot created_at_remote date to YYYY-MM-DD.")
        parser.add_argument("--country", action="append", default=[], help="Filter by country_id. Repeatable.")
        parser.add_argument("--status", action="append", default=[], help="Filter by status_id. Repeatable.")
        parser.add_argument(
            "--skip-profile-updates",
            action="store_true",
            help="Create missing profiles but do not enrich existing profiles.",
        )
        parser.add_argument(
            "--update-existing-orders",
            action="store_true",
            help="Refresh CRM orders that were already imported from FitExpress snapshots.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        batch_size = max(1, int(options["batch_size"] or 1000))
        limit = options["limit"]
        update_profiles = not options["skip_profile_updates"]

        queryset = FitexpressOrderSnapshot.objects.all().order_by("created_at_remote", "external_order_id")
        if options["date_from"]:
            queryset = queryset.filter(created_at_remote__date__gte=options["date_from"])
        if options["date_to"]:
            queryset = queryset.filter(created_at_remote__date__lte=options["date_to"])
        if options["country"]:
            queryset = queryset.filter(country_id__in=[int(value) for value in options["country"]])
        if options["status"]:
            queryset = queryset.filter(status_id__in=[int(value) for value in options["status"]])
        if limit:
            queryset = queryset[:limit]

        stats = {
            "snapshots": 0,
            "profiles_create": 0,
            "profiles_existing": 0,
            "profiles_update": 0,
            "identities_create": 0,
            "identities_existing": 0,
            "orders_create": 0,
            "orders_existing": 0,
            "orders_update": 0,
            "links_create": 0,
            "links_existing": 0,
            "orders_without_phone": 0,
        }
        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(
            f"{mode}: snapshots={queryset.count()}; limit={limit or 'none'}; "
            f"batch_size={batch_size}; update_profiles={update_profiles}; "
            f"update_existing_orders={options['update_existing_orders']}"
        )

        for batch in chunks(queryset.iterator(chunk_size=batch_size), batch_size):
            self._process_batch(batch, batch_size, apply_changes, update_profiles, options, stats)
            if stats["snapshots"] % (batch_size * 20) == 0:
                self.stdout.write(
                    "progress: "
                    f"snapshots={stats['snapshots']}, "
                    f"profiles_create={stats['profiles_create']}, "
                    f"profiles_update={stats['profiles_update']}, "
                    f"orders_create={stats['orders_create']}, "
                    f"orders_existing={stats['orders_existing']}, "
                    f"links_create={stats['links_create']}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} complete. "
                f"snapshots={stats['snapshots']}; "
                f"profiles create={stats['profiles_create']}, existing={stats['profiles_existing']}, "
                f"update={stats['profiles_update']}; "
                f"identities create={stats['identities_create']}, existing={stats['identities_existing']}; "
                f"orders create={stats['orders_create']}, existing={stats['orders_existing']}, "
                f"update={stats['orders_update']}, without_phone={stats['orders_without_phone']}; "
                f"links create={stats['links_create']}, existing={stats['links_existing']}."
            )
        )

    def _process_batch(self, snapshots, batch_size, apply_changes, update_profiles, options, stats):
        now = timezone.now()
        stats["snapshots"] += len(snapshots)

        phones = sorted({snapshot.normalized_phone for snapshot in snapshots if clean_value(snapshot.normalized_phone)})
        profile_keys = [profile_key_for_phone(phone) for phone in phones]
        profiles_by_key = {
            profile.profile_key: profile
            for profile in CustomerProfile.objects.filter(profile_key__in=profile_keys)
        }
        identities_by_phone = {
            identity.normalized_identifier: identity
            for identity in CustomerChannelIdentity.objects.filter(
                channel="phone",
                normalized_identifier__in=phones,
            )
        }

        profiles_to_create = []
        profiles_to_update = []
        identities_to_create = []
        seen_new_profiles = set()
        seen_new_identities = set()

        for snapshot in snapshots:
            phone = clean_value(snapshot.normalized_phone)
            if not phone:
                continue
            profile_key = profile_key_for_phone(phone)
            profile = profiles_by_key.get(profile_key)
            if profile is None and profile_key not in seen_new_profiles:
                profile = self._build_profile(snapshot, profile_key, now)
                profiles_by_key[profile_key] = profile
                profiles_to_create.append(profile)
                seen_new_profiles.add(profile_key)
                stats["profiles_create"] += 1
            elif profile is not None:
                stats["profiles_existing"] += 1
                if update_profiles and self._merge_profile(profile, snapshot, now):
                    profiles_to_update.append(profile)
                    stats["profiles_update"] += 1

            if phone in identities_by_phone or phone in seen_new_identities:
                stats["identities_existing"] += 1
                continue

            profile = profiles_by_key.get(profile_key)
            if profile is None:
                continue
            identities_to_create.append(
                CustomerChannelIdentity(
                    customer_id=profile.customer_id,
                    channel="phone",
                    identifier=phone,
                    normalized_identifier=phone,
                    provider="fitexpress",
                    is_primary=True,
                    status="active",
                    first_seen_at=order_datetime(snapshot),
                    last_seen_at=snapshot.updated_at_remote or order_datetime(snapshot),
                    metadata={"source": "fitexpress_import", "profile_key": profile_key},
                )
            )
            seen_new_identities.add(phone)
            stats["identities_create"] += 1

        idempotency_keys = [self._idempotency_key(snapshot) for snapshot in snapshots]
        existing_orders = {
            order.idempotency_key: order
            for order in CustomerOrder.objects.filter(idempotency_key__in=idempotency_keys)
        }
        orders_to_create = []
        orders_to_update = []
        orders_by_key = dict(existing_orders)

        for snapshot in snapshots:
            key = self._idempotency_key(snapshot)
            order = existing_orders.get(key)
            if order is None:
                order = self._build_order(snapshot, profiles_by_key, now)
                orders_to_create.append(order)
                orders_by_key[key] = order
                stats["orders_create"] += 1
                if not clean_value(snapshot.normalized_phone):
                    stats["orders_without_phone"] += 1
                continue

            stats["orders_existing"] += 1
            if options["update_existing_orders"]:
                self._apply_order_snapshot(order, snapshot, profiles_by_key, now)
                orders_to_update.append(order)
                stats["orders_update"] += 1

        order_ids = [order.order_id for order in orders_by_key.values() if order.order_id]
        existing_pairs = set()
        if order_ids and phones:
            existing_pairs = set(
                CustomerOrderPhoneLink.objects.filter(
                    order_id__in=order_ids,
                    normalized_phone__in=phones,
                ).values_list("order_id", "normalized_phone")
            )

        links_to_create = []
        seen_new_links = set()
        for snapshot in snapshots:
            phone = clean_value(snapshot.normalized_phone)
            if not phone:
                continue
            order = orders_by_key.get(self._idempotency_key(snapshot))
            if order is None:
                continue
            pair = (order.order_id, phone)
            if pair in existing_pairs or pair in seen_new_links:
                stats["links_existing"] += 1
                continue
            profile = profiles_by_key.get(profile_key_for_phone(phone))
            links_to_create.append(
                CustomerOrderPhoneLink(
                    order_id=order.order_id,
                    customer_id=profile.customer_id if profile else order.customer_id,
                    normalized_phone=phone,
                    raw_phone=clean_value(snapshot.customer_phone),
                    is_primary=True,
                    source="fitexpress",
                    country_id=snapshot.country_id,
                    metadata={
                        "source": "fitexpress_snapshot_import",
                        "snapshot_id": str(snapshot.snapshot_id),
                        "external_order_id": snapshot.external_order_id,
                        "status_id": snapshot.status_id,
                        "product_id": snapshot.product_id,
                        "created_at_remote": snapshot.created_at_remote.isoformat()
                        if snapshot.created_at_remote
                        else None,
                    },
                )
            )
            seen_new_links.add(pair)
            stats["links_create"] += 1

        if apply_changes:
            with transaction.atomic():
                if profiles_to_create:
                    CustomerProfile.objects.bulk_create(
                        profiles_to_create,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if profiles_to_update:
                    CustomerProfile.objects.bulk_update(
                        profiles_to_update,
                        [
                            "display_name",
                            "phone",
                            "email",
                            "first_seen_at",
                            "last_seen_at",
                            "last_product_detected",
                            "metadata",
                            "updated_at",
                        ],
                        batch_size=batch_size,
                    )
                if identities_to_create:
                    CustomerChannelIdentity.objects.bulk_create(
                        identities_to_create,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if orders_to_create:
                    CustomerOrder.objects.bulk_create(
                        orders_to_create,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if orders_to_update:
                    CustomerOrder.objects.bulk_update(
                        orders_to_update,
                        [
                            "customer_id",
                            "product_id",
                            "sku",
                            "quantity",
                            "cost",
                            "currency",
                            "status",
                            "source_channel",
                            "external_order_id",
                            "external_status",
                            "customer_comment",
                            "order_payload",
                            "submitted_at",
                            "updated_at",
                        ],
                        batch_size=batch_size,
                    )
                if links_to_create:
                    CustomerOrderPhoneLink.objects.bulk_create(
                        links_to_create,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )

    def _build_profile(self, snapshot, profile_key, now):
        ordered_at = order_datetime(snapshot)
        metadata = self._profile_metadata(snapshot)
        return CustomerProfile(
            customer_id=customer_id_for_profile_key(profile_key),
            profile_key=profile_key,
            display_name=clean_value(snapshot.customer_name),
            phone=clean_value(snapshot.normalized_phone),
            email=clean_value(snapshot.customer_email),
            first_seen_at=ordered_at,
            last_seen_at=snapshot.updated_at_remote or ordered_at,
            total_conversations=0,
            total_messages=0,
            last_product_detected=clean_value(snapshot.product_id),
            status="active",
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )

    def _merge_profile(self, profile, snapshot, now):
        changed = False
        ordered_at = order_datetime(snapshot)
        latest_at = snapshot.updated_at_remote or ordered_at

        if not clean_value(profile.display_name) and clean_value(snapshot.customer_name):
            profile.display_name = clean_value(snapshot.customer_name)
            changed = True
        if not clean_value(profile.phone) and clean_value(snapshot.normalized_phone):
            profile.phone = clean_value(snapshot.normalized_phone)
            changed = True
        if not clean_value(profile.email) and clean_value(snapshot.customer_email):
            profile.email = clean_value(snapshot.customer_email)
            changed = True
        if ordered_at and (profile.first_seen_at is None or ordered_at < profile.first_seen_at):
            profile.first_seen_at = ordered_at
            changed = True
        should_mark_latest = latest_at and (profile.last_seen_at is None or latest_at > profile.last_seen_at)
        if should_mark_latest:
            profile.last_seen_at = latest_at
            if clean_value(snapshot.product_id):
                profile.last_product_detected = clean_value(snapshot.product_id)
            changed = True

        metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        before = dict(metadata)
        fitexpress_metadata = metadata.get("fitexpress") if isinstance(metadata.get("fitexpress"), dict) else {}
        current_latest = parse_metadata_datetime(
            fitexpress_metadata.get("latest_updated_at") or fitexpress_metadata.get("latest_order_at")
        )
        if current_latest is None or (latest_at and latest_at > current_latest):
            metadata["fitexpress"] = self._profile_metadata(snapshot).get("fitexpress", {})
        metadata.setdefault("phone_normalized", clean_value(snapshot.normalized_phone))
        metadata.setdefault("phone_digits", "".join(ch for ch in clean_value(snapshot.normalized_phone) or "" if ch.isdigit()))
        if snapshot.country_id:
            metadata.setdefault("fitexpress_country_id", snapshot.country_id)
        currency = currency_for_snapshot(snapshot)
        if currency != "UNKNOWN":
            metadata.setdefault("currency", currency)
        if metadata != before:
            profile.metadata = metadata
            changed = True

        if changed:
            profile.updated_at = now
        return changed

    def _profile_metadata(self, snapshot):
        return {
            "source": "fitexpress_snapshot_import",
            "identity": "phone",
            "phone_normalized": clean_value(snapshot.normalized_phone),
            "phone_digits": "".join(ch for ch in clean_value(snapshot.normalized_phone) or "" if ch.isdigit()),
            "fitexpress_country_id": snapshot.country_id,
            "currency": currency_for_snapshot(snapshot),
            "fitexpress": {
                "latest_order_id": snapshot.external_order_id,
                "latest_product_id": clean_value(snapshot.product_id),
                "latest_status_id": snapshot.status_id,
                "latest_country_id": snapshot.country_id,
                "latest_location": clean_value(snapshot.customer_location),
                "latest_address": clean_value(snapshot.customer_address),
                "latest_zipcode": clean_value(snapshot.customer_zipcode),
                "latest_order_at": snapshot.created_at_remote.isoformat() if snapshot.created_at_remote else None,
                "latest_updated_at": snapshot.updated_at_remote.isoformat() if snapshot.updated_at_remote else None,
            },
        }

    def _build_order(self, snapshot, profiles_by_key, now):
        order = CustomerOrder()
        self._apply_order_snapshot(order, snapshot, profiles_by_key, now)
        return order

    def _apply_order_snapshot(self, order, snapshot, profiles_by_key, now):
        phone = clean_value(snapshot.normalized_phone)
        profile = profiles_by_key.get(profile_key_for_phone(phone)) if phone else None
        quantity = int_or_one(snapshot.quantity_number)
        currency = currency_for_snapshot(snapshot)
        submitted_at = order_datetime(snapshot)

        order.customer_id = profile.customer_id if profile else None
        order.product_id = clean_value(snapshot.product_id) or clean_value(snapshot.product_sku) or "unknown"
        order.sku = clean_value(snapshot.product_sku) or clean_value(snapshot.product_id)
        order.quantity = quantity
        order.cost = decimal_or_zero(snapshot.cost)
        order.currency = currency
        order.status = "submitted"
        order.source_channel = "fitexpress"
        order.external_order_id = snapshot.external_order_id
        order.external_status = str(snapshot.status_id) if snapshot.status_id is not None else None
        order.idempotency_key = self._idempotency_key(snapshot)
        order.customer_comment = clean_value(snapshot.customer_comment)
        order.order_payload = {
            "source": "fitexpress_snapshot_import",
            "snapshot_id": str(snapshot.snapshot_id),
            "external_order_id": snapshot.external_order_id,
            "status_id": snapshot.status_id,
            "product_id": clean_value(snapshot.product_id),
            "product_sku": clean_value(snapshot.product_sku),
            "country_id": snapshot.country_id,
            "region_id": snapshot.region_id,
            "currency_id": snapshot.currency_id,
            "shipping_cost": str(snapshot.shipping_cost) if snapshot.shipping_cost is not None else None,
            "payment_type": clean_value(snapshot.payment_type),
            "customer_paid_online": snapshot.customer_paid_online,
            "referral": clean_value(snapshot.referral),
            "source_channel_raw": clean_value(snapshot.source),
            "approve_method": clean_value(snapshot.approve_method),
            "tracking_url": clean_value(snapshot.tracking_url),
            "tracking_pdf": clean_value(snapshot.tracking_pdf),
            "customer": {
                "name": clean_value(snapshot.customer_name),
                "phone": clean_value(snapshot.customer_phone),
                "normalized_phone": phone,
                "email": clean_value(snapshot.customer_email),
                "location": clean_value(snapshot.customer_location),
                "address": clean_value(snapshot.customer_address),
                "zipcode": clean_value(snapshot.customer_zipcode),
                "streetnr": clean_value(snapshot.customer_streetnr),
                "blocknr": clean_value(snapshot.customer_blocknr),
                "appartmentnr": clean_value(snapshot.customer_appartmentnr),
            },
            "raw_payload": snapshot.raw_payload,
        }
        order.submitted_at = submitted_at
        order.updated_at = now

    def _idempotency_key(self, snapshot):
        return f"fitexpress:order:{snapshot.external_order_id}"
