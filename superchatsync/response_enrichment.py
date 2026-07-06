import re

from django.db import connection

from superchatsync.models import ProductCreativeAsset


NO_CREATIVE_INTENTS = {"asks_delivery", "wants_to_order"}
LANGUAGE_ALIASES = {
    "ro": {"ro", "ro-ro", "romanian", "romana", "română", "roumanian"},
    "ru": {"ru", "ru-ru", "russian", "rusa", "rusă", "русский"},
    "uk": {"uk", "ua", "uk-ua", "ukrainian", "ucraineana", "українська"},
    "en": {"en", "en-us", "en-gb", "english", "eng"},
    "es": {"es", "es-es", "spanish", "español", "esp"},
    "it": {"it", "it-it", "italian", "italiano"},
    "fr": {"fr", "fr-fr", "french", "français", "francais"},
    "de": {"de", "de-de", "german", "deutsch"},
}
NEUTRAL_LANGUAGE_MARKERS = {"all", "any", "neutral", "universal", "no_text", "notext", "multilang"}

CTA_LABELS = {
    "ro": {
        "want_2": "Vreau 2 bucăți",
        "want_premium": "Vreau premium",
        "choose_offer": "Vreau oferta recomandată",
        "more_details": "Mai multe detalii",
        "compare": "Compară variantele",
        "delivery_details": "Detalii livrare",
        "order_now": "Comandă acum",
        "see_offer": "Vezi oferta",
        "not_now": "Nu acum",
        "question": "Vreau comanda",
        "product_details": "Detalii produs",
        "confirm_order": "Confirm comanda",
        "usage": "Cum se folosește?",
        "use_grill": "Pentru grătar",
        "use_kitchen": "Pentru bucătărie",
        "use_gift": "Pentru cadou",
    },
    "en": {
        "want_2": "I want 2 pcs",
        "want_premium": "I want premium",
        "choose_offer": "I want recommended offer",
        "more_details": "More details",
        "compare": "Compare options",
        "delivery_details": "Delivery details",
        "order_now": "Order now",
        "see_offer": "See offer",
        "not_now": "Not now",
        "question": "I want to order",
        "product_details": "Product details",
        "confirm_order": "Confirm order",
        "usage": "How to use?",
        "use_grill": "For grilling",
        "use_kitchen": "For kitchen",
        "use_gift": "As a gift",
    },
    "ru": {
        "want_2": "Хочу 2 шт.",
        "want_premium": "Хочу premium",
        "choose_offer": "Хочу рекомендованное",
        "more_details": "Подробнее",
        "compare": "Сравнить варианты",
        "delivery_details": "Детали доставки",
        "order_now": "Заказать сейчас",
        "see_offer": "Посмотреть предложение",
        "not_now": "Не сейчас",
        "question": "Хочу заказать",
        "product_details": "Детали товара",
        "confirm_order": "Подтвердить заказ",
        "usage": "Как использовать?",
        "use_grill": "Для гриля",
        "use_kitchen": "Для кухни",
        "use_gift": "В подарок",
    },
    "uk": {
        "want_2": "Хочу 2 шт.",
        "want_premium": "Хочу premium",
        "choose_offer": "Хочу рекомендоване",
        "more_details": "Детальніше",
        "compare": "Порівняти варіанти",
        "delivery_details": "Деталі доставки",
        "order_now": "Замовити зараз",
        "see_offer": "Переглянути пропозицію",
        "not_now": "Не зараз",
        "question": "Хочу замовити",
        "product_details": "Деталі товару",
        "confirm_order": "Підтвердити замовлення",
        "usage": "Як використовувати?",
        "use_grill": "Для гриля",
        "use_kitchen": "Для кухні",
        "use_gift": "На подарунок",
    },
    "es": {
        "want_2": "Quiero 2 uds.",
        "want_premium": "Quiero premium",
        "choose_offer": "Quiero oferta recomendada",
        "more_details": "Más detalles",
        "compare": "Comparar opciones",
        "delivery_details": "Detalles entrega",
        "order_now": "Pedir ahora",
        "see_offer": "Ver oferta",
        "not_now": "Ahora no",
        "question": "Quiero pedir",
        "product_details": "Detalles producto",
        "confirm_order": "Confirmar pedido",
        "usage": "Cómo se usa?",
        "use_grill": "Para parrilla",
        "use_kitchen": "Para cocina",
        "use_gift": "Para regalo",
    },
    "it": {
        "want_2": "Voglio 2 pz.",
        "want_premium": "Voglio premium",
        "choose_offer": "Voglio offerta consigliata",
        "more_details": "Più dettagli",
        "compare": "Confronta opzioni",
        "delivery_details": "Dettagli consegna",
        "order_now": "Ordina ora",
        "see_offer": "Vedi offerta",
        "not_now": "Non ora",
        "question": "Voglio ordinare",
        "product_details": "Dettagli prodotto",
        "confirm_order": "Conferma ordine",
        "usage": "Come si usa?",
        "use_grill": "Per griglia",
        "use_kitchen": "Per cucina",
        "use_gift": "Per regalo",
    },
    "fr": {
        "want_2": "Je veux 2 pcs",
        "want_premium": "Je veux premium",
        "choose_offer": "Je veux l'offre conseillée",
        "more_details": "Plus de détails",
        "compare": "Comparer options",
        "delivery_details": "Détails livraison",
        "order_now": "Commander",
        "see_offer": "Voir l'offre",
        "not_now": "Pas maintenant",
        "question": "Je veux commander",
        "product_details": "Détails produit",
        "confirm_order": "Confirmer commande",
        "usage": "Comment l'utiliser?",
        "use_grill": "Pour barbecue",
        "use_kitchen": "Pour cuisine",
        "use_gift": "Pour cadeau",
    },
    "de": {
        "want_2": "Ich will 2 Stk.",
        "want_premium": "Ich will premium",
        "choose_offer": "Empfohlenes Angebot",
        "more_details": "Mehr Details",
        "compare": "Optionen vergleichen",
        "delivery_details": "Lieferdetails",
        "order_now": "Jetzt bestellen",
        "see_offer": "Angebot ansehen",
        "not_now": "Nicht jetzt",
        "question": "Ich will bestellen",
        "product_details": "Produktdetails",
        "confirm_order": "Bestellung bestätigen",
        "usage": "Wie benutzt man es?",
        "use_grill": "Für Grill",
        "use_kitchen": "Für Küche",
        "use_gift": "Als Geschenk",
    },
}


