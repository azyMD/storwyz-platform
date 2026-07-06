# Generated manually for Fitexpress API order snapshots on 2026-06-30.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0005_customer_order_phone_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="FitexpressOrderSnapshot",
            fields=[
                ("snapshot_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("external_order_id", models.TextField(db_index=True, unique=True)),
                ("status_id", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("product_id", models.TextField(blank=True, db_index=True, null=True)),
                ("country_id", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("region_id", models.PositiveIntegerField(blank=True, null=True)),
                ("product_sku", models.TextField(blank=True, null=True)),
                ("quantity", models.TextField(blank=True, null=True)),
                ("quantity_number", models.IntegerField(blank=True, null=True)),
                ("cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("shipping_cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("currency_id", models.PositiveIntegerField(blank=True, null=True)),
                ("payment_type", models.TextField(blank=True, null=True)),
                ("customer_paid_online", models.BooleanField(blank=True, null=True)),
                ("customer_name", models.TextField(blank=True, null=True)),
                ("customer_location", models.TextField(blank=True, null=True)),
                ("customer_address", models.TextField(blank=True, null=True)),
                ("customer_phone", models.TextField(blank=True, null=True)),
                ("normalized_phone", models.TextField(blank=True, db_index=True, null=True)),
                ("customer_comment", models.TextField(blank=True, null=True)),
                ("customer_zipcode", models.TextField(blank=True, null=True)),
                ("customer_email", models.TextField(blank=True, null=True)),
                ("customer_age", models.TextField(blank=True, null=True)),
                ("customer_gender", models.TextField(blank=True, null=True)),
                ("customer_streetnr", models.TextField(blank=True, null=True)),
                ("customer_blocknr", models.TextField(blank=True, null=True)),
                ("customer_appartmentnr", models.TextField(blank=True, null=True)),
                ("deliver_date", models.TextField(blank=True, null=True)),
                ("created_at_remote", models.DateTimeField(blank=True, null=True)),
                ("updated_at_remote", models.DateTimeField(blank=True, null=True)),
                ("referral", models.TextField(blank=True, null=True)),
                ("source", models.TextField(blank=True, null=True)),
                ("curier_id", models.TextField(blank=True, null=True)),
                ("courier_note", models.TextField(blank=True, null=True)),
                ("tracking_url", models.TextField(blank=True, null=True)),
                ("tracking_pdf", models.TextField(blank=True, null=True)),
                ("approve_method", models.TextField(blank=True, null=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("fetch_params", models.JSONField(blank=True, default=dict)),
                ("fetched_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Fitexpress Order Snapshot",
                "verbose_name_plural": "Fitexpress Order Snapshots",
                "db_table": "fitexpress_order_snapshots",
                "ordering": ["-created_at_remote", "-updated_at_remote", "external_order_id"],
            },
        ),
        migrations.AddIndex(
            model_name="fitexpressordersnapshot",
            index=models.Index(fields=["status_id", "country_id"], name="fitexp_snap_status_country_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressordersnapshot",
            index=models.Index(fields=["product_id", "created_at_remote"], name="fitexp_snap_prod_cr_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressordersnapshot",
            index=models.Index(fields=["created_at_remote"], name="fitexp_snap_created_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressordersnapshot",
            index=models.Index(fields=["updated_at_remote"], name="fitexp_snap_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressordersnapshot",
            index=models.Index(fields=["normalized_phone"], name="fitexp_snap_phone_idx"),
        ),
    ]
