# Generated manually for Fitexpress reference mappings on 2026-06-27.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0003_customer_segments"),
    ]

    operations = [
        migrations.CreateModel(
            name="FitexpressCountry",
            fields=[
                ("country_id", models.PositiveIntegerField(primary_key=True, serialize=False)),
                ("country_name", models.TextField()),
                ("iso2", models.CharField(blank=True, max_length=2, null=True)),
                ("phone_prefixes", models.JSONField(blank=True, default=list)),
                ("default_language", models.CharField(blank=True, max_length=12, null=True)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Fitexpress Country",
                "verbose_name_plural": "Fitexpress Countries",
                "db_table": "fitexpress_countries",
                "ordering": ["country_name"],
            },
        ),
        migrations.CreateModel(
            name="FitexpressProductMapping",
            fields=[
                ("mapping_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_id", models.TextField(db_index=True, unique=True)),
                ("product_name", models.TextField()),
                ("fitexpress_product_id", models.TextField(db_index=True, unique=True)),
                ("aliases", models.JSONField(blank=True, default=list)),
                ("landing_url", models.TextField(blank=True, null=True)),
                ("match_status", models.CharField(default="exact", max_length=40)),
                ("options", models.JSONField(blank=True, default=dict)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Fitexpress Product Mapping",
                "verbose_name_plural": "Fitexpress Product Mappings",
                "db_table": "fitexpress_product_mappings",
                "ordering": ["product_name"],
            },
        ),
        migrations.AddIndex(
            model_name="fitexpresscountry",
            index=models.Index(fields=["country_name"], name="fitexpress__country_0ed3fc_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpresscountry",
            index=models.Index(fields=["iso2"], name="fitexpress__iso2_44198a_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpresscountry",
            index=models.Index(fields=["active"], name="fitexpress__active_13c547_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressproductmapping",
            index=models.Index(fields=["product_name"], name="fitexpress__product_b6e248_idx"),
        ),
        migrations.AddIndex(
            model_name="fitexpressproductmapping",
            index=models.Index(fields=["active"], name="fitexpress__active_754915_idx"),
        ),
    ]