def _unique_buttons(values, limit=3):
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()[:40]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result[:limit] if limit else result


def _normalize_language_code(value):
    text = str(value or "").strip().casefold().replace("_", "-")
    if not text:
        return None
    if text.startswith("lang:"):
        text = text.split(":", 1)[1]
    if text.startswith("language:"):
        text = text.split(":", 1)[1]
    for code, aliases in LANGUAGE_ALIASES.items():
        if text == code or text in aliases:
            return code
    if "-" in text:
        return _normalize_language_code(text.split("-", 1)[0])
    return text if text in LANGUAGE_ALIASES else None


def _language_markers_from_value(value):
    markers = set()
    if isinstance(value, dict):
        for key in ("language", "lang", "locale", "asset_language", "creative_language"):
            marker = _normalize_language_code(value.get(key))
            if marker:
                markers.add(marker)
        for key in ("languages", "locales"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple, set)):
                for item in nested:
                    marker = _normalize_language_code(item)
                    if marker:
                        markers.add(marker)
        return markers
    if isinstance(value, (list, tuple, set)):
        for item in value:
            markers |= _language_markers_from_value(item)
        return markers

    text = str(value or "").strip()
    if not text:
        return markers
    marker = _normalize_language_code(text)
    if marker:
        markers.add(marker)
    for raw in re.findall(r"(?:lang|language|locale)[:=_-]([a-z]{2}(?:-[a-z]{2})?|romanian|russian|english|spanish|italian|french|german)", text, flags=re.IGNORECASE):
        marker = _normalize_language_code(raw)
        if marker:
            markers.add(marker)
    return markers


def _asset_language_codes(asset):
    markers = set()
    metadata = asset.metadata if isinstance(asset.metadata, dict) else {}
    markers |= _language_markers_from_value(metadata)
    markers |= _language_markers_from_value(asset.tags or [])

    text_fields = " ".join(
        str(value or "")
        for value in (
            asset.title,
            asset.description,
            asset.original_filename,
            asset.public_url,
        )
    )
    markers |= _language_markers_from_value(text_fields)

    lowered_tags = {str(tag or "").strip().casefold() for tag in (asset.tags or [])}
    lowered_meta = {str(value or "").strip().casefold() for value in metadata.values() if not isinstance(value, (dict, list))}
    neutral = bool((lowered_tags | lowered_meta) & NEUTRAL_LANGUAGE_MARKERS)
    return markers, neutral


