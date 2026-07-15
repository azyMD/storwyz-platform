import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("superchatsync", "0013_landing_lead_submissions"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandingProductMapping",
            fields=[
                ("mapping_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("product_name", models.TextField()),
                ("normalized_name", models.TextField(db_index=True, unique=True)),
                ("sku", models.TextField(db_index=True)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("product_knowledge", "Product knowledge"),
                            ("product_alias", "Product alias"),
                            ("manual", "Manual"),
                        ],
                        default="manual",
                        max_length=40,
                    ),
                ),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Landing Product Mapping",
                "verbose_name_plural": "Landing Product Mappings",
                "db_table": "landing_product_mappings",
                "ordering": ["product_name"],
            },
        ),
        migrations.AddField(
            model_name="landingleadsubmission",
            name="product_mapping",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="leads",
                to="superchatsync.landingproductmapping",
            ),
        ),
        migrations.AddField(
            model_name="landingleadsubmission",
            name="product_normalized",
            field=models.TextField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="landingleadsubmission",
            name="product_sku",
            field=models.TextField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="landingleadsubmission",
            name="status",
            field=models.CharField(
                choices=[
                    ("received", "Received"),
                    ("mapping_required", "Mapping required"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                    ("validation_failed", "Validation failed"),
                ],
                db_index=True,
                default="received",
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="landingproductmapping",
            index=models.Index(fields=["active", "product_name"], name="landprod_active_name_idx"),
        ),
    ]
