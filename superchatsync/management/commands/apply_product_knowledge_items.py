import json
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


CATEGORY_TARGET_TABLE = {
    "product_facts": "product_sales_rules",
    "offers": "offers",
    "objection_rules": "objection_rules",
    "sales_rules": "product_sales_rules",
    "cross_sell_rules": "cross_sell_rules",
    "product_faq": "product_faq",
    "detection_keywords": "product_detection_rules",
    "conversation_examples": "conversation_examples",
    "workflow_rules": "workflow_fixes",
}


def dictfetchall(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def table_exists(table_name):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables WHERE table_name = %s
            )
        """, [table_name])
        return cur.fetchone()[0]


def get_columns(table_name):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT
                column_name,
                data_type,
                column_default,
                is_nullable
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
        """, [table_name])
        return dictfetchall(cur)


def get_primary_keys(table_name):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_name = %s
        """, [table_name])
        return [r[0] for r in cur.fetchall()]


def get_items(item_ids=None, import_id=None, approved_only=False):
    where = []
    params = []

    if item_ids:
        where.append("item_id = ANY(%s::uuid[])")
        params.append([str(x) for x in item_ids])

    if import_id:
        where.append("import_id = %s")
        params.append(str(import_id))

    if approved_only:
        where.append("status = 'approved'")
    else:
        where.append("status IN ('pending_review', 'approved')")

    if not where:
        raise CommandError("Trebuie --item-id sau --import-id.")

    sql = f"""
        SELECT *
        FROM product_knowledge_items
        WHERE {' AND '.join(where)}
        ORDER BY category, confidence_score DESC
    """

    with connection.cursor() as cur:
        cur.execute(sql, params)
        return dictfetchall(cur)


def update_item(item_id, **fields):
    set_sql = ", ".join([f"{k} = %s" for k in fields.keys()])
    params = list(fields.values()) + [str(item_id)]

    with connection.cursor() as cur:
        cur.execute(f"""
            UPDATE product_knowledge_items
            SET {set_sql}, updated_at = NOW()
            WHERE item_id = %s
        """, params)


def best_text(*values):
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def value_for_column(column, data_type, item):
    category = item.get("category")

    title = best_text(item.get("title"), item.get("question"), item.get("keyword"), category)
    question = best_text(item.get("question"), item.get("title"))
    answer = best_text(item.get("answer"), item.get("description"), item.get("rule"))
    rule = best_text(item.get("rule"), item.get("description"), item.get("answer"))
    keyword = best_text(item.get("keyword"), item.get("title"))
    description = best_text(item.get("description"), item.get("rule"), item.get("answer"))
    evidence = best_text(item.get("evidence"))
    price = best_text(item.get("price"))
    product_id = item.get("product_id")
    target_product_id = best_text(item.get("target_product_id"))
    target_product_name = best_text(item.get("target_product_name"), item.get("title"))
    confidence = item.get("confidence_score") or 70
    priority = item.get("priority") or confidence

    c = column.lower()

    if c in ["product_id", "main_product_id", "source_product_id", "from_product_id"]:
        return product_id

    if c in ["target_product_id", "recommended_product_id", "cross_sell_product_id", "to_product_id"]:
        return target_product_id or None

    if c in ["target_product_name", "cross_sell_name", "related_product_name"]:
        return target_product_name

    if c in ["title", "name", "rule_title", "offer_name", "faq_title"]:
        return title

    if c in ["question", "faq_question", "client_question", "suggested_question"]:
        return question

    if c in [
        "answer",
        "faq_answer",
        "response",
        "recommended_response",
        "recommended_answer",
        "suggested_answer",
        "good_response",
        "reply",
        "operator_response",
    ]:
        return answer or rule or description

    if c in ["objection", "objection_text", "client_objection", "objection_type"]:
        return question

    if c in [
        "rule",
        "rule_text",
        "sales_rule",
        "suggested_rule",
        "recommended_rule",
        "recommended_action",
        "action",
        "instruction",
        "operator_instruction",
        "script",
        "message_template",
    ]:
        return rule or answer or description

    if c in [
        "trigger",
        "condition",
        "when_to_use",
        "client_trigger",
        "scenario",
    ]:
        return title or question or keyword or category

    if c in ["keyword", "detection_keyword", "phrase"]:
        return keyword

    if c in ["description", "content", "text", "body", "notes", "offer_description"]:
        return description

    if c in ["price", "offer_price"]:
        return price

    if c in ["evidence", "source_text", "source_excerpt"]:
        return evidence

    if c in ["category", "rule_type", "item_type", "type"]:
        return category

    if c in ["priority", "weight", "score"]:
        return int(priority)

    if c in ["confidence", "confidence_score"]:
        return int(confidence)

    if c in ["match_type"]:
        return "contains"

    if c in ["status"]:
        return "active"

    if c in ["is_active", "active", "enabled"]:
        return True

    if c in ["created_by", "source", "created_source"]:
        return "knowledge_import"

    if c in ["raw_payload", "metadata"]:
        return json.dumps({
            "source": "product_knowledge_items",
            "item_id": str(item.get("item_id")),
            "category": category,
            "evidence": evidence,
        }, ensure_ascii=False)

    return None




def parse_number(value):
    if value is None:
        return None

    text = str(value).strip().replace(",", ".")

    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if not match:
        return None

    number = match.group(0)

    try:
        if "." in number:
            return float(number)
        return int(number)
    except Exception:
        return None


def adapt_value_for_db(value, data_type):
    if value is None or value == "":
        return None

    if data_type in [
        "numeric",
        "decimal",
        "double precision",
        "real",
        "integer",
        "bigint",
        "smallint",
    ]:
        return parse_number(value)

    return value


def insert_dynamic(table_name, item):
    if not table_exists(table_name):
        raise RuntimeError(f"Tabela țintă nu există: {table_name}")

    columns = get_columns(table_name)
    pk_cols = set(get_primary_keys(table_name))

    insert_cols = []
    placeholders = []
    values = []

    for col in columns:
        name = col["column_name"]
        data_type = col["data_type"]
        default = col["column_default"] or ""

        if name in pk_cols and name not in ["product_id", "source_product_id", "target_product_id"]:
            continue

        if name in ["created_at", "updated_at", "reviewed_at", "applied_at"]:
            insert_cols.append(name)
            placeholders.append("NOW()")
            continue

        if "gen_random_uuid" in default or "uuid_generate" in default or "nextval" in default:
            continue

        value = value_for_column(name, data_type, item)
        value = adapt_value_for_db(value, data_type)

        if value is None or value == "":
            continue

        insert_cols.append(name)

        if data_type in ["json", "jsonb"]:
            placeholders.append("%s::jsonb")
        else:
            placeholders.append("%s")

        values.append(value)

    if not insert_cols:
        raise RuntimeError(f"Nu am găsit coloane compatibile pentru tabela {table_name}")

    pk_return = ""
    returning_col = None

    for pk in pk_cols:
        if pk not in ["product_id", "source_product_id", "target_product_id"]:
            returning_col = pk
            pk_return = f" RETURNING {pk}"
            break

    sql = f"""
        INSERT INTO {table_name} ({', '.join(insert_cols)})
        VALUES ({', '.join(placeholders)})
        {pk_return}
    """

    with connection.cursor() as cur:
        cur.execute(sql, values)

        if returning_col:
            row = cur.fetchone()
            return str(row[0]) if row else None

    return None


def apply_item(item):
    category = item.get("category")
    item_id = item.get("item_id")

    table_name = CATEGORY_TARGET_TABLE.get(category)

    if not table_name:
        raise RuntimeError(f"Nu există mapping pentru categoria: {category}")

    target_id = insert_dynamic(table_name, item)

    update_item(
        item_id,
        status="applied",
        applied_target_table=table_name,
        applied_target_id=target_id,
        apply_error=None,
    )

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_knowledge_items
            SET applied_at = NOW(), updated_at = NOW()
            WHERE item_id = %s
        """, [str(item_id)])

    return table_name, target_id


class Command(BaseCommand):
    help = "Apply Product Knowledge Items directly to final product feed tables."

    def add_arguments(self, parser):
        parser.add_argument("--item-id", action="append")
        parser.add_argument("--import-id")
        parser.add_argument("--approved-only", action="store_true")

    def handle(self, *args, **options):
        item_ids = options.get("item_id")
        import_id = options.get("import_id")
        approved_only = options.get("approved_only")

        items = get_items(
            item_ids=item_ids,
            import_id=import_id,
            approved_only=approved_only,
        )

        if not items:
            self.stdout.write("No items to apply.")
            return

        applied = 0
        errors = 0

        for item in items:
            try:
                table_name, target_id = apply_item(item)
                applied += 1

                self.stdout.write(self.style.SUCCESS(
                    f"Applied {item['item_id']} → {table_name} / {target_id}"
                ))

            except Exception as e:
                errors += 1

                update_item(
                    item["item_id"],
                    status="error",
                    apply_error=str(e),
                )

                self.stdout.write(self.style.ERROR(
                    f"ERROR {item['item_id']}: {e}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Done. applied={applied}, errors={errors}"
        ))
