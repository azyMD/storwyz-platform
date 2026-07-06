# Generated manually for Wyzbox order phone links on 2026-06-29.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0004_fitexpress_reference"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerOrderPhoneLink",
            fields=[
                ("link_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("normalized_phone", models.TextField(db_index=True)),
                ("raw_phone", models.TextField(blank=True, null=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("source", models.CharField(default="wyzbox", max_length=40)),
                ("country_id", models.PositiveIntegerField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phone_links",
                        to="superchatsync.customerorder",
                    ),
                ),
            ],
            options={
                "verbose_name": "CRM Customer Order Phone Link",
                "verbose_name_plural": "CRM Customer Order Phone Links",
                "db_table": "crm_customer_order_phone_links",
                "ordering": ["order_id", "-is_primary", "normalized_phone"],
            },
        ),
        migrations.AddConstraint(
            model_name="customerorderphonelink",
            constraint=models.UniqueConstraint(fields=("order", "normalized_phone"), name="uniq_crm_order_phone_link"),
        ),
        migrations.AddIndex(
            model_name="customerorderphonelink",
            index=models.Index(fields=["normalized_phone"], name="crm_ordphone_norm_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorderphonelink",
            index=models.Index(fields=["customer_id"], name="crm_ordphone_customer_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorderphonelink",
            index=models.Index(fields=["source", "country_id"], name="crm_ordphone_src_country_idx"),
        ),
    ]
