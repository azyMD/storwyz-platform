import uuid

import django.db.models.deletion
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("superchatsync", "0009_business_product_rankings"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShortLink",
            fields=[
                ("link_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.SlugField(max_length=40, unique=True, db_index=True)),
                ("target_url", models.TextField()),
                ("title", models.TextField(blank=True, null=True)),
                ("business_slug", models.CharField(blank=True, db_index=True, max_length=80, null=True)),
                ("source_channel", models.CharField(default="whatsapp", max_length=30)),
                ("source_template", models.TextField(blank=True, null=True)),
                ("source_message_id", models.TextField(blank=True, null=True)),
                ("intent", models.TextField(blank=True, null=True)),
                ("conversation_id", models.TextField(blank=True, db_index=True, null=True)),
                ("contact_id", models.TextField(blank=True, null=True)),
                ("channel_id", models.TextField(blank=True, null=True)),
                ("customer_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("phone", models.TextField(blank=True, null=True)),
                ("product_id", models.TextField(blank=True, db_index=True, null=True)),
                ("product_name", models.TextField(blank=True, null=True)),
                ("campaign_id", models.TextField(blank=True, null=True)),
                ("thank_you_enabled", models.BooleanField(default=True)),
                (
                    "thank_you_body",
                    models.TextField(default="Thanks for checking it. If you need help choosing, just reply here."),
                ),
                ("thank_you_attempted_at", models.DateTimeField(blank=True, null=True)),
                ("thank_you_sent_at", models.DateTimeField(blank=True, null=True)),
                ("thank_you_message_id", models.TextField(blank=True, null=True)),
                ("last_thank_you_error", models.TextField(blank=True, null=True)),
                ("click_count", models.PositiveIntegerField(default=0)),
                ("first_clicked_at", models.DateTimeField(blank=True, null=True)),
                ("last_clicked_at", models.DateTimeField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Short Link",
                "verbose_name_plural": "Short Links",
                "db_table": "short_links",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ShortLinkClick",
            fields=[
                ("click_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("clicked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True, null=True)),
                ("referer", models.TextField(blank=True, null=True)),
                ("request_method", models.CharField(default="GET", max_length=12)),
                ("query_params", models.JSONField(blank=True, default=dict)),
                ("is_preview", models.BooleanField(default=False)),
                ("thank_you_queued", models.BooleanField(default=False)),
                ("thank_you_result", models.JSONField(blank=True, default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="clicks",
                        to="superchatsync.shortlink",
                    ),
                ),
            ],
            options={
                "verbose_name": "Short Link Click",
                "verbose_name_plural": "Short Link Clicks",
                "db_table": "short_link_clicks",
                "ordering": ["-clicked_at"],
            },
        ),
        migrations.AddIndex(
            model_name="shortlink",
            index=models.Index(fields=["business_slug", "conversation_id"], name="shortlink_biz_conv_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlink",
            index=models.Index(fields=["product_id", "created_at"], name="shortlink_product_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlink",
            index=models.Index(fields=["campaign_id"], name="shortlink_campaign_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlink",
            index=models.Index(fields=["active", "expires_at"], name="shortlink_active_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlinkclick",
            index=models.Index(fields=["link", "clicked_at"], name="shortclick_link_time_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlinkclick",
            index=models.Index(fields=["ip_address"], name="shortclick_ip_idx"),
        ),
        migrations.AddIndex(
            model_name="shortlinkclick",
            index=models.Index(fields=["is_preview"], name="shortclick_preview_idx"),
        ),
    ]
