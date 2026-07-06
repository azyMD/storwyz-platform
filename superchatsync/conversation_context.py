import json
import re
from difflib import SequenceMatcher

from django.db import connection, transaction
from django.utils import timezone

from superchatsync.models import Message


DIACRITIC_TRANSLATION = str.maketrans(
    "ăâîșțĂÂÎȘȚşţŞŢ",
    "aaistAAISTstST",
)
SIMILARITY_STOP_WORDS = {
    "a", "acum", "ai", "al", "ale", "am", "are", "cu", "ca", "care", "ce", "de",
    "din", "dori", "doriți", "este", "fi", "in", "în", "la", "mai", "o", "pe", "pentru",
    "pot", "sa", "să", "si", "și", "sunt", "un", "una", "va", "vă",
}

LANGUAGE_DEFINITIONS = {
    "ro": {
        "name": "Romanian",
        "instruction": "Write only in Romanian, matching the customer's tone.",
    },
    "ru": {
        "name": "Russian",
        "instruction": "Write only in Russian, using Cyrillic text.",
    },
    "uk": {
        "name": "Ukrainian",
        "instruction": "Write only in Ukrainian, using Cyrillic text.",
    },
    "en": {
        "name": "English",
        "instruction": "Write only in English.",
    },
    "es": {
        "name": "Spanish",
        "instruction": "Write only in Spanish.",
    },
    "it": {
        "name": "Italian",
        "instruction": "Write only in Italian.",
    },
    "fr": {
        "name": "French",
        "instruction": "Write only in French.",
    },
    "de": {
        "name": "German",
        "instruction": "Write only in German.",
    },
}

LANGUAGE_SIGNATURES = {
    "ro": {
        "chars": "ăâîșț",
        "words": {
            "salut", "buna", "bună", "vreau", "doresc", "comanda", "comandă",
            "pret", "preț", "livrare", "detalii", "multumesc", "mulțumesc",
            "cat", "cât", "adresa", "curier",
        },
    },
    "ru": {
        "chars": "",
        "words": {
            "привет", "здравствуйте", "хочу", "заказ", "заказать", "купить",
            "цена", "сколько", "стоит", "доставка", "подробнее", "спасибо",
            "адрес", "курьер",
        },
    },
    "uk": {
        "chars": "іїєґ",
        "words": {
            "привіт", "доброго", "хочу", "замовити", "купити", "ціна",
            "скільки", "коштує", "доставка", "детальніше", "дякую",
            "адреса", "кур'єр",
        },
    },
    "en": {
        "chars": "",
        "words": {
            "hello", "hi", "want", "order", "buy", "price", "cost", "delivery",
            "shipping", "details", "please", "thanks", "address", "courier",
        },
    },
    "es": {
        "chars": "ñáéíóú¿¡",
        "words": {
            "hola", "quiero", "pedido", "comprar", "precio", "cuanto", "cuánto",
            "entrega", "envio", "envío", "detalles", "gracias", "direccion",
            "dirección",
        },
    },
    "it": {
        "chars": "àèéìòù",
        "words": {
            "ciao", "voglio", "ordine", "ordinare", "comprare", "prezzo",
            "quanto", "costa", "consegna", "spedizione", "dettagli", "grazie",
            "indirizzo",
        },
    },
    "fr": {
        "chars": "àâçéèêëîïôùûüÿœ",
        "words": {
            "bonjour", "salut", "veux", "commande", "commander", "acheter",
            "prix", "combien", "livraison", "details", "détails", "merci",
            "adresse",
        },
    },
    "de": {
        "chars": "äöüß",
        "words": {
            "hallo", "möchte", "mochte", "bestellen", "kaufen", "preis",
            "kostet", "lieferung", "versand", "details", "danke", "adresse",
        },
    },
}

PHONE_COUNTRY_LANGUAGE_PREFIXES = (
    ("373", "MD", "Moldova", "ro"),
    ("40", "RO", "Romania", "ro"),
    ("380", "UA", "Ukraine", "uk"),
    ("7", "RU", "Russia/Kazakhstan", "ru"),
    ("420", "CZ", "Czech", "en"),
    ("34", "ES", "Spain", "es"),
    ("39", "IT", "Italy", "it"),
    ("33", "FR", "France", "fr"),
    ("49", "DE", "Germany", "de"),
    ("44", "GB", "United Kingdom", "en"),
    ("1", "US/CA", "United States/Canada", "en"),
    ("359", "BG", "Bulgaria", "en"),
    ("48", "PL", "Poland", "en"),
)


