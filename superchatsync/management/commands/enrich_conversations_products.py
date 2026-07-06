import json
import re

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


MIN_SCORE = 20


def normalize_text(value):
    return str(value or "").lower()


def contains_phrase(text, phrase):
    phrase = str(phrase or "").strip().lower()
    if len(phrase) < 2:
        return False
    return phrase in text


def add_score(scores, reasons, product_id, points, reason):
    if not product_id or not points:
        return

    points = int(points)
    scores[product_id] = scores.get(product_id, 0) + points
    reasons.setdefault(product_id, []).append({
        "points": points,
        "reason": reason,
    })


def load_detection_data():
    with connection.cursor() as cur:
        cur.execute("""
            SELECT product_id, product_name, brand, category
            FROM products
            WHERE active = TRUE
        """)
        products = cur.fetchall()

        cur.execute("""
            SELECT product_id, offer_name, variant
            FROM offers
            WHERE active = TRUE
        """)
        offers = cur.fetchall()

        cur.execute("""
            SELECT product_id, keyword, match_type, weight
            FROM product_detection_rules
            WHERE active = TRUE
        """)
        rules = cur.fetchall()

    return products, offers, rules


def get_promotion_text(conversation_id):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT
                message_id,
                sender_type,
                message_text,
                sent_at
            FROM messages
            WHERE conversation_id = %s
            ORDER BY sent_at ASC NULLS LAST, created_at ASC
        """, [conversation_id])

        rows = cur.fetchall()

    if not rows:
        return "", {
            "source": "no_messages",
            "message_ids": [],
            "messages_used": 0,
        }

    outbound_types = {"campaign", "workflow", "operator", "system"}

    first_client_index = None

    for idx, row in enumerate(rows):
        _, sender_type, _, _ = row
        if sender_type == "client":
            first_client_index = idx
            break

    if first_client_index is not None:
        candidate_rows = rows[:first_client_index]
        source = "messages_before_first_client_reply"
    else:
        candidate_rows = rows
        source = "no_client_reply_first_outbound_messages"

    promotion_rows = []

    for message_id, sender_type, message_text, sent_at in candidate_rows:
        if sender_type in outbound_types and message_text:
            promotion_rows.append((message_id, sender_type, message_text, sent_at))

    if not promotion_rows:
        for message_id, sender_type, message_text, sent_at in rows:
            if sender_type != "client" and message_text:
                promotion_rows.append((message_id, sender_type, message_text, sent_at))
            if len(promotion_rows) >= 2:
                break
        source = "fallback_first_non_client_messages"

    promotion_rows = promotion_rows[:3]

    promotion_text = "\n\n".join([
        f"{sender_type}: {message_text}"
        for message_id, sender_type, message_text, sent_at in promotion_rows
    ])

    meta = {
        "source": source,
        "message_ids": [message_id for message_id, _, _, _ in promotion_rows if message_id],
        "messages_used": len(promotion_rows),
    }

    return promotion_text, meta


def detect_product_from_promotion_text(promotion_text, products, offers, rules):
    text = normalize_text(promotion_text)

    scores = {}
    reasons = {}

    for product_id, product_name, brand, category in products:
        # product_id este SKU intern. Nu îl căutăm în conversație.

        if contains_phrase(text, product_name):
            add_score(
                scores,
                reasons,
                product_id,
                150,
                f"product_name matched in promotion_text: {product_name}",
            )

        if brand and contains_phrase(text, brand):
            add_score(
                scores,
                reasons,
                product_id,
                40,
                f"brand matched in promotion_text: {brand}",
            )

        if category and contains_phrase(text, category):
            add_score(
                scores,
                reasons,
                product_id,
                10,
                f"category matched in promotion_text: {category}",
            )

    for product_id, offer_name, variant in offers:
        if contains_phrase(text, offer_name):
            add_score(
                scores,
                reasons,
                product_id,
                80,
                f"offer_name matched in promotion_text: {offer_name}",
            )

        if contains_phrase(text, variant):
            add_score(
                scores,
                reasons,
                product_id,
                50,
                f"variant matched in promotion_text: {variant}",
            )

    for product_id, keyword, match_type, weight in rules:
        keyword_text = str(keyword or "").strip()
        keyword_lower = keyword_text.lower()

        if not keyword_lower:
            continue

        matched = False

        if match_type == "exact":
            matched = re.search(rf"\b{re.escape(keyword_lower)}\b", text) is not None

        elif match_type == "regex":
            try:
                matched = re.search(keyword_text, promotion_text, flags=re.IGNORECASE) is not None
            except re.error:
                matched = False

        else:
            matched = keyword_lower in text

        if matched:
            add_score(
                scores,
                reasons,
                product_id,
                weight or 10,
                f"rule matched in promotion_text: {keyword_text}",
            )

    if not scores:
        return None, 0, {
            "all_scores": {},
            "reasons": [],
            "all_reasons": {},
        }

    best_product_id, best_score = max(scores.items(), key=lambda x: x[1])

    return best_product_id, best_score, {
        "all_scores": scores,
        "reasons": reasons.get(best_product_id, []),
        "all_reasons": reasons,
    }


def get_conversation_ids(run_id=None, force=False, limit=None):
    params = []

    if run_id:
        sql = """
            SELECT DISTINCT c.conversation_id
            FROM conversations c
            INNER JOIN superchat_sync_candidates sc
                ON sc.conversation_id = c.conversation_id
            WHERE sc.run_id = %s
        """
        params.append(run_id)

        if not force:
            sql += """
              AND (
                    c.enrichment_status IS NULL
                 OR c.enrichment_status = 'pending'
                 OR c.product_detected IS NULL
              )
            """

    else:
        sql = """
            SELECT c.conversation_id
            FROM conversations c
            WHERE 1=1
        """

        if not force:
            sql += """
              AND (
                    c.enrichment_status IS NULL
                 OR c.enrichment_status = 'pending'
                 OR c.product_detected IS NULL
              )
            """

    sql += " ORDER BY c.conversation_id"

    if limit:
        sql += " LIMIT %s"
        params.append(limit)

    with connection.cursor() as cur:
        cur.execute(sql, params)
        return [row[0] for row in cur.fetchall()]


class Command(BaseCommand):
    help = "Detect primary product for conversations using only promotion/opening messages."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=False)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--limit", type=int, required=False)

    def handle(self, *args, **options):
        run_id = options.get("run_id")
        force = options.get("force")
        limit = options.get("limit")

        products, offers, rules = load_detection_data()

        self.stdout.write(
            f"Loaded products={len(products)}, offers={len(offers)}, rules={len(rules)}"
        )

        conversation_ids = get_conversation_ids(run_id=run_id, force=force, limit=limit)

        total = len(conversation_ids)
        self.stdout.write(f"Conversations to enrich: {total}")

        processed = 0
        needs_review_count = 0

        for conversation_id in conversation_ids:
            promotion_text, promotion_meta = get_promotion_text(conversation_id)

            product_id, score, reason = detect_product_from_promotion_text(
                promotion_text,
                products,
                offers,
                rules,
            )

            needs_review = product_id is None or score < MIN_SCORE

            if needs_review:
                product_id = None
                needs_review_count += 1

            full_reason = {
                "detection_method": "promotion_text_only",
                "promotion_meta": promotion_meta,
                "promotion_text_preview": promotion_text[:1000],
                "score_details": reason,
            }

            with connection.cursor() as cur:
                cur.execute("""
                    UPDATE conversations
                    SET product_detected = %s,
                        product_detection_score = %s,
                        product_detection_reason = %s::jsonb,
                        product_detection_source = %s,
                        needs_review = %s,
                        enrichment_status = %s,
                        enriched_at = %s,
                        updated_at = NOW()
                    WHERE conversation_id = %s
                """, [
                    product_id,
                    score,
                    json.dumps(full_reason, ensure_ascii=False),
                    promotion_meta.get("source"),
                    needs_review,
                    "needs_review" if needs_review else "enriched",
                    timezone.now(),
                    conversation_id,
                ])

            processed += 1

            self.stdout.write(
                f"{conversation_id}: product={product_id}, score={score}, source={promotion_meta.get('source')}, needs_review={needs_review}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Product enrichment completed. processed={processed}, needs_review={needs_review_count}"
        ))
