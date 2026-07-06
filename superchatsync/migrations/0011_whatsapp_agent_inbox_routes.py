import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def seed_whatsapp_agent_routes(apps, schema_editor):
    BusinessClient = apps.get_model("superchatsync", "BusinessClient")
    WhatsappAgentInboxRoute = apps.get_model("superchatsync", "WhatsappAgentInboxRoute")

    peeko = BusinessClient.objects.filter(slug="peeko").first()
    routes = [
        {
            "channel_id": "mc_BkixI64sb18mghwBXk7VO",
            "defaults": {
                "name": "ButchAxe WhatsApp Agent",
                "channel_phone": "+15559875079",
                "channel_phone_digits": "15559875079",
                "channel_name": "+15559875079",
                "inbox_id": "ib_zZUIzY4T8u9FZr4C9sTHT",
                "inbox_name": "Campaigns",
                "agent_type": "fitexpress_product_agent",
                "business": None,
                "default_product_id": "2757",
                "require_handle_status": True,
                "active": True,
                "metadata": {"seeded_from": "0011_whatsapp_agent_inbox_routes"},
            },
        },
        {
            "channel_id": "mc_nmzKNiUSx74GqKYluHIUq",
            "defaults": {
                "name": "Peeko WhatsApp Agent",
                "channel_phone": "+15559680919",
                "channel_phone_digits": "15559680919",
                "channel_name": "+15559680919",
                "inbox_id": "ib_zZUIzY4T8u9FZr4C9sTHT",
                "inbox_name": "Campaigns",
                "agent_type": "peeko_business_agent",
                "business": peeko,
                "default_product_id": "",
                "require_handle_status": True,
                "active": True,
                "metadata": {"seeded_from": "0011_whatsapp_agent_inbox_routes"},
            },
        },
    ]
    for item in routes:
        WhatsappAgentInboxRoute.objects.update_or_create(
            channel_id=item["channel_id"],
            defaults=item["defaults"],
        )


def unseed_whatsapp_agent_routes(apps, schema_editor):
    WhatsappAgentInboxRoute = apps.get_model("superchatsync", "WhatsappAgentInboxRoute")
    WhatsappAgentInboxRoute.objects.filter(
        channel_id__in=[
            "mc_BkixI64sb18mghwBXk7VO",
            "mc_nmzKNiUSx74GqKYluHIUq",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0010_shortlinks"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhatsappAgentInboxRoute",
            fields=[
                ("route_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.TextField()),
                ("channel_id", models.TextField(blank=True, null=True, unique=True)),
                ("channel_phone", models.TextField(blank=True, null=True)),
                ("channel_phone_digits", models.CharField(blank=True, max_length=32, null=True, unique=True)),
                ("channel_name", models.TextField(blank=True, null=True)),
                ("inbox_id", models.TextField(blank=True, null=True)),
                ("inbox_name", models.TextField(blank=True, null=True)),
                (
                    "agent_type",
                    models.CharField(
                        choices=[
                            ("fitexpress_product_agent", "Fitexpress product agent"),
                            ("peeko_business_agent", "Peeko business agent"),
                        ],
                        max_length=40,
                    ),
                ),
                ("default_product_id", models.TextField(blank=True, null=True)),
                ("require_handle_status", models.BooleanField(default=True)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="whatsapp_agent_routes",
                        to="superchatsync.businessclient",
                    ),
                ),
            ],
            options={
                "verbose_name": "WhatsApp Agent Inbox Route",
                "verbose_name_plural": "WhatsApp Agent Inbox Routes",
                "db_table": "whatsapp_agent_inbox_routes",
                "ordering": ["name"],
            },
        ),
        migrations.AddIndex(
            model_name="whatsappagentinboxroute",
            index=models.Index(fields=["active", "agent_type"], name="wa_route_active_agent_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappagentinboxroute",
            index=models.Index(fields=["inbox_id"], name="wa_route_inbox_idx"),
        ),
        migrations.RunPython(seed_whatsapp_agent_routes, unseed_whatsapp_agent_routes),
    ]