def _clean_text(value, limit=500):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _json_value(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, type(default)) else default
        except json.JSONDecodeError:
            return default
    return default


def infer_phone_country_language(phone):
    digits = re.sub(r"\D", "", str(phone or ""))
    for prefix, country_code, country_name, language_code in sorted(
        PHONE_COUNTRY_LANGUAGE_PREFIXES,
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if digits.startswith(prefix):
            return {
                "country_code": country_code,
                "country_name": country_name,
                "language_code": language_code if language_code in LANGUAGE_DEFINITIONS else "ro",
            }
    return {
        "country_code": "unknown",
        "country_name": "Unknown",
        "language_code": "ro",
    }


def _language_meta(code, confidence=0.0):
    code = code if code in LANGUAGE_DEFINITIONS else "ro"
    meta = LANGUAGE_DEFINITIONS[code]
    return {
        "code": code,
        "name": meta["name"],
        "instruction": meta["instruction"],
        "confidence": round(float(confidence or 0), 3),
    }


def _normalized_for_language(value):
    return str(value or "").casefold().translate(DIACRITIC_TRANSLATION)


def _word_hits(text, words):
    hits = 0
    for word in words:
        pattern = rf"(?<!\w){re.escape(_normalized_for_language(word))}(?!\w)"
        if re.search(pattern, text):
            hits += 1
    return hits


def detect_language_from_texts(texts, fallback="ro"):
    values = [_clean_text(text, limit=1000) for text in (texts or []) if _clean_text(text)]
    if not values:
        return _language_meta(fallback, 0)

    combined_raw = " ".join(values).casefold()
    combined = _normalized_for_language(combined_raw)
    scores = {code: 0.0 for code in LANGUAGE_DEFINITIONS}

    cyrillic_count = len(re.findall(r"[а-яёіїєґ]", combined_raw, flags=re.IGNORECASE))
    latin_count = len(re.findall(r"[a-zăâîșțñáéíóúàèêëïôùûüäöß]", combined_raw, flags=re.IGNORECASE))
    if cyrillic_count:
        if re.search(r"[іїєґ]", combined_raw, flags=re.IGNORECASE):
            scores["uk"] += 8
        else:
            scores["ru"] += 6
    if cyrillic_count >= max(4, latin_count):
        scores["ru"] += 3
        scores["uk"] += 2

    for code, signature in LANGUAGE_SIGNATURES.items():
        char_hits = sum(combined_raw.count(char) for char in signature.get("chars", ""))
        scores[code] += min(char_hits, 6) * 1.5
        scores[code] += _word_hits(combined, signature.get("words", set())) * 2

    if re.search(r"\b(?:ok|yes|no|hello|price|order|delivery)\b", combined):
        scores["en"] += 1

    best_code, best_score = max(scores.items(), key=lambda item: item[1])
    fallback = fallback if fallback in LANGUAGE_DEFINITIONS else "ro"
    if best_score < 2:
        return _language_meta(fallback, best_score)
    return _language_meta(best_code, best_score)


def _stage_for_intent(intent):
    return {
        "asks_offer": "offer_consideration",
        "asks_price": "offer_consideration",
        "asks_product_details": "product_evaluation",
        "asks_corrosion": "product_evaluation",
        "asks_delivery": "logistics",
        "wants_to_order": "order_collection",
    }.get(intent, "product_discovery")


def _next_action_for_intent(intent):
    return {
        "asks_offer": "choose_offer",
        "asks_price": "choose_offer",
        "asks_product_details": "present_relevant_benefit",
        "asks_corrosion": "answer_quality_question",
        "asks_delivery": "confirm_delivery_then_order",
        "wants_to_order": "collect_order_data",
    }.get(intent, "clarify_need")


def _journey_policy(intent, client_turns, assistant_turns, persisted_stage=None):
    engagement_depth = min(10, max(client_turns or 0, assistant_turns or 0))
    explicit_offer = intent in {"asks_price", "asks_offer"}
    order_intent = intent == "wants_to_order"
    repeated_engagement = engagement_depth >= 3

    if order_intent:
        stage = "action_lead"
    elif explicit_offer:
        stage = "desire_offer"
    elif repeated_engagement:
        stage = "desire_building"
    elif engagement_depth <= 1:
        stage = "attention"
    else:
        stage = "interest_discovery"

    if persisted_stage == "order_pending":
        stage = "action_lead"

    price_exposure_allowed = bool(explicit_offer or order_intent or repeated_engagement)
    allowed_cta_intents = ["usage", "benefits", "details", "quality", "delivery"]
    blocked_cta_intents = []

    if explicit_offer:
        allowed_cta_intents.extend(["offer", "price", "quantity"])
        blocked_cta_intents.append("order")
    elif order_intent:
        allowed_cta_intents.extend(["offer", "price", "order", "quantity"])
    elif repeated_engagement:
        allowed_cta_intents.append("offer")
        blocked_cta_intents.extend(["price", "order", "quantity"])
    else:
        blocked_cta_intents.extend(["offer", "price", "order", "quantity"])

    return {
        "journey_stage": stage,
        "engagement_depth": engagement_depth,
        "price_exposure_allowed": price_exposure_allowed,
        "allowed_cta_intents": allowed_cta_intents,
        "blocked_cta_intents": blocked_cta_intents,
    }


def _topics_in_text(text):
    value = str(text or "").lower()
    topics = []
    patterns = {
        "price_offer": r"\b(?:ron|lei|preț|pret|costă|costa|ofert|reducere|price|cost|offer|discount|цена|скид|precio|prezzo|prix)",
        "delivery": r"\b(?:livr|curier|transport|sms|delivery|shipping|courier|достав|курьер|env[ií]o|consegna|livraison)",
        "quality_material": r"\b(?:oțel|otel|rockwell|duritate|calitate|material|steel|hardness|quality|сталь|тверд|materiale|qualit)",
        "corrosion": r"\b(?:corozi|rugin|oxid|corrosion|rust|корроз|ржав)",
        "order": r"\b(?:comand|adres|telefon|nume|înregistr|order|address|phone|заказ|адрес|телефон)",
        "usage": r"\b(?:folos|utiliz|carne|grătar|gratar|bucătărie|bucatarie|use|usage|kitchen|использ|кухн)",
    }
    for topic, pattern in patterns.items():
        if re.search(pattern, value, flags=re.IGNORECASE):
            topics.append(topic)
    return topics


DETAIL_FLOW_RE = re.compile(
    r"\b(?:detalii|mai\s+multe|cum\s+se\s+folose|folos|utiliz|"
    r"gr[aă]tar|buc[aă]t[aă]rie|cadou|carne|material|calitate|duritate|"
    r"details|more\s+details|how\s+to\s+use|usage|grill|grilling|kitchen|gift|"
    r"parrilla|cocina|regalo|подроб|детал|использ|грил|кухн|подар|"
    r"dettagli|détails)\b",
    flags=re.IGNORECASE,
)
USE_CONTEXT_RE = re.compile(
    r"\b(?:gr[aă]tar|grill|grilling|parrilla|"
    r"buc[aă]t[aă]rie|kitchen|cocina|cucina|"
    r"cadou|gift|regalo|грил|кухн|подар)\b",
    flags=re.IGNORECASE,
)


def _selected_use_context_from_text(text):
    value = str(text or "").casefold()
    if re.search(r"\b(gr[aă]tar|grill|grilling|parrilla|грил)", value, flags=re.IGNORECASE):
        return "grill"
    if re.search(r"\b(buc[aă]t[aă]rie|kitchen|cocina|cucina|кухн)", value, flags=re.IGNORECASE):
        return "kitchen"
    if re.search(r"\b(cadou|gift|regalo|подар)", value, flags=re.IGNORECASE):
        return "gift"
    return None


def _flow_metrics(client_texts):
    detail_path_count = 0
    use_context_count = 0
    selected_use_context = None

    for text in client_texts[-10:]:
        value = str(text or "")
        if DETAIL_FLOW_RE.search(value):
            detail_path_count += 1
        use_context = _selected_use_context_from_text(value)
        if use_context:
            use_context_count += 1
            selected_use_context = use_context

    latest_text = client_texts[-1] if client_texts else ""
    current_use_context = _selected_use_context_from_text(latest_text)
    details_path_exhausted = detail_path_count >= 3
    return {
        "detail_path_count": detail_path_count,
        "use_context_count": use_context_count,
        "selected_use_context": selected_use_context,
        "current_use_context": current_use_context,
        "details_path_exhausted": details_path_exhausted,
    }


def _load_persisted_state(conversation_id):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT sales_stage, intent, cta_history, next_best_action, raw_state
            FROM ai_sales_conversation_state
            WHERE conversation_id = %s
            """,
            [conversation_id],
        )
        row = cursor.fetchone()
    if not row:
        return {}
    return {
        "sales_stage": row[0],
        "intent": row[1],
        "cta_history": _json_value(row[2], []),
        "next_best_action": row[3],
        "raw_state": _json_value(row[4], {}),
    }


def _last_buttons(cta_history):
    if not cta_history:
        return []
    last = cta_history[-1]
    if isinstance(last, dict):
        return list(last.get("buttons") or [])
    if isinstance(last, list):
        return list(last)
    return []


def build_conversation_context(conversation, intent, client_message, limit=12):
    recent = list(
        Message.objects.filter(conversation_id=conversation.conversation_id)
        .exclude(message_text__isnull=True)
        .exclude(message_text="")
        .order_by("-sent_at")[:limit]
    )
    recent.reverse()

    dialogue = []
    previous_replies = []
    client_texts = []
    for message in recent:
        role = "client" if message.is_client_reply else "assistant"
        text = _clean_text(message.message_text)
        dialogue.append(
            {
                "role": role,
                "text": text,
                "sent_at": message.sent_at.isoformat() if message.sent_at else None,
            }
        )
        if role == "client":
            client_texts.append(text)
        if role == "assistant" and len(text) >= 20:
            previous_replies.append(text)

    persisted = _load_persisted_state(conversation.conversation_id)
    raw_state = persisted.get("raw_state") or {}
    for reply in raw_state.get("sent_replies") or []:
        cleaned = _clean_text(reply)
        if cleaned and cleaned not in previous_replies:
            previous_replies.append(cleaned)

    previous_replies = previous_replies[-8:]
    answered_topics = sorted(
        {
            topic
            for reply in previous_replies
            for topic in _topics_in_text(reply)
        }
    )
    cta_history = persisted.get("cta_history") or []
    flow_metrics = _flow_metrics(client_texts)
    phone_locale = infer_phone_country_language(conversation.client_phone)
    language = detect_language_from_texts(
        client_texts + [_clean_text(client_message)],
        fallback=(raw_state.get("language_code") or phone_locale["language_code"] or "ro"),
    )
    client_turns = len(client_texts)
    assistant_turns = len(previous_replies)
    journey = _journey_policy(
        intent,
        client_turns,
        assistant_turns,
        persisted_stage=persisted.get("sales_stage"),
    )
    sales_stage = journey["journey_stage"]
    next_best_action = _next_action_for_intent(intent)

    summary_parts = [f"Etapă: {sales_stage}."]
    summary_parts.append(f"Depth: {journey['engagement_depth']}.")
    summary_parts.append(f"Detalii path: {flow_metrics['detail_path_count']}.")
    if not journey["price_exposure_allowed"]:
        summary_parts.append("Nu expune prețul/oferta încă; continuă discovery și dorință.")
    if flow_metrics["details_path_exhausted"]:
        summary_parts.append("Ramura de detalii a atins limita; mută CTA-ul spre progres.")
    if phone_locale["country_code"] != "unknown":
        summary_parts.append(f"Țara telefonului: {phone_locale['country_name']} ({phone_locale['country_code']}).")
    summary_parts.append(f"Limba clientului: {language['name']}.")
    if answered_topics:
        summary_parts.append("Deja discutat: " + ", ".join(answered_topics) + ".")
    if _last_buttons(cta_history):
        summary_parts.append("Ultimele CTA au fost deja afișate.")

    return {
        "conversation_id": conversation.conversation_id,
        "sales_stage": sales_stage,
        "journey_stage": journey["journey_stage"],
        "engagement_depth": journey["engagement_depth"],
        "price_exposure_allowed": journey["price_exposure_allowed"],
        "allowed_cta_intents": journey["allowed_cta_intents"],
        "blocked_cta_intents": journey["blocked_cta_intents"],
        "detail_path_count": flow_metrics["detail_path_count"],
        "use_context_count": flow_metrics["use_context_count"],
        "selected_use_context": flow_metrics["selected_use_context"],
        "current_use_context": flow_metrics["current_use_context"],
        "details_path_exhausted": flow_metrics["details_path_exhausted"],
        "intent": intent,
        "next_best_action": next_best_action,
        "summary": " ".join(summary_parts),
        "language_code": language["code"],
        "language_name": language["name"],
        "language_confidence": language["confidence"],
        "language_instruction": language["instruction"],
        "phone_country_code": phone_locale["country_code"],
        "phone_country_name": phone_locale["country_name"],
        "phone_default_language_code": phone_locale["language_code"],
        "current_client_message": _clean_text(client_message),
        "recent_dialogue": dialogue,
        "previous_assistant_replies": previous_replies,
        "answered_topics": answered_topics,
        "cta_history": cta_history[-5:],
        "last_buttons": _last_buttons(cta_history),
        "sent_creative_ids": list(raw_state.get("sent_creative_ids") or [])[-10:],
    }


def _similarity_tokens(text):
    words = re.findall(r"[\wăâîșț]+", str(text or "").lower())
    return {word for word in words if len(word) > 2 and word not in SIMILARITY_STOP_WORDS}


def evaluate_repetition(body, context):
    candidate = _clean_text(body, limit=2000).lower()
    candidate_tokens = _similarity_tokens(candidate)
    best = {"repeated": False, "score": 0.0, "sequence": 0.0, "jaccard": 0.0}

    for previous in context.get("previous_assistant_replies") or []:
        prior = _clean_text(previous, limit=2000).lower()
        prior_tokens = _similarity_tokens(prior)
        sequence = SequenceMatcher(None, candidate, prior).ratio()
        union = candidate_tokens | prior_tokens
        jaccard = len(candidate_tokens & prior_tokens) / len(union) if union else 0.0
        score = max(sequence, jaccard)
        if score > best["score"]:
            best = {
                "repeated": sequence >= 0.82 or jaccard >= 0.78,
                "score": round(score, 3),
                "sequence": round(sequence, 3),
                "jaccard": round(jaccard, 3),
            }

    return best


def context_for_prompt(context):
    return {
        "summary": context.get("summary"),
        "language_code": context.get("language_code") or "ro",
        "language_name": context.get("language_name") or "Romanian",
        "language_instruction": context.get("language_instruction") or LANGUAGE_DEFINITIONS["ro"]["instruction"],
        "language_confidence": context.get("language_confidence") or 0,
        "phone_country_code": context.get("phone_country_code") or "unknown",
        "phone_country_name": context.get("phone_country_name") or "Unknown",
        "phone_default_language_code": context.get("phone_default_language_code") or "ro",
        "sales_stage": context.get("sales_stage"),
        "journey_stage": context.get("journey_stage") or context.get("sales_stage"),
        "engagement_depth": context.get("engagement_depth") or 0,
        "price_exposure_allowed": bool(context.get("price_exposure_allowed")),
        "allowed_cta_intents": context.get("allowed_cta_intents") or [],
        "blocked_cta_intents": context.get("blocked_cta_intents") or [],
        "detail_path_count": context.get("detail_path_count") or 0,
        "use_context_count": context.get("use_context_count") or 0,
        "selected_use_context": context.get("selected_use_context"),
        "current_use_context": context.get("current_use_context"),
        "details_path_exhausted": bool(context.get("details_path_exhausted")),
        "next_best_action": context.get("next_best_action"),
        "answered_topics": context.get("answered_topics") or [],
        "last_buttons": context.get("last_buttons") or [],
        "recent_dialogue": (context.get("recent_dialogue") or [])[-8:],
        "previous_assistant_replies": (context.get("previous_assistant_replies") or [])[-4:],
    }


def record_client_message(conversation_id, product_id, body):
    now = timezone.now()
    body = _clean_text(body, limit=4000)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai_chat_sessions (
                    conversation_id, product_id, status, ai_turn_count, user_turn_count,
                    repeat_count, last_client_message, created_at, updated_at
                ) VALUES (%s, %s, 'active', 0, 1, 0, %s, %s, %s)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    product_id = EXCLUDED.product_id,
                    user_turn_count = ai_chat_sessions.user_turn_count + 1,
                    last_client_message = EXCLUDED.last_client_message,
                    updated_at = EXCLUDED.updated_at
                """,
                [conversation_id, str(product_id), body, now, now],
            )
            cursor.execute(
                """
                INSERT INTO ai_chat_transcript (conversation_id, role, message_text, source, created_at)
                VALUES (%s, 'client', %s, 'superchat_test_webhook', %s)
                """,
                [conversation_id, body, now],
            )


