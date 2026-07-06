# Generated manually for CRM tables on 2026-06-27.

import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerChannelIdentity",
            fields=[
                ("identity_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(db_index=True)),
                ("channel", models.CharField(choices=[("whatsapp", "WhatsApp"), ("sms", "SMS"), ("phone", "Phone"), ("email", "Email"), ("push", "Push notification"), ("web", "Web"), ("other", "Other")], max_length=30)),
                ("identifier", models.TextField()),
                ("normalized_identifier", models.TextField()),
                ("provider", models.TextField(blank=True, null=True)),
                ("provider_contact_id", models.TextField(blank=True, null=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("status", models.CharField(default="active", max_length=30)),
                ("first_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "CRM Channel Identity",
                "verbose_name_plural": "CRM Channel Identities",
                "db_table": "crm_channel_identities",
                "ordering": ["customer_id", "channel", "normalized_identifier"],
            },
        ),
        migrations.CreateModel(
            name="CustomerCommunicationEvent",
            fields=[
                ("event_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(db_index=True)),
                ("channel_identity_id", models.UUIDField(blank=True, db_column="identity_id", db_index=True, null=True)),
                ("channel", models.CharField(choices=[("whatsapp", "WhatsApp"), ("sms", "SMS"), ("phone", "Phone"), ("email", "Email"), ("push", "Push notification"), ("web", "Web"), ("other", "Other")], max_length=30)),
                ("direction", models.CharField(choices=[("inbound", "Inbound"), ("outbound", "Outbound"), ("system", "System"), ("unknown", "Unknown")], default="unknown", max_length=20)),
                ("event_type", models.CharField(default="message", max_length=60)),
                ("status", models.CharField(blank=True, max_length=60, null=True)),
                ("provider", models.TextField(blank=True, null=True)),
                ("provider_message_id", models.TextField(blank=True, null=True)),
                ("conversation_id", models.TextField(blank=True, null=True)),
                ("message_id", models.TextField(blank=True, null=True)),
                ("campaign_id", models.TextField(blank=True, null=True)),
                ("workflow_id", models.TextField(blank=True, null=True)),
                ("subject", models.TextField(blank=True, null=True)),
                ("body_preview", models.TextField(blank=True, null=True)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "CRM Communication Event",
                "verbose_name_plural": "CRM Communication Events",
                "db_table": "crm_communication_events",
                "ordering": ["-occurred_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CustomerOrder",
            fields=[
                ("order_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("product_id", models.TextField()),
                ("sku", models.TextField(blank=True, null=True)),
                ("quantity", models.IntegerField(default=1)),
                ("cost", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("currency", models.CharField(default="RON", max_length=10)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("confirmed", "Confirmed"), ("paid", "Paid"), ("fulfilled", "Fulfilled"), ("delivered", "Delivered"), ("cancelled", "Cancelled"), ("returned", "Returned"), ("failed", "Failed")], default="submitted", max_length=30)),
                ("source_channel", models.CharField(default="whatsapp", max_length=30)),
                ("source_conversation_id", models.TextField(blank=True, null=True)),
                ("source_message_id", models.TextField(blank=True, null=True)),
                ("external_order_id", models.TextField(blank=True, null=True)),
                ("external_status", models.TextField(blank=True, null=True)),
                ("idempotency_key", models.TextField(unique=True)),
                ("webhook_url", models.TextField(blank=True, null=True)),
                ("webhook_http_status", models.IntegerField(blank=True, null=True)),
                ("customer_comment", models.TextField(blank=True, null=True)),
                ("order_payload", models.JSONField(blank=True, default=dict)),
                ("raw_response", models.TextField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "CRM Customer Order",
                "verbose_name_plural": "CRM Customer Orders",
                "db_table": "crm_customer_orders",
                "ordering": ["-submitted_at"],
            },
        ),
        migrations.CreateModel(
            name="CustomerConversionEvent",
            fields=[
                ("conversion_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(db_index=True)),
                ("communication_event_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("order_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("channel", models.CharField(choices=[("whatsapp", "WhatsApp"), ("sms", "SMS"), ("phone", "Phone"), ("email", "Email"), ("push", "Push notification"), ("web", "Web"), ("other", "Other")], max_length=30)),
                ("event_type", models.CharField(choices=[("sent", "Sent"), ("delivered", "Delivered"), ("opened", "Opened"), ("read", "Read"), ("clicked", "Clicked"), ("replied", "Replied"), ("lead", "Lead"), ("order_submitted", "Order submitted"), ("buy", "Buy"), ("paid", "Paid"), ("delivered_order", "Delivered order"), ("cancelled", "Cancelled"), ("returned", "Returned")], max_length=40)),
                ("product_id", models.TextField(blank=True, null=True)),
                ("campaign_id", models.TextField(blank=True, null=True)),
                ("conversation_id", models.TextField(blank=True, null=True)),
                ("value", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("currency", models.CharField(blank=True, max_length=10, null=True)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "verbose_name": "CRM Conversion Event",
                "verbose_name_plural": "CRM Conversion Events",
                "db_table": "crm_conversion_events",
                "ordering": ["-occurred_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="customerchannelidentity",
            constraint=models.UniqueConstraint(fields=("channel", "normalized_identifier"), name="uniq_crm_channel_identity"),
        ),
        migrations.AddConstraint(
            model_name="customercommunicationevent",
            constraint=models.UniqueConstraint(condition=models.Q(("provider_message_id__isnull", False)), fields=("provider", "provider_message_id"), name="uniq_crm_provider_message"),
        ),
        migrations.AddIndex(
            model_name="customerchannelidentity",
            index=models.Index(fields=["customer_id", "channel"], name="crm_channel_custome_b3367d_idx"),
        ),
        migrations.AddIndex(
            model_name="customerchannelidentity",
            index=models.Index(fields=["channel", "normalized_identifier"], name="crm_channel_channel_61c4e5_idx"),
        ),
        migrations.AddIndex(
            model_name="customercommunicationevent",
            index=models.Index(fields=["customer_id", "channel", "occurred_at"], name="crm_communi_custome_d131d1_idx"),
        ),
        migrations.AddIndex(
            model_name="customercommunicationevent",
            index=models.Index(fields=["conversation_id"], name="crm_communi_convers_d3dd9c_idx"),
        ),
        migrations.AddIndex(
            model_name="customercommunicationevent",
            index=models.Index(fields=["message_id"], name="crm_communi_message_c02477_idx"),
        ),
        migrations.AddIndex(
            model_name="customercommunicationevent",
            index=models.Index(fields=["event_type", "status"], name="crm_communi_event_t_802e68_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["customer_id", "submitted_at"], name="crm_custome_custome_6245ca_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["product_id", "submitted_at"], name="crm_custome_product_c6c868_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["source_conversation_id"], name="crm_custome_source__a432eb_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["status"], name="crm_custome_status_7c26b0_idx"),
        ),
        migrations.AddIndex(
            model_name="customerconversionevent",
            index=models.Index(fields=["customer_id", "event_type", "occurred_at"], name="crm_convers_custome_0145a7_idx"),
        ),
        migrations.AddIndex(
            model_name="customerconversionevent",
            index=models.Index(fields=["channel", "event_type"], name="crm_convers_channel_8fe18f_idx"),
        ),
        migrations.AddIndex(
            model_name="customerconversionevent",
            index=models.Index(fields=["product_id", "event_type"], name="crm_convers_product_393179_idx"),
        ),
        migrations.AddIndex(
            model_name="customerconversionevent",
            index=models.Index(fields=["conversation_id"], name="crm_convers_convers_68e6eb_idx"),
        ),
    ]