def _language_candidates(assets, language_code):
    requested = _normalize_language_code(language_code) or "ro"
    exact = []
    neutral = []
    for asset in assets:
        languages, force_neutral = _asset_language_codes(asset)
        if requested in languages:
            exact.append(asset)
        elif not languages or force_neutral:
            neutral.append(asset)

    if exact:
        return exact, "exact", requested
    if neutral:
        return neutral, "neutral", requested
    return [], "missing_language_match", requested


def _rotate_buttons(buttons, context):
    buttons = _unique_buttons(buttons, limit=None)
    previous = {
        str(button).strip().casefold()
        for button in ((context or {}).get("last_buttons") or [])
    }
    if not previous:
        return buttons[:3]
    fresh = [button for button in buttons if button.casefold() not in previous]
    repeated = [button for button in buttons if button.casefold() in previous]
    return _unique_buttons(fresh + repeated)


def _button_limit_for_context(context):
    depth = int((context or {}).get("engagement_depth") or 0)
    stage = str((context or {}).get("journey_stage") or "")
    if stage in {"action_lead", "desire_offer"}:
        return 2 if depth >= 4 else 3
    if (context or {}).get("details_path_exhausted"):
        return 2
    if stage == "desire_building" and depth >= 5:
        return 2
    return 3


def _keep_exit_button(context):
    depth = int((context or {}).get("engagement_depth") or 0)
    stage = str((context or {}).get("journey_stage") or "")
    if stage in {"attention", "interest_discovery"}:
        return True
    if stage == "desire_offer" and depth <= 3:
        return True
    return False


def _journey_buttons(buttons, labels, context, include_exit=True):
    limit = max(1, min(3, _button_limit_for_context(context)))
    if not include_exit:
        return _rotate_buttons(buttons, context)[:limit]
    exit_label = labels["not_now"]
    business_buttons = [
        button for button in _unique_buttons(buttons, limit=None)
        if button.casefold() != exit_label.casefold()
    ]
    rotated = _rotate_buttons(business_buttons, context)
    if _keep_exit_button(context):
        result = _unique_buttons(rotated[: max(1, limit - 1)] + [exit_label], limit=None)
        return result[:limit]
    return _unique_buttons(rotated, limit=None)[:limit]


def _offer_quantity_button(labels, quantity):
    try:
        quantity = int(quantity or 0)
    except (TypeError, ValueError):
        quantity = 0
    if quantity <= 0:
        return labels["choose_offer"]
    if quantity == 2:
        return labels["want_2"]
    if quantity == 1:
        singular = {
            "Vreau 2 bucăți": "Vreau 1 bucată",
            "I want 2 pcs": "I want 1 pc",
            "Хочу 2 шт.": "Хочу 1 шт.",
            "Quiero 2 uds.": "Quiero 1 ud.",
            "Voglio 2 pz.": "Voglio 1 pz.",
            "Je veux 2 pcs": "Je veux 1 pc",
            "Ich will 2 Stk.": "Ich will 1 Stk.",
        }
        return singular.get(labels["want_2"], re.sub(r"\b2\b", "1", labels["want_2"], count=1))
    return re.sub(r"\b2\b", str(quantity), labels["want_2"], count=1)


