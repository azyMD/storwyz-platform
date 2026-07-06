import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


CATEGORY_FILES = {
    "product_facts": "01_product_facts.jsonl",
    "offers": "02_offers.jsonl",
    "objection_rules": "03_objection_rules.jsonl",
    "sales_rules": "04_sales_rules.jsonl",
    "cross_sell_rules": "05_cross_sell_rules.jsonl",
    "product_faq": "06_product_faq.jsonl",
    "detection_keywords": "07_detection_keywords.jsonl",
    "conversation_examples": "08_conversation_examples.jsonl",
    "workflow_rules": "09_workflow_rules.jsonl",
}


def dictfetchall(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_import(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM product_knowledge_imports
            WHERE import_id = %s
        """, [str(import_id)])
        rows = dictfetchall(cur)

    return rows[0] if rows else None


def update_import(import_id, **fields):
    fields["updated_at"] = timezone.now()

    set_sql = ", ".join([f"{k} = %s" for k in fields.keys()])
    params = list(fields.values()) + [str(import_id)]

    with connection.cursor() as cur:
        cur.execute(f"""
            UPDATE product_knowledge_imports
            SET {set_sql}
            WHERE import_id = %s
        """, params)


def read_jsonl(path):
    if not path.exists():
        return []

    rows = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            rows.append(json.loads(line))

    return rows


def delete_existing_items(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            DELETE FROM product_knowledge_items
            WHERE import_id = %s
              AND status IN ('pending_review', 'rejected', 'error')
        """, [str(import_id)])


def clean(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_item(category, item):
    title = clean(item.get("title"))
    question = clean(item.get("question"))
    answer = clean(item.get("answer"))
    rule = clean(item.get("rule"))
    keyword = clean(item.get("keyword"))
    description = clean(item.get("description"))
    price = clean(item.get("price"))
    target_product_name = clean(item.get("target_product_name"))
    target_product_id = clean(item.get("target_product_id"))
    evidence = clean(item.get("evidence"))

    try:
        confidence_score = int(item.get("confidence_score") or 70)
    except Exception:
        confidence_score = 70

    try:
        priority = int(item.get("priority") or 50)
    except Exception:
        priority = 50

    if category == "product_facts":
        if not title:
            title = "Product fact"
        if not rule:
            rule = clean(f"{title}: {description or answer}")

    elif category == "offers":
        if not title:
            title = "Offer"
        if not rule:
            rule = clean(f"Ofertă: {title}. {description} Preț: {price}")

    elif category == "objection_rules":
        if not question:
            question = title
        if not answer:
            answer = description or rule
        if not rule:
            rule = clean(f"Pentru obiecția '{question}', răspunde: {answer}")

    elif category == "sales_rules":
        if not rule:
            rule = description or answer or title

    elif category == "cross_sell_rules":
        if not target_product_name:
            target_product_name = title
        if not rule:
            rule = clean(f"Recomandă cross-sell: {target_product_name}. {description} Preț: {price}")

    elif category == "product_faq":
        if not question:
            question = title
        if not answer:
            answer = description or rule

    elif category == "detection_keywords":
        if not keyword:
            keyword = title

    elif category == "conversation_examples":
        if not rule:
            rule = clean(f"Client: {question}\nRăspuns recomandat: {answer}")

    elif category == "workflow_rules":
        if not rule:
            rule = description or answer or title

    return {
        "title": title,
        "question": question,
        "answer": answer,
        "rule": rule,
        "keyword": keyword,
        "description": description,
        "price": price,
        "target_product_name": target_product_name,
        "target_product_id": target_product_id,
        "evidence": evidence,
        "confidence_score": confidence_score,
        "priority": priority,
    }


def insert_item(import_id, product_id, category, item):
    normalized = normalize_item(category, item)

    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO product_knowledge_items (
                import_id,
                product_id,
                category,
                title,
                question,
                answer,
                rule,
                keyword,
                description,
                price,
                target_product_name,
                target_product_id,
                evidence,
                confidence_score,
                priority,
                status,
                raw_payload,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'pending_review',
                %s::jsonb,
                NOW(),
                NOW()
            )
        """, [
            str(import_id),
            product_id,
            category,
            normalized["title"],
            normalized["question"],
            normalized["answer"],
            normalized["rule"],
            normalized["keyword"],
            normalized["description"],
            normalized["price"],
            normalized["target_product_name"],
            normalized["target_product_id"],
            normalized["evidence"],
            normalized["confidence_score"],
            normalized["priority"],
            json.dumps({
                "source": "knowledge_package",
                "category": category,
                "original_item": item,
            }, ensure_ascii=False),
        ])


class Command(BaseCommand):
    help = "Convert AI knowledge package JSONL files into Product Knowledge Items."

    def add_arguments(self, parser):
        parser.add_argument("--import-id", required=True)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        import_id = options["import_id"]
        force = options.get("force")

        item = get_import(import_id)

        if not item:
            raise CommandError(f"Importul nu există: {import_id}")

        product_id = item.get("product_id")
        package_dir = item.get("knowledge_package_dir")

        if not product_id:
            raise CommandError("Importul nu are product_id.")

        if not package_dir:
            raise CommandError("Importul nu are knowledge_package_dir.")

        package_path = Path(package_dir)

        if not package_path.exists():
            raise CommandError(f"Folderul package nu există: {package_path}")

        if force:
            delete_existing_items(import_id)

        inserted = 0
        total_read = 0

        for category, filename in CATEGORY_FILES.items():
            file_path = package_path / filename
            rows = read_jsonl(file_path)

            total_read += len(rows)
            category_inserted = 0

            for source_item in rows:
                insert_item(
                    import_id=import_id,
                    product_id=product_id,
                    category=category,
                    item=source_item,
                )
                inserted += 1
                category_inserted += 1

            self.stdout.write(f"{category}: read={len(rows)}, inserted={category_inserted}")

        update_import(
            import_id,
            package_suggestions_created_count=inserted,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Knowledge items created. total_read={total_read}, inserted={inserted}"
        ))
