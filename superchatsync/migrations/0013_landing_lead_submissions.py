import uuid

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("superchatsync", "0012_peeko_route_operator_only_gate"),
    ]

    operations = [
        migrations.CreateModel(
            name="LandingLeadSubmission",
            fields=[
                ("lead_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                            ("validation_failed", "Validation failed"),
                        ],
                        db_index=True,
                        default="received",
                        max_length=32,
                    ),
                ),
                ("customer_name", models.TextField(blank=True, null=True)),
                ("customer_phone", models.TextField(blank=True, db_index=True, null=True)),
                ("customer_region", models.PositiveIntegerField(blank=True, db_index=True, null=True)),
                ("customer_address", models.TextField(blank=True, null=True)),
                ("quantity", models.PositiveIntegerField(blank=True, null=True)),
                ("cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("product", models.TextField(blank=True, db_index=True, null=True)),
                ("referral", models.TextField(blank=True, null=True)),
                ("customer_comment", models.TextField(blank=True, null=True)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("forwarded_payload", models.JSONField(blank=True, default=dict)),
                ("validation_errors", models.JSONField(blank=True, default=dict)),
                ("forward_url", models.TextField(blank=True, null=True)),
                ("upstream_http_status", models.PositiveIntegerField(blank=True, null=True)),
                ("upstream_response", models.TextField(blank=True, null=True)),
                ("external_order_id", models.TextField(blank=True, db_index=True, null=True)),
                ("error", models.TextField(blank=True, null=True)),
                ("source_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True, null=True)),
                ("origin", models.TextField(blank=True, null=True)),
                ("received_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Landing Lead Submission",
                "verbose_name_plural": "Landing Lead Submissions",
                "db_table": "landing_lead_submissions",
                "ordering": ["-received_at"],
            },
        ),
        migrations.AddIndex(
            model_name="landingleadsubmission",
            index=models.Index(fields=["status", "received_at"], name="landlead_status_time_idx"),
        ),
        migrations.AddIndex(
            model_name="landingleadsubmission",
            index=models.Index(fields=["product", "received_at"], name="landlead_product_time_idx"),
        ),
    ]