def _offer_quantity_from_label(label):
    match = re.search(r"\b([1-9]\d*)\s*(?:buc|bucat[ăa]|bucăți|bucati|pcs?|uds?\.?|pz\.?|шт|stk)\b", label, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b([1-9]\d*)\b", label)
    return int(match.group(1)) if match else 0


def _offer_buttons(offers, labels):
    buttons = []
    for offer in offers:
        label = f"{offer.get('offer_name', '')} {offer.get('variant', '')}".lower()
        quantity = offer.get("quantity") or _offer_quantity_from_label(label)
        if quantity and int(quantity) not in {1, 2}:
            continue
        if "premium" in label and not quantity:
            buttons.append(labels["want_premium"])
        else:
            buttons.append(_offer_quantity_button(labels, quantity))
    return _unique_buttons(buttons, limit=None)


def _labels(context):
    language_code = str((context or {}).get("language_code") or "ro").lower()
    return CTA_LABELS.get(language_code, CTA_LABELS["ro"])


def _price_allowed(context):
    return bool((context or {}).get("price_exposure_allowed"))


def _early_stage_buttons(labels, context):
    return _journey_buttons(
        [
            labels["usage"],
            labels["product_details"],
            labels["not_now"],
        ],
        labels,
        context,
    )


def _latest_client_text(context):
    for item in reversed((context or {}).get("recent_dialogue") or []):
        if item.get("role") == "client":
            return str(item.get("text") or "").casefold()
    return ""


def _client_texts(context):
    return [
        str(item.get("text") or "")
        for item in ((context or {}).get("recent_dialogue") or [])
        if item.get("role") == "client"
    ]


def _selected_use_context(context):
    return (context or {}).get("current_use_context") or (context or {}).get("selected_use_context")


def _is_use_context_text(text):
    return bool(
        re.search(
            r"\b(gr[aă]tar|grill|grilling|parrilla|buc[aă]t[aă]rie|kitchen|cocina|cucina|cadou|gift|regalo|грил|кухн|подар)",
            str(text or ""),
            flags=re.IGNORECASE,
        )
    )


def _is_detail_flow_text(text):
    return bool(
        re.search(
            r"\b(detalii|mai\s+multe|cum\s+se\s+folose|folos|utiliz|"
            r"gr[aă]tar|buc[aă]t[aă]rie|cadou|carne|material|calitate|duritate|"
            r"details|more\s+details|how\s+to\s+use|usage|grill|grilling|kitchen|gift|"
            r"parrilla|cocina|regalo|подроб|детал|использ|грил|кухн|подар|"
            r"dettagli|détails)\b",
            str(text or ""),
            flags=re.IGNORECASE,
        )
    )


def _detail_path_count(context):
    value = (context or {}).get("detail_path_count")
    if value is not None:
        return int(value or 0)
    return sum(1 for text in _client_texts(context)[-10:] if _is_detail_flow_text(text))


def _use_context_count(context):
    value = (context or {}).get("use_context_count")
    if value is not None:
        return int(value or 0)
    return sum(1 for text in _client_texts(context)[-10:] if _is_use_context_text(text))


def _details_path_exhausted(context):
    if (context or {}).get("details_path_exhausted") is not None:
        return bool((context or {}).get("details_path_exhausted"))
    return _detail_path_count(context) >= 3


def _last_buttons_include_discovery(labels, context):
    discovery = {
        labels["use_grill"].casefold(),
        labels["use_kitchen"].casefold(),
        labels["use_gift"].casefold(),
    }
    last = {
        str(button or "").strip().casefold()
        for button in ((context or {}).get("last_buttons") or [])
    }
    return bool(discovery & last)


def _discovery_buttons(labels, context):
    return _journey_buttons(
        [
            labels["use_grill"],
            labels["use_kitchen"],
            labels["use_gift"],
            labels["not_now"],
        ],
        labels,
        context,
    )


def _after_use_buttons(labels, context):
    buttons = [
        labels["see_offer"],
        labels["question"],
        labels["delivery_details"],
        labels["not_now"],
    ]
    return _journey_buttons(_remove_blocked_sales_buttons(buttons, labels, context), labels, context)


def _details_exit_buttons(labels, context):
    buttons = [
        labels["see_offer"],
        labels["question"],
        labels["not_now"],
    ]
    return _journey_buttons(_remove_blocked_sales_buttons(buttons, labels, context), labels, context)


def _needs_discovery_turn(intent, context):
    if intent not in {"asks_product_details", "asks_question", "general_reply"}:
        return False
    if _details_path_exhausted(context) or _selected_use_context(context):
        return False
    return _detail_path_count(context) >= 2 or _last_buttons_include_discovery(_labels(context), context)


def _remove_blocked_sales_buttons(buttons, labels, context):
    if _price_allowed(context):
        return buttons
    blocked = {
        labels["want_2"],
        labels["want_premium"],
        labels["choose_offer"],
        labels["order_now"],
        labels["see_offer"],
        labels["question"],
        labels["confirm_order"],
    }
    return [button for button in buttons if button not in blocked]


def select_cta_buttons(intent, knowledge, context=None):
    offers = knowledge.get("offers") or []
    labels = _labels(context)

    if intent in {"asks_product_details", "asks_question", "general_reply"}:
        if _details_path_exhausted(context):
            return _details_exit_buttons(labels, context)
        if _selected_use_context(context):
            return _after_use_buttons(labels, context)

    if _needs_discovery_turn(intent, context):
        return _discovery_buttons(labels, context)

    if intent in {"asks_price", "asks_offer"}:
        buttons = _offer_buttons(offers, labels)
        if not buttons:
            buttons = [labels["question"], labels["delivery_details"]]
        return _journey_buttons(buttons + [labels["not_now"]], labels, context)

    if intent == "asks_delivery":
        buttons = [
            labels["question"],
            labels["see_offer"],
            labels["not_now"],
        ]
        if not _price_allowed(context):
            buttons = [labels["product_details"], labels["delivery_details"], labels["not_now"]]
        return _journey_buttons(buttons, labels, context)

    if intent == "wants_to_order":
        return _journey_buttons(
            [labels["confirm_order"], labels["delivery_details"], labels["not_now"]],
            labels,
            context,
        )

    if intent == "asks_product_details":
        if not _price_allowed(context):
            return _early_stage_buttons(labels, context)
        if (context or {}).get("previous_assistant_replies"):
            return _journey_buttons(
                [
                    labels["see_offer"],
                    labels["question"],
                    labels["not_now"],
                ],
                labels,
                context,
            )
        return _journey_buttons(
            [
                labels["see_offer"],
                labels["delivery_details"],
                labels["not_now"],
            ],
            labels,
            context,
        )

    if intent in {"asks_corrosion", "asks_question"}:
        buttons = [
            labels["see_offer"],
            labels["question"],
            labels["not_now"],
        ]
        if not _price_allowed(context):
            buttons = [labels["product_details"], labels["usage"], labels["not_now"]]
        return _journey_buttons(_remove_blocked_sales_buttons(buttons, labels, context), labels, context)

    if not _price_allowed(context):
        return _early_stage_buttons(labels, context)

    return _journey_buttons(
        [
            labels["see_offer"],
            labels["question"],
            labels["not_now"],
        ],
        labels,
        context,
    )


def _sent_creative_ids(conversation_id):
    if not conversation_id:
        return set()

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT asset_id::text
            FROM product_creative_usage_history
            WHERE conversation_id = %s
              AND sent = TRUE
              AND asset_id IS NOT NULL
            """,
            [conversation_id],
        )
        return {row[0] for row in cursor.fetchall()}


def _creative_score(asset, intent, language_match=None):
    title = (asset.title or "").lower()
    tags = {str(tag).lower() for tag in (asset.tags or [])}
    score = max(0, 120 - int(asset.priority or 100))

    if intent == "asks_offer":
        if "general overview" in title:
            score += 120
        if "review" in title:
            score += 55
        if "offer" in tags:
            score += 80
    elif intent == "asks_price":
        if "general overview" in title:
            score += 85
        if "specification" in title:
            score += 45
    elif intent == "asks_corrosion":
        if "specification" in title:
            score += 120
        if "production process" in title:
            score += 70
        if "quality" in tags:
            score += 60
    elif intent == "asks_product_details":
        if "specification" in title:
            score += 115
        if "why to use" in title:
            score += 90
        if "review" in title:
            score += 75
    else:
        if "why to use" in title:
            score += 95
        if "general overview" in title:
            score += 80

    if language_match == "exact":
        score += 70
    elif language_match == "neutral":
        score += 10

    return score


def select_creative(product_id, intent, conversation_id=None, language_code=None):
    if not product_id or intent in NO_CREATIVE_INTENTS:
        return None

    sent_ids = _sent_creative_ids(conversation_id)
    assets = list(
        ProductCreativeAsset.objects.filter(
            product_id=str(product_id),
            is_active=True,
            use_superchat_file=True,
        ).exclude(superchat_file_id__isnull=True).exclude(superchat_file_id="")
    )
    unsent_assets = [asset for asset in assets if str(asset.asset_id) not in sent_ids]
    candidates, language_match, requested_language = _language_candidates(unsent_assets, language_code)
    if not candidates:
        return None

    selected = max(candidates, key=lambda asset: _creative_score(asset, intent, language_match))
    score = _creative_score(selected, intent, language_match)
    if score < 35:
        return None

    return {
        "asset_id": str(selected.asset_id),
        "asset_type": selected.asset_type,
        "title": selected.title,
        "description": selected.description,
        "public_url": selected.public_url,
        "superchat_file_id": selected.superchat_file_id,
        "selection_reason": f"intent:{intent};score:{score}",
        "language_code": requested_language,
        "language_match": language_match,
    }


def build_response_enrichment(product_id, intent, knowledge, conversation_id=None, context=None):
    buttons = select_cta_buttons(intent, knowledge, context)
    creative = select_creative(
        product_id,
        intent,
        conversation_id,
        language_code=(context or {}).get("language_code"),
    )
    return {
        "message_type": "quick_reply" if buttons else "text",
        "buttons": buttons,
        "creative": creative,
        "delivery_mode": "single_message_with_optional_media_header",
        "send_enabled": False,
    }
