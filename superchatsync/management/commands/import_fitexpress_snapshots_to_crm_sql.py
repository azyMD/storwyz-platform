from django.core.management.base import BaseCommand
from django.db import connection, transaction


def currency_case(alias):
    return f"""
        CASE {alias}.country_id
            WHEN 1001 THEN 'RON'
            WHEN 1044 THEN 'MDL'
            WHEN 1081 THEN 'BGN'
            WHEN 1109 THEN 'CZK'
            WHEN 1186 THEN 'PLN'
            WHEN 1300 THEN 'EUR'
            WHEN 1400 THEN 'UAH'
            WHEN 1500 THEN 'HUF'
            WHEN 1600 THEN 'GBP'
            WHEN 1700 THEN 'EUR'
            WHEN 1800 THEN 'TRY'
            WHEN 1801 THEN 'EUR'
            WHEN 1802 THEN 'EUR'
            WHEN 1803 THEN 'RSD'
            WHEN 1804 THEN 'VND'
            WHEN 1805 THEN 'SGD'
            WHEN 1806 THEN 'MXN'
            WHEN 1807 THEN 'IDR'
            WHEN 1808 THEN 'EUR'
            WHEN 1809 THEN 'VND'
            WHEN 1811 THEN 'EUR'
            WHEN 1812 THEN 'EUR'
            WHEN 1813 THEN 'EUR'
            WHEN 1814 THEN 'EUR'
            WHEN 1815 THEN 'EUR'
            WHEN 1816 THEN 'EGP'
            WHEN 1817 THEN 'NGN'
            WHEN 1818 THEN 'KES'
            WHEN 1819 THEN 'SAR'
            WHEN 1820 THEN 'USD'
            WHEN 1821 THEN 'EUR'
            WHEN 1822 THEN 'EUR'
            WHEN 1823 THEN 'EUR'
            WHEN 1824 THEN 'CHF'
            WHEN 1825 THEN 'COP'
            ELSE CASE {alias}.currency_id
                WHEN 2 THEN 'CZK'
                WHEN 3 THEN 'BGN'
                WHEN 4 THEN 'TRY'
                WHEN 5 THEN 'EUR'
                WHEN 6 THEN 'RON'
                WHEN 10 THEN 'PLN'
                WHEN 12 THEN 'MXN'
                ELSE 'UNKNOWN'
            END
        END
    """


PHONE_BASE_CTE = """
    phone_base AS (
        SELECT
            normalized_phone,
            MIN(COALESCE(created_at_remote, updated_at_remote, fetched_at)) AS first_seen,
            MAX(COALESCE(updated_at_remote, created_at_remote, fetched_at)) AS last_seen
        FROM fitexpress_order_snapshots
        WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
        GROUP BY normalized_phone
    ),
    latest_phone AS (
        SELECT DISTINCT ON (normalized_phone)
            *
        FROM fitexpress_order_snapshots
        WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
        ORDER BY
            normalized_phone,
            COALESCE(updated_at_remote, created_at_remote, fetched_at) DESC NULLS LAST,
            external_order_id DESC
    )
"""


def fitexpress_metadata_sql(alias):
    currency_sql = currency_case(alias)
    return f"""
        jsonb_strip_nulls(jsonb_build_object(
            'latest_order_id', {alias}.external_order_id,
            'latest_product_id', NULLIF({alias}.product_id, ''),
            'latest_status_id', {alias}.status_id,
            'latest_country_id', {alias}.country_id,
            'latest_location', NULLIF({alias}.customer_location, ''),
            'latest_address', NULLIF({alias}.customer_address, ''),
            'latest_zipcode', NULLIF({alias}.customer_zipcode, ''),
            'latest_order_at', {alias}.created_at_remote,
            'latest_updated_at', {alias}.updated_at_remote,
            'currency', {currency_sql}
        ))
    """


