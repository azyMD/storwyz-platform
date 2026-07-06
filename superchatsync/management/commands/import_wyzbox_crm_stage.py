import json
import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from superchatsync.models import (
    CustomerChannelIdentity,
    CustomerOrder,
    CustomerOrderPhoneLink,
    CustomerProfile,
)


CUSTOMER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://storwyz.com/crm/customer-profile")


def clean_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def int_value(value, default=None):
    text = clean_value(value)
    if text is None:
        return default
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


def decimal_value(value):
    text = clean_value(value)
    if text is None:
        return Decimal("0.00")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def parse_stage_datetime(value):
    text = clean_value(value)
    if not text or text.startswith("0000-00-00"):
        return None
    parsed = parse_datetime(text)
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def profile_key_for_phone(phone):
    phone = clean_value(phone)
    return f"phone:{phone}" if phone else None


def customer_id_for_profile_key(profile_key):
    return uuid.uuid5(CUSTOMER_NAMESPACE, profile_key)


def load_payload(value):
    text = clean_value(value)
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_payload_json": text}
    return payload if isinstance(payload, dict) else {"raw_payload_json": payload}


class Command(BaseCommand):
    help = "Import normalized Wyzbox CRM staging data into customer profiles and order history."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="Path to wyzbox_crm_stage.sqlite.")
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag the command is dry-run.")
        parser.add_argument("--limit", type=int, default=None, help="Limit rows per staging table for a safe test run.")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument(
            "--update-existing-profiles",
            action="store_true",
            help="Also enrich existing CustomerProfile rows. Off by default for large imports.",
        )

    def handle(self, *args, **options):
        source = Path(options["source"]).expanduser()
        if not source.exists():
            raise CommandError(f"Source SQLite file not found: {source}")

        apply_changes = bool(options["apply"])
        limit = options["limit"]
        batch_size = max(1, int(options["batch_size"] or 1000))

        connection = sqlite3.connect(str(source))
        connection.row_factory = sqlite3.Row

        try:
            self._validate_source(connection)
            counts = self._source_counts(connection, limit)
            mode = "APPLY" if apply_changes else "DRY-RUN"
            self.stdout.write(
                f"{mode}: source={source}; "
                f"profiles={counts['profiles']}; orders={counts['orders']}; "
                f"order_phone_links={counts['order_phone_links']}; limit={limit or 'none'}"
            )

            if not apply_changes and limit is None:
                self.stdout.write(
                    self.style.WARNING(
                        "Dry-run without --limit only validates the source and counts rows. "
                        "Use --limit for a sampled dry-run or --apply for the real import."
                    )
                )
                return

            profile_stats = self._import_profiles(
                connection,
                batch_size,
                limit,
                apply_changes,
                options["update_existing_profiles"],
            )
            order_stats = self._import_orders(connection, batch_size, limit, apply_changes)
            link_stats = self._import_order_phone_links(connection, batch_size, limit, apply_changes)
        finally:
            connection.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} complete. "
                f"profiles seen={profile_stats['seen']}, create={profile_stats['create']}, "
                f"existing={profile_stats['existing']}, update={profile_stats['update']}, "
                f"identities={profile_stats['identities']}; "
                f"orders seen={order_stats['seen']}, create={order_stats['create']}, "
                f"existing={order_stats['existing']}; "
                f"links seen={link_stats['seen']}, create={link_stats['create']}, "
                f"existing={link_stats['existing']}, skipped_missing_order={link_stats['skipped_missing_order']}."
            )
        )

    def _validate_source(self, connection):
        expected_tables = {"profiles", "orders", "order_phone_links"}
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = {row["name"] for row in rows}
        missing = sorted(expected_tables - tables)
        if missing:
            raise CommandError(f"Source SQLite is missing table(s): {', '.join(missing)}")

    def _source_counts(self, connection, limit):
        counts = {}
        for table in ("profiles", "orders", "order_phone_links"):
            total = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
            counts[table] = min(total, limit) if limit else total
        return counts

    def _iter_rows(self, connection, table, order_by, batch_size, limit):
        sql = f"SELECT * FROM {table} ORDER BY {order_by}"
        params = []
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = connection.execute(sql, params)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield rows

    def _import_profiles(self, connection, batch_size, limit, apply_changes, update_existing_profiles):
        stats = {"seen": 0, "create": 0, "existing": 0, "update": 0, "identities": 0}
        now = timezone.now()

        for rows in self._iter_rows(connection, "profiles", "normalized_phone", batch_size, limit):
            stats["seen"] += len(rows)
            profile_keys = [row["profile_key"] for row in rows if clean_value(row["profile_key"])]
            existing_profiles = {
                profile.profile_key: profile
                for profile in CustomerProfile.objects.filter(profile_key__in=profile_keys)
            }
            existing_identities = set(
                CustomerChannelIdentity.objects.filter(
                    channel="phone",
                    normalized_identifier__in=[
                        row["normalized_phone"]
                        for row in rows
                        if clean_value(row["normalized_phone"])
                    ],
                ).values_list("normalized_identifier", flat=True)
            )

            profiles_to_create = []
            profiles_to_update = []
            identities_to_create = []

            for row in rows:
                profile_key = clean_value(row["profile_key"])
                normalized_phone = clean_value(row["normalized_phone"])
                if not profile_key or not normalized_phone:
                    continue

                first_seen = parse_stage_datetime(row["first_seen_at"])
                last_seen = parse_stage_datetime(row["last_seen_at"])
                country_id = int_value(row["country_id"])
                latest_product_id = clean_value(row["latest_product_id"])
                metadata = {
                    "source": "wyzbox_import",
                    "identity": "phone",
                    "phone_normalized": normalized_phone,
                    "phone_digits": "".join(ch for ch in normalized_phone if ch.isdigit()),
                    "fitexpress_country_id": country_id,
                    "country_name": clean_value(row["country_name"]),
                    "currency": clean_value(row["currency"]),
                    "orders_count": int_value(row["orders_count"], 0),
                    "latest_order_id": int_value(row["latest_order_id"]),
                    "latest_location": clean_value(row["latest_location"]),
                    "latest_address": clean_value(row["latest_address"]),
                    "latest_zipcode": clean_value(row["latest_zipcode"]),
                    "stage_updated_at": clean_value(row["updated_at"]),
                }

                profile = existing_profiles.get(profile_key)
                if profile is None:
                    customer_id = customer_id_for_profile_key(profile_key)
                    profile = CustomerProfile(
                        customer_id=customer_id,
                        profile_key=profile_key,
                        display_name=clean_value(row["display_name"]),
                        phone=normalized_phone,
                        email=clean_value(row["email"]),
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                        total_conversations=0,
                        total_messages=0,
                        last_product_detected=str(latest_product_id) if latest_product_id else None,
                        status="active",
                        metadata=metadata,
                        created_at=now,
                        updated_at=now,
                    )
                    profiles_to_create.append(profile)
                    existing_profiles[profile_key] = profile
                    stats["create"] += 1
                else:
                    stats["existing"] += 1
                    if update_existing_profiles and self._merge_existing_profile(
                        profile,
                        row,
                        metadata,
                        first_seen,
                        last_seen,
                        latest_product_id,
                        now,
                    ):
                        profiles_to_update.append(profile)
                        stats["update"] += 1

                if normalized_phone not in existing_identities:
                    identities_to_create.append(
                        CustomerChannelIdentity(
                            customer_id=profile.customer_id,
                            channel="phone",
                            identifier=normalized_phone,
                            normalized_identifier=normalized_phone,
                            provider="wyzbox",
                            is_primary=True,
                            status="active",
                            first_seen_at=first_seen,
                            last_seen_at=last_seen,
                            metadata={"source": "wyzbox_import", "profile_key": profile_key},
                        )
                    )
                    existing_identities.add(normalized_phone)
                    stats["identities"] += 1

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
                                "status",
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

            if stats["seen"] % (batch_size * 20) == 0:
                self.stdout.write(
                    f"profiles progress: seen={stats['seen']}, create={stats['create']}, "
                    f"existing={stats['existing']}, identities={stats['identities']}"
                )

        return stats

    def _merge_existing_profile(self, profile, row, wyzbox_metadata, first_seen, last_seen, latest_product_id, now):
        changed = False

        if not clean_value(profile.display_name) and clean_value(row["display_name"]):
            profile.display_name = clean_value(row["display_name"])
            changed = True
        if not clean_value(profile.phone) and clean_value(row["normalized_phone"]):
            profile.phone = clean_value(row["normalized_phone"])
            changed = True
        if not clean_value(profile.email) and clean_value(row["email"]):
            profile.email = clean_value(row["email"])
            changed = True
        if first_seen and (profile.first_seen_at is None or first_seen < profile.first_seen_at):
            profile.first_seen_at = first_seen
            changed = True
        if last_seen and (profile.last_seen_at is None or last_seen > profile.last_seen_at):
            profile.last_seen_at = last_seen
            changed = True
        if latest_product_id and not clean_value(profile.last_product_detected):
            profile.last_product_detected = str(latest_product_id)
            changed = True
        if not clean_value(profile.status):
            profile.status = "active"
            changed = True

        metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        before = dict(metadata)
        metadata["wyzbox"] = wyzbox_metadata
        metadata.setdefault("phone_normalized", wyzbox_metadata.get("phone_normalized"))
        metadata.setdefault("phone_digits", wyzbox_metadata.get("phone_digits"))
        if wyzbox_metadata.get("country_name"):
            metadata.setdefault("country_name", wyzbox_metadata["country_name"])
        if wyzbox_metadata.get("fitexpress_country_id"):
            metadata.setdefault("fitexpress_country_id", wyzbox_metadata["fitexpress_country_id"])
        if wyzbox_metadata.get("currency"):
            metadata.setdefault("currency", wyzbox_metadata["currency"])
        if metadata != before:
            profile.metadata = metadata
            changed = True

        if changed:
            profile.updated_at = now
        return changed

    def _import_orders(self, connection, batch_size, limit, apply_changes):
        stats = {"seen": 0, "create": 0, "existing": 0}
        now = timezone.now()

        for rows in self._iter_rows(connection, "orders", "order_id", batch_size, limit):
            stats["seen"] += len(rows)
            idempotency_keys = [f"wyzbox:order:{row['order_id']}" for row in rows]
            existing_keys = set(
                CustomerOrder.objects.filter(idempotency_key__in=idempotency_keys)
                .values_list("idempotency_key", flat=True)
            )
            phones = [row["primary_phone"] for row in rows if clean_value(row["primary_phone"])]
            profiles_by_phone = self._profiles_by_phone(phones)
            orders_to_create = []

            for row in rows:
                idempotency_key = f"wyzbox:order:{row['order_id']}"
                if idempotency_key in existing_keys:
                    stats["existing"] += 1
                    continue

                primary_phone = clean_value(row["primary_phone"])
                customer_id = profiles_by_phone.get(primary_phone)
                if customer_id is None and primary_phone:
                    customer_id = customer_id_for_profile_key(profile_key_for_phone(primary_phone))

                payload = load_payload(row["payload_json"])
                payload["wyzbox_import"] = {
                    "stage_order_id": int_value(row["order_id"]),
                    "source_order_code": clean_value(row["source_order_code"]),
                    "status_id": int_value(row["status_id"]),
                    "country_id": int_value(row["country_id"]),
                    "region_id": int_value(row["region_id"]),
                    "referral": clean_value(row["referral"]),
                    "source": clean_value(row["source"]),
                    "click_id": clean_value(row["click_id"]),
                    "currency_id": int_value(row["currency_id"]),
                    "paid": int_value(row["paid"], 0),
                    "primary_phone": primary_phone,
                    "customer_phone_raw": clean_value(row["customer_phone_raw"]),
                }

                submitted_at = (
                    parse_stage_datetime(row["created_at"])
                    or parse_stage_datetime(row["updated_at"])
                    or now
                )
                product_id = clean_value(row["product_id"]) or "unknown"
                quantity = int_value(row["quantity_number"], 1) or 1
                orders_to_create.append(
                    CustomerOrder(
                        customer_id=customer_id,
                        product_id=str(product_id),
                        sku=str(product_id),
                        quantity=quantity,
                        cost=decimal_value(row["cost"]),
                        currency=clean_value(row["currency"]) or "RON",
                        status="submitted",
                        source_channel="wyzbox",
                        external_order_id=str(row["order_id"]),
                        external_status=str(row["status_id"]) if row["status_id"] is not None else None,
                        idempotency_key=idempotency_key,
                        customer_comment=clean_value(row["customer_comment"]),
                        order_payload=payload,
                        submitted_at=submitted_at,
                    )
                )
                stats["create"] += 1

            if apply_changes and orders_to_create:
                CustomerOrder.objects.bulk_create(
                    orders_to_create,
                    batch_size=batch_size,
                    ignore_conflicts=True,
                )

            if stats["seen"] % (batch_size * 20) == 0:
                self.stdout.write(
                    f"orders progress: seen={stats['seen']}, create={stats['create']}, "
                    f"existing={stats['existing']}"
                )

        return stats

    def _import_order_phone_links(self, connection, batch_size, limit, apply_changes):
        stats = {"seen": 0, "create": 0, "existing": 0, "skipped_missing_order": 0}

        for rows in self._iter_rows(connection, "order_phone_links", "order_id, normalized_phone", batch_size, limit):
            stats["seen"] += len(rows)
            if not apply_changes:
                stats["create"] += len([row for row in rows if clean_value(row["normalized_phone"])])
                continue

            stage_order_ids = [int_value(row["order_id"]) for row in rows if int_value(row["order_id"]) is not None]
            idempotency_keys = [f"wyzbox:order:{order_id}" for order_id in stage_order_ids]
            orders = CustomerOrder.objects.filter(idempotency_key__in=idempotency_keys)
            order_ids_by_stage_id = {
                int(order.idempotency_key.rsplit(":", 1)[-1]): order.order_id
                for order in orders
            }
            phones = [row["normalized_phone"] for row in rows if clean_value(row["normalized_phone"])]
            profiles_by_phone = self._profiles_by_phone(phones)
            existing_pairs = set(
                CustomerOrderPhoneLink.objects.filter(
                    order_id__in=list(order_ids_by_stage_id.values()),
                    normalized_phone__in=phones,
                ).values_list("order_id", "normalized_phone")
            )

            links_to_create = []
            for row in rows:
                stage_order_id = int_value(row["order_id"])
                normalized_phone = clean_value(row["normalized_phone"])
                if not stage_order_id or not normalized_phone:
                    continue
                order_uuid = order_ids_by_stage_id.get(stage_order_id)
                if order_uuid is None:
                    stats["skipped_missing_order"] += 1
                    continue
                if (order_uuid, normalized_phone) in existing_pairs:
                    stats["existing"] += 1
                    continue

                customer_id = profiles_by_phone.get(normalized_phone)
                if customer_id is None:
                    customer_id = customer_id_for_profile_key(profile_key_for_phone(normalized_phone))

                links_to_create.append(
                    CustomerOrderPhoneLink(
                        order_id=order_uuid,
                        customer_id=customer_id,
                        normalized_phone=normalized_phone,
                        raw_phone=clean_value(row["raw_phone"]),
                        is_primary=bool(int_value(row["is_primary"], 0)),
                        source="wyzbox",
                        country_id=int_value(row["country_id"]),
                        metadata={"source": "wyzbox_import", "stage_order_id": stage_order_id},
                    )
                )
                stats["create"] += 1

            if links_to_create:
                CustomerOrderPhoneLink.objects.bulk_create(
                    links_to_create,
                    batch_size=batch_size,
                    ignore_conflicts=True,
                )

            if stats["seen"] % (batch_size * 20) == 0:
                self.stdout.write(
                    f"links progress: seen={stats['seen']}, create={stats['create']}, "
                    f"existing={stats['existing']}, skipped_missing_order={stats['skipped_missing_order']}"
                )

        return stats

    def _profiles_by_phone(self, phones):
        unique_phones = sorted({clean_value(phone) for phone in phones if clean_value(phone)})
        if not unique_phones:
            return {}
        profile_keys = [profile_key_for_phone(phone) for phone in unique_phones]
        profiles = CustomerProfile.objects.filter(profile_key__in=profile_keys)
        return {
            profile.profile_key.replace("phone:", "", 1): profile.customer_id
            for profile in profiles
        }
