import uuid

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("superchatsync", "0006_fitexpress_order_snapshots"),
    ]

    operations = [
        migrations.CreateModel(
            name="CurrencyMonthlyRate",
            fields=[
                ("rate_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("currency", models.CharField(max_length=10)),
                ("month", models.DateField()),
                ("units_per_eur", models.DecimalField(decimal_places=8, max_digits=20)),
                ("rate_to_eur", models.DecimalField(decimal_places=10, max_digits=20)),
                ("source", models.TextField(default="frankfurter_ecb_monthly_average")),
                ("source_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "CRM Currency Monthly Rate",
                "verbose_name_plural": "CRM Currency Monthly Rates",
                "db_table": "crm_currency_monthly_rates",
                "ordering": ["-month", "currency"],
            },
        ),
        migrations.AddField(
            model_name="customerorder",
            name="cost_eur_estimate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="customerorder",
            name="eur_exchange_rate",
            field=models.DecimalField(blank=True, decimal_places=8, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="customerorder",
            name="exchange_rate_month",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customerorder",
            name="exchange_rate_source",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="currencymonthlyrate",
            constraint=models.UniqueConstraint(fields=("currency", "month"), name="uniq_crm_currency_month"),
        ),
        migrations.AddIndex(
            model_name="currencymonthlyrate",
            index=models.Index(fields=["currency", "month"], name="crm_fx_currency_month_idx"),
        ),
        migrations.AddIndex(
            model_name="currencymonthlyrate",
            index=models.Index(fields=["month"], name="crm_fx_month_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["currency", "submitted_at"], name="crm_order_currency_month_idx"),
        ),
        migrations.AddIndex(
            model_name="customerorder",
            index=models.Index(fields=["cost_eur_estimate"], name="crm_order_cost_eur_idx"),
        ),
    ]