class Command(BaseCommand):
    help = "Fast SQL import from FitExpress snapshots into CRM profiles, orders, identities, and phone links."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag only counts pending work.")
        parser.add_argument("--analyze", action="store_true", help="Run ANALYZE on affected CRM tables after apply.")

    def handle(self, *args, **options):
        pending = self._pending_counts()
        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            f"{mode}: "
            f"missing_profiles={pending['missing_profiles']}; "
            f"missing_identities={pending['missing_identities']}; "
            f"missing_orders={pending['missing_orders']}; "
            f"missing_links={pending['missing_links']}; "
            f"orders_without_phone={pending['orders_without_phone']}"
        )

        if not options["apply"]:
            return

        with transaction.atomic():
            profile_inserted = self._execute(self._insert_profiles_sql())
            profile_updated = self._execute(self._update_profiles_sql())
            identities_inserted = self._execute(self._insert_identities_sql())
            orders_inserted = self._execute(self._insert_orders_sql())
            links_inserted = self._execute(self._insert_links_sql())

        if options["analyze"]:
            self._analyze()

        after = self._totals()
        self.stdout.write(
            self.style.SUCCESS(
                "APPLY complete. "
                f"profiles_inserted={profile_inserted}; "
                f"profiles_updated={profile_updated}; "
                f"identities_inserted={identities_inserted}; "
                f"orders_inserted={orders_inserted}; "
                f"links_inserted={links_inserted}; "
                f"fitexpress_orders_total={after['fitexpress_orders_total']}; "
                f"fitexpress_links_total={after['fitexpress_links_total']}; "
                f"fitexpress_profiles_with_phone={after['fitexpress_profiles_with_phone']}."
            )
        )

    def _pending_counts(self):
        sql = """
            WITH phones AS (
                SELECT DISTINCT normalized_phone
                FROM fitexpress_order_snapshots
                WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
            ),
            snapshot_orders AS (
                SELECT
                    s.snapshot_id,
                    s.external_order_id,
                    s.normalized_phone,
                    o.order_id
                FROM fitexpress_order_snapshots s
                LEFT JOIN crm_customer_orders o
                    ON o.idempotency_key = 'fitexpress:order:' || s.external_order_id
            )
            SELECT
                (
                    SELECT COUNT(*)
                    FROM phones ph
                    LEFT JOIN customer_profiles p
                        ON p.profile_key = 'phone:' || ph.normalized_phone
                    WHERE p.customer_id IS NULL
                ) AS missing_profiles,
                (
                    SELECT COUNT(*)
                    FROM phones ph
                    LEFT JOIN crm_channel_identities i
                        ON i.channel = 'phone'
                        AND i.normalized_identifier = ph.normalized_phone
                    WHERE i.identity_id IS NULL
                ) AS missing_identities,
                (
                    SELECT COUNT(*)
                    FROM snapshot_orders
                    WHERE order_id IS NULL
                ) AS missing_orders,
                (
                    SELECT COUNT(*)
                    FROM snapshot_orders so
                    LEFT JOIN crm_customer_order_phone_links l
                        ON l.order_id = so.order_id
                        AND l.normalized_phone = so.normalized_phone
                    WHERE so.normalized_phone IS NOT NULL
                        AND so.normalized_phone <> ''
                        AND so.order_id IS NOT NULL
                        AND l.link_id IS NULL
                ) AS missing_links,
                (
                    SELECT COUNT(*)
                    FROM fitexpress_order_snapshots
                    WHERE normalized_phone IS NULL OR normalized_phone = ''
                ) AS orders_without_phone
        """
        return self._fetch_one(sql)

    def _totals(self):
        sql = """
            SELECT
                (SELECT COUNT(*) FROM crm_customer_orders WHERE source_channel = 'fitexpress') AS fitexpress_orders_total,
                (SELECT COUNT(*) FROM crm_customer_order_phone_links WHERE source = 'fitexpress') AS fitexpress_links_total,
                (
                    SELECT COUNT(DISTINCT p.customer_id)
                    FROM customer_profiles p
                    JOIN fitexpress_order_snapshots s
                        ON p.profile_key = 'phone:' || s.normalized_phone
                    WHERE s.normalized_phone IS NOT NULL AND s.normalized_phone <> ''
                ) AS fitexpress_profiles_with_phone
        """
        return self._fetch_one(sql)

    def _insert_profiles_sql(self):
        fitexpress_metadata = fitexpress_metadata_sql("l")
        currency_sql = currency_case("l")
        return f"""
            WITH {PHONE_BASE_CTE}
            INSERT INTO customer_profiles (
                customer_id,
                profile_key,
                display_name,
                phone,
                email,
                first_seen_at,
                last_seen_at,
                total_conversations,
                total_messages,
                last_product_detected,
                last_conversation_id,
                status,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                'phone:' || l.normalized_phone,
                NULLIF(l.customer_name, ''),
                l.normalized_phone,
                NULLIF(l.customer_email, ''),
                b.first_seen,
                b.last_seen,
                0,
                0,
                NULLIF(l.product_id, ''),
                NULL,
                'active',
                jsonb_strip_nulls(jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'identity', 'phone',
                    'phone_normalized', l.normalized_phone,
                    'phone_digits', regexp_replace(l.normalized_phone, '\\D', '', 'g'),
                    'fitexpress_country_id', l.country_id,
                    'currency', {currency_sql},
                    'fitexpress', {fitexpress_metadata}
                )),
                NOW(),
                NOW()
            FROM phone_base b
            JOIN latest_phone l ON l.normalized_phone = b.normalized_phone
            LEFT JOIN customer_profiles p ON p.profile_key = 'phone:' || l.normalized_phone
            WHERE p.customer_id IS NULL
            ON CONFLICT (profile_key) DO NOTHING
        """

    def _update_profiles_sql(self):
        fitexpress_metadata = fitexpress_metadata_sql("l")
        currency_sql = currency_case("l")
        return f"""
            WITH {PHONE_BASE_CTE}
            UPDATE customer_profiles p
            SET
                display_name = COALESCE(NULLIF(p.display_name, ''), NULLIF(l.customer_name, '')),
                phone = COALESCE(NULLIF(p.phone, ''), l.normalized_phone),
                email = COALESCE(NULLIF(p.email, ''), NULLIF(l.customer_email, '')),
                first_seen_at = CASE
                    WHEN p.first_seen_at IS NULL OR b.first_seen < p.first_seen_at THEN b.first_seen
                    ELSE p.first_seen_at
                END,
                last_seen_at = CASE
                    WHEN p.last_seen_at IS NULL OR b.last_seen > p.last_seen_at THEN b.last_seen
                    ELSE p.last_seen_at
                END,
                last_product_detected = CASE
                    WHEN p.last_seen_at IS NULL OR b.last_seen > p.last_seen_at
                        THEN COALESCE(NULLIF(l.product_id, ''), p.last_product_detected)
                    ELSE p.last_product_detected
                END,
                metadata = COALESCE(p.metadata, '{{}}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
                    'phone_normalized', l.normalized_phone,
                    'phone_digits', regexp_replace(l.normalized_phone, '\\D', '', 'g'),
                    'fitexpress_country_id', l.country_id,
                    'currency', {currency_sql},
                    'fitexpress', {fitexpress_metadata}
                )),
                updated_at = NOW()
            FROM phone_base b
            JOIN latest_phone l ON l.normalized_phone = b.normalized_phone
            WHERE p.profile_key = 'phone:' || b.normalized_phone
        """

    def _insert_identities_sql(self):
        return f"""
            WITH {PHONE_BASE_CTE}
            INSERT INTO crm_channel_identities (
                identity_id,
                customer_id,
                channel,
                identifier,
                normalized_identifier,
                provider,
                provider_contact_id,
                is_primary,
                status,
                first_seen_at,
                last_seen_at,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                p.customer_id,
                'phone',
                b.normalized_phone,
                b.normalized_phone,
                'fitexpress',
                NULL,
                TRUE,
                'active',
                b.first_seen,
                b.last_seen,
                jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'profile_key', p.profile_key
                ),
                NOW(),
                NOW()
            FROM phone_base b
            JOIN customer_profiles p ON p.profile_key = 'phone:' || b.normalized_phone
            LEFT JOIN crm_channel_identities i
                ON i.channel = 'phone'
                AND i.normalized_identifier = b.normalized_phone
            WHERE i.identity_id IS NULL
            ON CONFLICT (channel, normalized_identifier) DO NOTHING
        """

    def _insert_orders_sql(self):
        currency_sql = currency_case("s")
        return f"""
            INSERT INTO crm_customer_orders (
                order_id,
                customer_id,
                product_id,
                sku,
                quantity,
                cost,
                currency,
                status,
                source_channel,
                source_conversation_id,
                source_message_id,
                external_order_id,
                external_status,
                idempotency_key,
                webhook_url,
                webhook_http_status,
                customer_comment,
                order_payload,
                raw_response,
                submitted_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                p.customer_id,
                COALESCE(NULLIF(s.product_id, ''), NULLIF(s.product_sku, ''), 'unknown'),
                COALESCE(NULLIF(s.product_sku, ''), NULLIF(s.product_id, '')),
                COALESCE(NULLIF(s.quantity_number, 0), 1),
                COALESCE(s.cost, 0),
                {currency_sql},
                'submitted',
                'fitexpress',
                NULL,
                NULL,
                s.external_order_id,
                s.status_id::text,
                'fitexpress:order:' || s.external_order_id,
                NULL,
                NULL,
                NULLIF(s.customer_comment, ''),
                jsonb_strip_nulls(jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'snapshot_id', s.snapshot_id::text,
                    'external_order_id', s.external_order_id,
                    'status_id', s.status_id,
                    'product_id', NULLIF(s.product_id, ''),
                    'product_sku', NULLIF(s.product_sku, ''),
                    'country_id', s.country_id,
                    'region_id', s.region_id,
                    'currency_id', s.currency_id,
                    'shipping_cost', s.shipping_cost,
                    'payment_type', NULLIF(s.payment_type, ''),
                    'customer_paid_online', s.customer_paid_online,
                    'referral', NULLIF(s.referral, ''),
                    'source_channel_raw', NULLIF(s.source, ''),
                    'approve_method', NULLIF(s.approve_method, ''),
                    'tracking_url', NULLIF(s.tracking_url, ''),
                    'tracking_pdf', NULLIF(s.tracking_pdf, ''),
                    'customer', jsonb_strip_nulls(jsonb_build_object(
                        'name', NULLIF(s.customer_name, ''),
                        'phone', NULLIF(s.customer_phone, ''),
                        'normalized_phone', NULLIF(s.normalized_phone, ''),
                        'email', NULLIF(s.customer_email, ''),
                        'location', NULLIF(s.customer_location, ''),
                        'address', NULLIF(s.customer_address, ''),
                        'zipcode', NULLIF(s.customer_zipcode, ''),
                        'streetnr', NULLIF(s.customer_streetnr, ''),
                        'blocknr', NULLIF(s.customer_blocknr, ''),
                        'appartmentnr', NULLIF(s.customer_appartmentnr, '')
                    )),
                    'raw_payload', COALESCE(s.raw_payload, '{{}}'::jsonb)
                )),
                NULL,
                COALESCE(s.created_at_remote, s.updated_at_remote, s.fetched_at, NOW()),
                NOW()
            FROM fitexpress_order_snapshots s
            LEFT JOIN customer_profiles p
                ON p.profile_key = 'phone:' || s.normalized_phone
            WHERE NOT EXISTS (
                SELECT 1
                FROM crm_customer_orders o
                WHERE o.idempotency_key = 'fitexpress:order:' || s.external_order_id
            )
            ON CONFLICT (idempotency_key) DO NOTHING
        """

    def _insert_links_sql(self):
        return """
            INSERT INTO crm_customer_order_phone_links (
                link_id,
                order_id,
                customer_id,
                normalized_phone,
                raw_phone,
                is_primary,
                source,
                country_id,
                metadata,
                created_at,
                updated_at
            )
            SELECT
                gen_random_uuid(),
                o.order_id,
                p.customer_id,
                s.normalized_phone,
                NULLIF(s.customer_phone, ''),
                TRUE,
                'fitexpress',
                s.country_id,
                jsonb_strip_nulls(jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'snapshot_id', s.snapshot_id::text,
                    'external_order_id', s.external_order_id,
                    'status_id', s.status_id,
                    'product_id', NULLIF(s.product_id, ''),
                    'created_at_remote', s.created_at_remote
                )),
                NOW(),
                NOW()
            FROM fitexpress_order_snapshots s
            JOIN crm_customer_orders o
                ON o.idempotency_key = 'fitexpress:order:' || s.external_order_id
            LEFT JOIN customer_profiles p
                ON p.profile_key = 'phone:' || s.normalized_phone
            LEFT JOIN crm_customer_order_phone_links l
                ON l.order_id = o.order_id
                AND l.normalized_phone = s.normalized_phone
            WHERE s.normalized_phone IS NOT NULL
                AND s.normalized_phone <> ''
                AND l.link_id IS NULL
            ON CONFLICT (order_id, normalized_phone) DO NOTHING
        """

    def _analyze(self):
        for table in (
            "customer_profiles",
            "crm_channel_identities",
            "crm_customer_orders",
            "crm_customer_order_phone_links",
        ):
            self._execute(f"ANALYZE {table}")

    def _execute(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.rowcount

    def _fetch_one(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row))
