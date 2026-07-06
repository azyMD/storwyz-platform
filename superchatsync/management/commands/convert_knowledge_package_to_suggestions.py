import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone


CATEGORY_MAP = {
    "product_facts": {
        "filename": "01_product_facts.jsonl",
        "suggestion_type": "sales_rule",
    },
    "offers": {
        "filename": "02_offers.jsonl",
        "suggestion_type": "sales_rule",
    },
    "objection_rules": {
        "filename": "03_objection_rules.jsonl",
        "suggestion_type": "objection_rule",
    },
    "sales_rules": {
        "filename": "04_sales_rules.jsonl",
        "suggestion_type": "sales_rule",
    },
    "cross_sell_rules": {
        "filename": "05_cross_sell_rules.jsonl",
        "suggestion_type": "sales_rule",
    },
    "product_faq": {
        "filename": "06_product_faq.jsonl",
        "suggestion_type": "product_faq",
    },
    "detection_keywords": {
        "filename": "07_detection_keywords.jsonl",
        "suggestion_type": "detection_keyword",
    },
    "conversation_examples": {
        "filename": "08_conversation_examples.jsonl",
        "suggestion_type": "conversation_example",
    },
    "workflow_rules": {
        "filename": "09_workflow_rules.jsonl",
        "suggestion_type": "workflow_fix",
    },
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

    items = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))

    return items


def delete_existing_package_suggestions(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            DELETE FROM product_feed_suggestions
            WHERE created_by = 'ai_knowledge_package'
              AND raw_payload->>'import_id' = %s
              AND status IN ('pending_review', 'rejected')
        """, [str(import_id)])


def exists_suggestion(product_id, suggestion_type, title, question, rule, keyword):
    checks = []

    if title and len(title.strip()) >= 8:
        checks.append(("title", title.strip()))

    if question and len(question.strip()) >= 8:
        checks.append(("suggested_question", question.strip()))

    if rule and len(rule.strip()) >= 12:
        checks.append(("suggested_rule", rule.strip()))

    if keyword and len(keyword.strip()) >= 3:
        checks.append(("suggested_keyword", keyword.strip()))

    if not checks:
        return False

    conditions = []
    params = [product_id, suggestion_type]

    for column, value in checks:
        conditions.append(f"LOWER(TRIM(COALESCE({column}, ''))) = LOWER(TRIM(%s))")
        params.append(value)

    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT 1
            FROM product_feed_suggestions
            WHERE product_id = %s
              AND suggestion_type = %s
              AND status IN ('pending_review', 'approved', 'applied')
              AND ({' OR '.join(conditions)})
            LIMIT 1
        """, params)

        return cur.fetchone() is not None


def normalize_item_to_suggestion(category_key, suggestion_type, item):
    title = item.get("title") or category_key

    question = item.get("question") or ""
    answer = item.get("answer") or ""
    rule = item.get("rule") or ""
    keyword = item.get("keyword") or ""
    description = item.get("description") or ""
    price = item.get("price") or ""

    if category_key == "product_facts":
        rule = rule or f"Product fact: {title}. {description}".strip()

    elif category_key == "offers":
        rule = rule or f"Offer: {title}. {description} Preț: {price}".strip()

    elif category_key == "cross_sell_rules":
        target = item.get("target_product_name") or title
        rule = rule or f"Cross-sell recomandat: {target}. {description} Preț: {price}".strip()

    elif category_key == "conversation_examples":
        rule = rule or f"Exemplu conversație: client='{question}' răspuns='{answer}'"

    elif category_key == "workflow_rules":
        rule = rule or description

    elif category_key == "sales_rules":
        rule = rule or description

    elif category_key == "objection_rules":
        question = question or title
        answer = answer or description
        rule = rule or f"Pentru obiecția '{question}', răspunde: {answer}"

    elif category_key == "product_faq":
        question = question or title
        answer = answer or description

    elif category_key == "detection_keywords":
        keyword = keyword or title

    return {
        "title": title,
        "suggested_question": question,
        "suggested_answer": answer,
        "suggested_rule": rule,
        "suggested_keyword": keyword,
        "reason": f"Extras din knowledge package categoria {category_key}.",
        "evidence": item.get("evidence") or "",
        "confidence_score": item.get("confidence_score") or 70,
    }


def insert_suggestion(import_id, product_id, package_dir, category_key, suggestion_type, item):
    normalized = normalize_item_to_suggestion(category_key, suggestion_type, item)

    confidence = int(normalized["confidence_score"] or 0)

    if confidence < 40:
        return False, "low_confidence"

    if exists_suggestion(
        product_id=product_id,
        suggestion_type=suggestion_type,
        title=normalized["title"],
        question=normalized["suggested_question"],
        rule=normalized["suggested_rule"],
        keyword=normalized["suggested_keyword"],
    ):
        return False, "duplicate"

    with connection.cursor() as cur:
        cur.execute("""
            INSERT INTO product_feed_suggestions (
                conversation_id,
                analysis_id,
                product_id,
                suggestion_type,
                title,
                suggested_question,
                suggested_answer,
                suggested_rule,
                suggested_keyword,
                reason,
                evidence,
                confidence_score,
                status,
                created_by,
                raw_payload,
                created_at,
                updated_at
            )
            VALUES (
                NULL,
                NULL,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'pending_review',
                'ai_knowledge_package',
                %s::jsonb,
                NOW(),
                NOW()
            )
        """, [
            product_id,
            suggestion_type,
            normalized["title"],
            normalized["suggested_question"],
            normalized["suggested_answer"],
            normalized["suggested_rule"],
            normalized["suggested_keyword"],
            normalized["reason"],
            normalized["evidence"],
            confidence,
            json.dumps({
                "source": "knowledge_package",
                "import_id": str(import_id),
                "package_dir": str(package_dir),
                "category": category_key,
                "original_item": item,
            }, ensure_ascii=False),
        ])

    return True, "inserted"


class Command(BaseCommand):
    help = "Convert AI knowledge package files to Product Feed Suggestions."

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
            raise CommandError("Importul nu are knowledge_package_dir. Rulează întâi create_product_knowledge_package.")

        package_path = Path(package_dir)

        if not package_path.exists():
            raise CommandError(f"Folderul package nu există: {package_path}")

        if force:
            delete_existing_package_suggestions(import_id)

        inserted = 0
        skipped = 0

        for category_key, cfg in CATEGORY_MAP.items():
            file_path = package_path / cfg["filename"]
            items = read_jsonl(file_path)

            for source_item in items:
                ok, reason = insert_suggestion(
                    import_id=import_id,
                    product_id=product_id,
                    package_dir=package_path,
                    category_key=category_key,
                    suggestion_type=cfg["suggestion_type"],
                    item=source_item,
                )

                if ok:
                    inserted += 1
                else:
                    skipped += 1

            self.stdout.write(f"{category_key}: read={len(items)}")

        update_import(
            import_id,
            package_suggestions_created_count=inserted,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Converted package to suggestions. inserted={inserted}, skipped={skipped}"
        ))