def record_sent_response(conversation_id, product_id, body, buttons, creative_asset_id=None, intent=None):
    now = timezone.now()
    body = _clean_text(body, limit=4000)
    buttons = [str(button).strip()[:40] for button in (buttons or []) if str(button).strip()][:3]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai_chat_sessions (
                    conversation_id, product_id, status, ai_turn_count, user_turn_count,
                    repeat_count, last_ai_reply, created_at, updated_at
                ) VALUES (%s, %s, 'active', 1, 0, 0, %s, %s, %s)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    product_id = EXCLUDED.product_id,
                    ai_turn_count = ai_chat_sessions.ai_turn_count + 1,
                    last_ai_reply = EXCLUDED.last_ai_reply,
                    updated_at = EXCLUDED.updated_at
                """,
                [conversation_id, str(product_id), body, now, now],
            )
            cursor.execute(
                """
                INSERT INTO ai_chat_transcript (conversation_id, role, message_text, source, created_at)
                VALUES (%s, 'ai', %s, 'safe_test_sender', %s)
                """,
                [conversation_id, body, now],
            )
            cursor.execute(
                """
                SELECT cta_history, raw_state
                FROM ai_sales_conversation_state
                WHERE conversation_id = %s
                FOR UPDATE
                """,
                [conversation_id],
            )
            row = cursor.fetchone()
            cta_history = _json_value(row[0], []) if row else []
            raw_state = _json_value(row[1], {}) if row else {}
            cta_history.append({"buttons": buttons, "sent_at": now.isoformat()})
            sent_replies = list(raw_state.get("sent_replies") or [])
            sent_replies.append(body)
            raw_state["sent_replies"] = sent_replies[-8:]
            sent_creatives = list(raw_state.get("sent_creative_ids") or [])
            if creative_asset_id and str(creative_asset_id) not in sent_creatives:
                sent_creatives.append(str(creative_asset_id))
            raw_state["sent_creative_ids"] = sent_creatives[-12:]
            raw_state["last_sent_at"] = now.isoformat()

            cursor.execute(
                """
                INSERT INTO ai_sales_conversation_state (
                    conversation_id, sales_stage, intent, decision_readiness,
                    friction_points, information_gap, known_customer_data,
                    missing_order_data, product_focus, cta_history,
                    next_best_action, raw_state, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, NULL, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                    '[]'::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (conversation_id) DO UPDATE SET
                    sales_stage = EXCLUDED.sales_stage,
                    intent = EXCLUDED.intent,
                    product_focus = EXCLUDED.product_focus,
                    cta_history = EXCLUDED.cta_history,
                    next_best_action = EXCLUDED.next_best_action,
                    raw_state = EXCLUDED.raw_state,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    conversation_id,
                    _stage_for_intent(intent),
                    intent,
                    str(product_id),
                    json.dumps(cta_history, ensure_ascii=False),
                    _next_action_for_intent(intent),
                    json.dumps(raw_state, ensure_ascii=False),
                    now,
                    now,
                ],
            )
