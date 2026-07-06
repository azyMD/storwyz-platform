from django.core.management.base import BaseCommand
from django.db import connection


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


def fitexpress_metadata(alias):
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
            'currency', {currency_case(alias)}
        ))
    """


class Command(BaseCommand):
    help = "Fast staged SQL import from FitExpress snapshots into CRM profile order history."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag only counts pending work.")
        parser.add_argument("--analyze", action="store_true", help="Run ANALYZE after apply.")

    def handle(self, *args, **options):
        pending = self._pending_counts()
        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            f"{mode}: "
            f"missing_profiles={pending['missing_profiles']}; "
            f"missing_identities={pending['missing_identities']}; "
            f"missing_orders={pending['missing_orders']}; "
            f"existing_fitexpress_orders={pending['existing_fitexpress_orders']}; "
            f"orders_without_phone={pending['orders_without_phone']}"
        )
        if not options["apply"]:
            return

        self._execute("SET work_mem = '256MB'")
        phone_stage_count = self._prepare_phone_stage()
        self.stdout.write(f"phone_stage={phone_stage_count}")

        profiles_inserted = self._execute(self._insert_profiles_sql())
        self.stdout.write(f"profiles_inserted={profiles_inserted}")

        staged_profiles = self._refresh_stage_customer_ids()
        self.stdout.write(f"phone_stage_with_customer_id={staged_profiles}")

        profiles_updated = self._execute(self._update_profiles_sql())
        self.stdout.write(f"profiles_updated={profiles_updated}")

        identities_inserted = self._execute(self._insert_identities_sql())
        self.stdout.write(f"identities_inserted={identities_inserted}")

        orders_inserted = self._execute(self._insert_orders_sql())
        self.stdout.write(f"orders_inserted={orders_inserted}")

        order_link_stage_count = self._prepare_order_link_stage()
        self.stdout.write(f"order_link_stage={order_link_stage_count}")

        links_inserted = self._execute(self._insert_links_sql())
        self.stdout.write(f"links_inserted={links_inserted}")

        if options["analyze"]:
            self._analyze()
            self.stdout.write("analyze=done")

        totals = self._totals()
        self.stdout.write(
            self.style.SUCCESS(
                "APPLY complete. "
                f"fitexpress_orders_total={totals['fitexpress_orders_total']}; "
                f"fitexpress_links_total={totals['fitexpress_links_total']}; "
                f"fitexpress_profiles_with_phone={totals['fitexpress_profiles_with_phone']}; "
                f"missing_fitexpress_orders={totals['missing_fitexpress_orders']}; "
                f"missing_fitexpress_links={totals['missing_fitexpress_links']}."
            )
        )

    def _pending_counts(self):
        return self._fetch_one(
            """
            WITH phones AS (
                SELECT DISTINCT normalized_phone
                FROM fitexpress_order_snapshots
                WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
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
                    FROM fitexpress_order_snapshots s
                    LEFT JOIN crm_customer_orders o
                        ON o.idempotency_key = 'fitexpress:order:' || s.external_order_id
                    WHERE o.order_id IS NULL
                ) AS missing_orders,
                (
                    SELECT COUNT(*)
                    FROM crm_customer_orders
                    WHERE source_channel = 'fitexpress'
                ) AS existing_fitexpress_orders,
                (
                    SELECT COUNT(*)
                    FROM fitexpress_order_snapshots
                    WHERE normalized_phone IS NULL OR normalized_phone = ''
                ) AS orders_without_phone
            """
        )

    def _prepare_phone_stage(self):
        metadata_sql = fitexpress_metadata("l")
        currency_sql = currency_case("l")
        self._execute("DROP TABLE IF EXISTS pg_temp.fitexpress_phone_stage")
        self._execute(
            f"""
            CREATE TEMP TABLE fitexpress_phone_stage ON COMMIT PRESERVE ROWS AS
            WITH phone_base AS (
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
            SELECT
                b.normalized_phone,
                'phone:' || b.normalized_phone AS profile_key,
                NULLIF(l.customer_name, '') AS display_name,
                NULLIF(l.customer_email, '') AS email,
                b.first_seen,
                b.last_seen,
                NULLIF(l.product_id, '') AS last_product_detected,
                l.country_id,
                {currency_sql} AS currency,
                jsonb_strip_nulls(jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'identity', 'phone',
                    'phone_normalized', b.normalized_phone,
                    'phone_digits', regexp_replace(b.normalized_phone, '\\D', '', 'g'),
                    'fitexpress_country_id', l.country_id,
                    'currency', {currency_sql},
                    'fitexpress', {metadata_sql}
                )) AS metadata,
                NULL::uuid AS customer_id
            FROM phone_base b
            JOIN latest_phone l ON l.normalized_phone = b.normalized_phone
            """
        )
        self._execute("CREATE INDEX fitexpress_phone_stage_profile_idx ON fitexpress_phone_stage(profile_key)")
        self._execute("CREATE INDEX fitexpress_phone_stage_phone_idx ON fitexpress_phone_stage(normalized_phone)")
        self._execute("ANALYZE fitexpress_phone_stage")
        return self._fetch_value("SELECT COUNT(*) FROM fitexpress_phone_stage")

    def _insert_profiles_sql(self):
        return """
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
                profile_key,
                display_name,
                normalized_phone,
                email,
                first_seen,
                last_seen,
                0,
                0,
                last_product_detected,
                NULL,
                'active',
                metadata,
                NOW(),
                NOW()
            FROM fitexpress_phone_stage
            ON CONFLICT (profile_key) DO NOTHING
        """

    def _refresh_stage_customer_ids(self):
        self._execute(
            """
            UPDATE fitexpress_phone_stage st
            SET customer_id = p.customer_id
            FROM customer_profiles p
            WHERE p.profile_key = st.profile_key
            """
        )
        self._execute("ANALYZE fitexpress_phone_stage")
        return self._fetch_value("SELECT COUNT(*) FROM fitexpress_phone_stage WHERE customer_id IS NOT NULL")

    def _update_profiles_sql(self):
        return """
            UPDATE customer_profiles p
            SET
                display_name = COALESCE(NULLIF(p.display_name, ''), st.display_name),
                phone = COALESCE(NULLIF(p.phone, ''), st.normalized_phone),
                email = COALESCE(NULLIF(p.email, ''), st.email),
                first_seen_at = CASE
                    WHEN p.first_seen_at IS NULL OR st.first_seen < p.first_seen_at THEN st.first_seen
                    ELSE p.first_seen_at
                END,
                last_seen_at = CASE
                    WHEN p.last_seen_at IS NULL OR st.last_seen > p.last_seen_at THEN st.last_seen
                    ELSE p.last_seen_at
                END,
                last_product_detected = CASE
                    WHEN p.last_seen_at IS NULL OR st.last_seen > p.last_seen_at
                        THEN COALESCE(st.last_product_detected, p.last_product_detected)
                    ELSE p.last_product_detected
                END,
                metadata = COALESCE(p.metadata, '{}'::jsonb) || st.metadata,
                updated_at = NOW()
            FROM fitexpress_phone_stage st
            WHERE p.customer_id = st.customer_id
                AND (
                    p.metadata->'fitexpress' IS NULL
                    OR (p.display_name IS NULL AND st.display_name IS NOT NULL)
                    OR (p.phone IS NULL AND st.normalized_phone IS NOT NULL)
                    OR (p.email IS NULL AND st.email IS NOT NULL)
                    OR p.first_seen_at IS NULL
                    OR st.first_seen < p.first_seen_at
                    OR p.last_seen_at IS NULL
                    OR st.last_seen > p.last_seen_at
                )
        """

    def _insert_identities_sql(self):
        return """
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
                customer_id,
                'phone',
                normalized_phone,
                normalized_phone,
                'fitexpress',
                NULL,
                TRUE,
                'active',
                first_seen,
                last_seen,
                jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'profile_key', profile_key
                ),
                NOW(),
                NOW()
            FROM fitexpress_phone_stage
            WHERE customer_id IS NOT NULL
            ON CONFLICT (channel, normalized_identifier) DO NOTHING
        """

    def _insert_orders_sql(self):
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
                st.customer_id,
                COALESCE(NULLIF(s.product_id, ''), NULLIF(s.product_sku, ''), 'unknown'),
                COALESCE(NULLIF(s.product_sku, ''), NULLIF(s.product_id, '')),
                COALESCE(NULLIF(s.quantity_number, 0), 1),
                COALESCE(s.cost, 0),
                {currency_case("s")},
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
            LEFT JOIN fitexpress_phone_stage st
                ON st.normalized_phone = s.normalized_phone
            ON CONFLICT (idempotency_key) DO NOTHING
        """

    def _prepare_order_link_stage(self):
        self._execute("DROP TABLE IF EXISTS pg_temp.fitexpress_order_link_stage")
        self._execute(
            """
            CREATE TEMP TABLE fitexpress_order_link_stage ON COMMIT PRESERVE ROWS AS
            SELECT
                o.order_id,
                st.customer_id,
                s.normalized_phone,
                NULLIF(s.customer_phone, '') AS raw_phone,
                s.country_id,
                jsonb_strip_nulls(jsonb_build_object(
                    'source', 'fitexpress_snapshot_import',
                    'snapshot_id', s.snapshot_id::text,
                    'external_order_id', s.external_order_id,
                    'status_id', s.status_id,
                    'product_id', NULLIF(s.product_id, ''),
                    'created_at_remote', s.created_at_remote
                )) AS metadata
            FROM fitexpress_order_snapshots s
            JOIN crm_customer_orders o
                ON o.idempotency_key = 'fitexpress:order:' || s.external_order_id
            LEFT JOIN fitexpress_phone_stage st
                ON st.normalized_phone = s.normalized_phone
            WHERE s.normalized_phone IS NOT NULL AND s.normalized_phone <> ''
            """
        )
        self._execute("CREATE INDEX fitexpress_order_link_stage_pair_idx ON fitexpress_order_link_stage(order_id, normalized_phone)")
        self._execute("ANALYZE fitexpress_order_link_stage")
        return self._fetch_value("SELECT COUNT(*) FROM fitexpress_order_link_stage")

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
                order_id,
                customer_id,
                normalized_phone,
                raw_phone,
                TRUE,
                'fitexpress',
                country_id,
                metadata,
                NOW(),
                NOW()
            FROM fitexpress_order_link_stage
            ON CONFLICT (order_id, normalized_phone) DO NOTHING
        """

    def _totals(self):
        return self._fetch_one(
            """
            WITH snapshot_orders AS (
                SELECT s.external_order_id, s.normalized_phone, o.order_id
                FROM fitexpress_order_snapshots s
                LEFT JOIN crm_customer_orders o
                    ON o.idempotency_key = 'fitexpress:order:' || s.external_order_id
            )
            SELECT
                (SELECT COUNT(*) FROM crm_customer_orders WHERE source_channel = 'fitexpress') AS fitexpress_orders_total,
                (SELECT COUNT(*) FROM crm_customer_order_phone_links WHERE source = 'fitexpress') AS fitexpress_links_total,
                (
                    SELECT COUNT(DISTINCT customer_id)
                    FROM fitexpress_phone_stage
                    WHERE customer_id IS NOT NULL
                ) AS fitexpress_profiles_with_phone,
                (
                    SELECT COUNT(*)
                    FROM snapshot_orders
                    WHERE order_id IS NULL
                ) AS missing_fitexpress_orders,
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
                ) AS missing_fitexpress_links
            """
        )

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

    def _fetch_value(self, sql):
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchone()[0]
