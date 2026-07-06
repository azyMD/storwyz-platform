from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "List creative assets for a product."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", required=True)
        parser.add_argument("--active-only", action="store_true")

    def handle(self, *args, **options):
        product_id = str(options["product_id"]).strip()
        active_only = options["active_only"]

        where = "WHERE product_id = %s"
        params = [product_id]

        if active_only:
            where += " AND is_active = TRUE"

        with connection.cursor() as cur:
            cur.execute(f"""
                SELECT
                    asset_id,
                    asset_type,
                    title,
                    description,
                    usage_context,
                    sales_stage,
                    intent,
                    next_best_action,
                    tags,
                    priority,
                    public_url,
                    is_active,
                    created_at
                FROM product_creative_assets
                {where}
                ORDER BY is_active DESC, priority ASC, created_at DESC
            """, params)

            rows = cur.fetchall()
            cols = [c[0] for c in cur.description]

        if not rows:
            self.stdout.write("No creative assets found.")
            return

        for row in rows:
            item = dict(zip(cols, row))
            self.stdout.write("-" * 80)
            for key, value in item.items():
                self.stdout.write(f"{key}: {value}")
