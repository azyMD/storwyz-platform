import uuid

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone


SUPPORTED_TYPES = {
    "product_faq",
    "objection_rule",
    "sales_rule",
    "detection_keyword",
    "conversation_example",
    "workflow_fix",
}


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def table_exists(table_name):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = %s
            )
        """, [table_name])
        return cur.fetchone()[0]


def get_columns(table_name):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
        """, [table_name])
        return {row[0] for row in cur.fetchall()}


def insert_dynamic(table_name, values):
    if not table_exists(table_name):
        raise RuntimeError(f"Tabela nu există: {table_name}")

    columns = get_columns(table_name)

    insert_values = {
        key: value
        for key, value in values.items()
        if key in columns and value is not None
    }

    if not insert_values:
        raise RuntimeError(f"Nu există coloane compatibile pentru insert în {table_name}")

    col_sql = ", ".join(insert_values.keys())
    placeholders = ", ".join(["%s"] * len(insert_values))

    sql = f"""
        INSERT INTO {table_name} ({col_sql})
        VALUES ({placeholders})
    """

    with connection.cursor() as cur:
        cur.execute(sql, list(insert_values.values()))

    possible_id_fields = [
        "faq_id",
        "rule_id",
        "objection_id",
        "example_id",
        "fix_id",
        "id",
    ]

    for field in possible_id_fields:
        if field in insert_values:
            return str(insert_values[field])

    return None


def fetch_suggestions(limit=None, suggestion_ids=None):
    params = []

    sql = """
        SELECT
            suggestion_id,
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
            raw_payload
        FROM product_feed_suggestions
        WHERE status = 'approved'
    """

    if suggestion_ids:
        sql += " AND suggestion_id::text = ANY(%s)"
        params.append(suggestion_ids)

    sql += " ORDER BY confidence_score DESC NULLS LAST, created_at ASC"

    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    result = []

    for row in rows:
        result.append({
            "suggestion_id": row[0],
            "conversation_id": row[1],
            "analysis_id": row[2],
            "product_id": row[3],
            "suggestion_type": row[4],
            "title": row[5],
            "suggested_question": row[6],
            "suggested_answer": row[7],
            "suggested_rule": row[8],
            "suggested_keyword": row[9],
            "reason": row[10],
            "evidence": row[11],
            "confidence_score": row[12],
            "raw_payload": row[13],
        })

    return result


def build_target(s):
    suggestion_type = s["suggestion_type"]
    product_id = s["product_id"]
    conversation_id = s["conversation_id"]
    confidence_score = s["confidence_score"] or 0

    title = s["title"] or ""
    question = s["suggested_question"] or ""
    answer = s["suggested_answer"] or ""
    rule = s["suggested_rule"] or ""
    keyword = s["suggested_keyword"] or ""
    reason = s["reason"] or ""
    evidence = s["evidence"] or ""

    now = timezone.now()

    if suggestion_type == "product_faq":
        if not question and title:
            question = title

        if not answer and rule:
            answer = rule

        if not question or not answer:
            raise RuntimeError("product_faq are nevoie de question și answer.")

        return "product_faq", {
            "faq_id": new_id("faq"),
            "product_id": product_id,
            "question": question,
            "answer": answer,
            "source": "ai_suggestion",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    if suggestion_type == "objection_rule":
        objection_text = question or title or reason
        response_text = answer or rule

        if not objection_text or not response_text:
            raise RuntimeError("objection_rule are nevoie de objection și response.")

        return "objection_rules", {
            "rule_id": new_id("obj"),
            "objection_id": new_id("obj"),
            "product_id": product_id,
            "objection_type": title or "ai_detected_objection",
            "objection_text": objection_text,
            "response_text": response_text,
            "rule_text": response_text,
            "answer": response_text,
            "priority": 100,
            "source": "ai_suggestion",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    if suggestion_type == "sales_rule":
        rule_text = rule or answer or reason

        if not rule_text:
            raise RuntimeError("sales_rule are nevoie de rule_text.")

        return "product_sales_rules", {
            "rule_id": new_id("sales"),
            "product_id": product_id,
            "rule_type": title or "ai_sales_rule",
            "rule_text": rule_text,
            "priority": 100,
            "source": "ai_suggestion",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    if suggestion_type == "detection_keyword":
        if not keyword:
            keyword = title

        if not keyword:
            raise RuntimeError("detection_keyword are nevoie de keyword.")

        return "product_detection_rules", {
            "rule_id": new_id("detect"),
            "product_id": product_id,
            "keyword": keyword,
            "match_type": "contains",
            "weight": 30,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    if suggestion_type == "conversation_example":
        good_response = answer or rule

        return "conversation_examples", {
            "example_id": new_id("ex"),
            "product_id": product_id,
            "conversation_id": conversation_id,
            "scenario": title or "ai_conversation_example",
            "client_message": question,
            "bad_response": "",
            "good_response": good_response,
            "reason": reason,
            "evidence": evidence,
            "confidence_score": confidence_score,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    if suggestion_type == "workflow_fix":
        return "workflow_fixes", {
            "fix_id": new_id("fix"),
            "product_id": product_id,
            "conversation_id": conversation_id,
            "title": title or "AI workflow fix",
            "issue": reason,
            "recommended_fix": rule or answer,
            "evidence": evidence,
            "confidence_score": confidence_score,
            "active": True,
            "created_at": now,
            "updated_at": now,
        }

    raise RuntimeError(f"Tip de sugestie nesuportat: {suggestion_type}")


def mark_applied(suggestion_id, target_table, target_id):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'applied',
                applied_at = NOW(),
                applied_target_table = %s,
                applied_target_id = %s,
                apply_error = NULL,
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [target_table, target_id, suggestion_id])


def mark_error(suggestion_id, error):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET apply_error = %s,
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [str(error), suggestion_id])


class Command(BaseCommand):
    help = "Apply approved AI product feed suggestions into product feed tables."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, required=False)
        parser.add_argument("--suggestion-id", action="append", required=False)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        limit = options.get("limit")
        suggestion_ids = options.get("suggestion_id")
        dry_run = options.get("dry_run")

        suggestions = fetch_suggestions(
            limit=limit,
            suggestion_ids=suggestion_ids,
        )

        self.stdout.write(f"Approved suggestions to apply: {len(suggestions)}")
        self.stdout.write(f"Dry run: {dry_run}")

        applied = 0
        errors = 0

        for s in suggestions:
            sid = s["suggestion_id"]

            try:
                if s["suggestion_type"] not in SUPPORTED_TYPES:
                    raise RuntimeError(f"Unsupported suggestion_type: {s['suggestion_type']}")

                target_table, values = build_target(s)

                self.stdout.write(
                    f"{sid} | {s['suggestion_type']} -> {target_table} | product={s['product_id']}"
                )

                if dry_run:
                    self.stdout.write(f"DRY RUN VALUES: {values}")
                    continue

                with transaction.atomic():
                    target_id = insert_dynamic(target_table, values)
                    mark_applied(sid, target_table, target_id)

                applied += 1

            except Exception as e:
                errors += 1
                mark_error(sid, e)
                self.stdout.write(self.style.ERROR(f"ERROR {sid}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Apply completed. applied={applied}, errors={errors}, dry_run={dry_run}"
        ))
