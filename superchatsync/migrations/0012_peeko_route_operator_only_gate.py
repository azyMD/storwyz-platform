from django.db import migrations


def set_peeko_route_operator_only_gate(apps, schema_editor):
    WhatsappAgentInboxRoute = apps.get_model("superchatsync", "WhatsappAgentInboxRoute")
    WhatsappAgentInboxRoute.objects.filter(
        channel_id="mc_nmzKNiUSx74GqKYluHIUq",
        agent_type="peeko_business_agent",
    ).update(require_handle_status=False)


def restore_peeko_route_agent_required(apps, schema_editor):
    WhatsappAgentInboxRoute = apps.get_model("superchatsync", "WhatsappAgentInboxRoute")
    WhatsappAgentInboxRoute.objects.filter(
        channel_id="mc_nmzKNiUSx74GqKYluHIUq",
        agent_type="peeko_business_agent",
    ).update(require_handle_status=True)


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0011_whatsapp_agent_inbox_routes"),
    ]

    operations = [
        migrations.RunPython(
            set_peeko_route_operator_only_gate,
            restore_peeko_route_agent_required,
        ),
    ]
