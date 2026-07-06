# Generated manually for CRM customer segmentation on 2026-06-27.

import django.db.models.deletion
import django.utils.timezone
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0002_customer_crm"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerSegment",
            fields=[
                ("segment_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.TextField(unique=True)),
                ("slug", models.SlugField(max_length=120, unique=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("audience_type", models.CharField(choices=[("marketing", "Marketing"), ("transactional", "Transactional"), ("mixed", "Mixed"), ("suppression", "Suppression / do not contact")], default="marketing", max_length=30)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("paused", "Paused"), ("archived", "Archived")], default="draft", max_length=30)),
                ("is_dynamic", models.BooleanField(default=False)),
                ("country", models.CharField(blank=True, max_length=20, null=True)),
                ("channel", models.CharField(blank=True, max_length=30, null=True)),
                ("product_id", models.TextField(blank=True, null=True)),
                ("crm_stage", models.CharField(blank=True, max_length=40, null=True)),
                ("profile_status", models.CharField(blank=True, max_length=40, null=True)),
                ("rules", models.JSONField(blank=True, default=dict)),
                ("profile_count", models.IntegerField(default=0)),
                ("last_built_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.TextField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "CRM Customer Segment",
                "verbose_name_plural": "CRM Customer Segments",
                "db_table": "crm_customer_segments",
                "ordering": ["audience_type", "name"],
            },
        ),
        migrations.CreateModel(
            name="CustomerSegmentMembership",
            fields=[
                ("membership_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_id", models.UUIDField(db_index=True)),
                ("source", models.CharField(choices=[("manual", "Manual"), ("filtered_selection", "Filtered selection"), ("dynamic_rule", "Dynamic rule"), ("import", "Import"), ("system", "System")], default="manual", max_length=40)),
                ("status", models.CharField(choices=[("active", "Active"), ("excluded", "Excluded"), ("removed", "Removed")], default="active", max_length=30)),
                ("added_by", models.TextField(blank=True, null=True)),
                ("added_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("segment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="superchatsync.customersegment")),
            ],
            options={
                "verbose_name": "CRM Segment Membership",
                "verbose_name_plural": "CRM Segment Memberships",
                "db_table": "crm_customer_segment_memberships",
                "ordering": ["segment", "-added_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="customersegmentmembership",
            constraint=models.UniqueConstraint(fields=("segment", "customer_id"), name="uniq_crm_segment_customer"),
        ),
        migrations.AddIndex(
            model_name="customersegment",
            index=models.Index(fields=["audience_type", "status"], name="crm_custome_audien_6aa2df_idx"),
        ),
        migrations.AddIndex(
            model_name="customersegment",
            index=models.Index(fields=["country", "channel"], name="crm_custome_country_5750ab_idx"),
        ),
        migrations.AddIndex(
            model_name="customersegment",
            index=models.Index(fields=["product_id"], name="crm_custome_product_8205fc_idx"),
        ),
        migrations.AddIndex(
            model_name="customersegment",
            index=models.Index(fields=["crm_stage"], name="crm_custome_crm_sta_ec66e4_idx"),
        ),
        migrations.AddIndex(
            model_name="customersegmentmembership",
            index=models.Index(fields=["segment", "status"], name="crm_custome_segment_f6936b_idx"),
        ),
        migrations.AddIndex(
            model_name="customersegmentmembership",
            index=models.Index(fields=["customer_id", "status"], name="crm_custome_custome_a6b3dc_idx"),
        ),
    ]
