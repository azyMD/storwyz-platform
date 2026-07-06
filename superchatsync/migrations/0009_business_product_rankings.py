import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("superchatsync", "0008_business_clients_knowledge"),
    ]

    operations = [
        migrations.CreateModel(
            name="BusinessProductRanking",
            fields=[
                ("ranking_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "rank_type",
                    models.CharField(
                        choices=[
                            ("best_seller", "Best seller"),
                            ("trending", "Trending"),
                            ("category_trending", "Category trending"),
                        ],
                        default="best_seller",
                        max_length=40,
                    ),
                ),
                ("collection_slug", models.SlugField(blank=True, max_length=220, null=True)),
                ("collection_title", models.TextField(blank=True, null=True)),
                ("source_url", models.TextField(blank=True, null=True)),
                ("rank", models.PositiveIntegerField(default=0)),
                ("score", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("active", models.BooleanField(default=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("first_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_rankings",
                        to="superchatsync.businessclient",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rankings",
                        to="superchatsync.businessproduct",
                    ),
                ),
            ],
            options={
                "verbose_name": "Business Product Ranking",
                "verbose_name_plural": "Business Product Rankings",
                "db_table": "business_product_rankings",
                "ordering": ["business", "rank_type", "rank", "product__name"],
            },
        ),
        migrations.AddConstraint(
            model_name="businessproductranking",
            constraint=models.UniqueConstraint(
                fields=("business", "product", "rank_type", "collection_slug"),
                name="uniq_business_product_ranking",
            ),
        ),
        migrations.AddIndex(
            model_name="businessproductranking",
            index=models.Index(fields=["business", "rank_type", "active"], name="superchats_business_b39157_idx"),
        ),
        migrations.AddIndex(
            model_name="businessproductranking",
            index=models.Index(fields=["business", "collection_slug", "active"], name="superchats_business_de4fc5_idx"),
        ),
        migrations.AddIndex(
            model_name="businessproductranking",
            index=models.Index(fields=["product", "active"], name="superchats_product_891ed4_idx"),
        ),
        migrations.AddIndex(
            model_name="businessproductranking",
            index=models.Index(fields=["rank"], name="superchats_rank_f8f501_idx"),
        ),
    ]
