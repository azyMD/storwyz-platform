import os
import re
import uuid

from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from productfeed.models import Product
from superchatsync.business_knowledge_agent import (
    generate_peeko_template_decision,
    is_peeko_test_phone,
)
from superchatsync.ai_safe_agent import generate_safe_ai_decision
from superchatsync.conversation_context import (
    detect_language_from_texts,
    infer_phone_country_language,
    record_client_message,
)
from superchatsync.models import (
    AiResponseProcessRun,
    AiResponseProcessStep,
    Conversation,
    Message,
    WhatsappAgentInboxRoute,
)
from superchatsync.order_webhook import (
    has_pending_order_request,
    is_order_request,
    prepare_order_request,
    submit_order,
)
from superchatsync.superchat_safe_send import (
    get_config,
    get_conversation_handle_status,
    load_env,
    send_link_buttons_to_conversation,
    send_quick_reply_to_conversation,
    send_reviewed_to_conversation,
    send_text_message_to_conversation,
    send_whats_app_template_to_conversation,
    set_conversation_custom_attribute,
    set_conversation_handle_status,
)
from superchatsync.response_enrichment import CTA_LABELS


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _csv_env(name):
    return {
        item.strip()
        for item in str(os.environ.get(name) or "").split(",")
        if item.strip()
    }


def _phone_in_allowlist(phone, allowed_values):
    if not allowed_values:
        return True
    phone_digits = _digits(phone)
    return any(_digits(value) == phone_digits for value in allowed_values if _digits(value))


def _peeko_test_keep_handle_status():
    return _is_true(os.environ.get("PEEKO_TEST_KEEP_HANDLE_STATUS", "0"))


AGENT_ROUTE_SCOPE_PREFIX = "agent_inbox_route:"


def _agent_route_scope(route):
    return f"{AGENT_ROUTE_SCOPE_PREFIX}{route.route_id}"


def _agent_route_from_scope(auth_scope):
    scope = str(auth_scope or "")
    if not scope.startswith(AGENT_ROUTE_SCOPE_PREFIX):
        return None
    route_id = scope[len(AGENT_ROUTE_SCOPE_PREFIX):]
    return (
        WhatsappAgentInboxRoute.objects
        .filter(route_id=route_id, active=True)
        .select_related("business")
        .first()
    )


def _route_is_peeko(route):
    return bool(route and route.agent_type == WhatsappAgentInboxRoute.AGENT_PEEKO_BUSINESS)


def _route_is_fitexpress_product(route):
    return bool(route and route.agent_type == WhatsappAgentInboxRoute.AGENT_FITEXPRESS_PRODUCT)


DIACRITIC_TRANSLATION = str.maketrans(
    "ăâîșțĂÂÎȘȚşţŞŢ",
    "aaistAAISTstST",
)
PRODUCT_MATCH_STOP_WORDS = {
    "produs", "produsul", "oferta", "ofertă", "detalii", "pret", "preț",
    "price", "offer", "details", "delivery", "shipping", "premium", "model",
    "товар", "цена", "доставка", "детали", "oferta", "producto", "prodotto",
}


def _match_normalize(value):
    text = str(value or "").translate(DIACRITIC_TRANSLATION).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _match_tokens(value):
    return {
        token
        for token in re.findall(r"[a-z0-9а-яёіїєґ]+", _match_normalize(value), flags=re.IGNORECASE)
        if len(token) > 2 and token not in PRODUCT_MATCH_STOP_WORDS
    }


def _payload_product_hint(extracted):
    hint = str(extracted.get("product_hint") or "").strip()
    if not hint:
        return None
    direct = Product.objects.filter(product_id=hint, active=True).first()
    if direct:
        return direct.product_id
    return detect_product_id_from_text(hint)


def detect_product_id_from_text(text):
    normalized_text = _match_normalize(text)
    if not normalized_text:
        return None
    text_tokens = _match_tokens(normalized_text)
    best_product_id = None
    best_score = 0

    for product in Product.objects.filter(active=True).values("product_id", "product_name", "brand", "category"):
        product_id = str(product.get("product_id") or "").strip()
        product_name = str(product.get("product_name") or "").strip()
        if not product_id or not product_name:
            continue
        if re.search(rf"(?<!\d){re.escape(_match_normalize(product_id))}(?!\d)", normalized_text):
            return product_id

        normalized_name = _match_normalize(product_name)
        if len(normalized_name) >= 4 and normalized_name in normalized_text:
            score = 100 + len(normalized_name)
        else:
            name_tokens = _match_tokens(product_name)
            if not name_tokens:
                continue
            common = name_tokens & text_tokens
            if len(common) == len(name_tokens) and len(name_tokens) <= 3:
                score = 80 + (len(common) * 10)
            elif len(common) >= 2:
                score = 35 + (len(common) * 12)
            else:
                score = 0
        if score > best_score:
            best_score = score
            best_product_id = product_id

    return best_product_id if best_score >= 45 else None


def _resolve_product_id_for_inbound(extracted, existing_conversation=None):
    return (
        _payload_product_hint(extracted)
        or detect_product_id_from_text(extracted.get("text"))
        or str(getattr(existing_conversation, "product_detected", "") or "").strip()
        or str(os.environ.get("AI_DEFAULT_PRODUCT_ID") or "").strip()
        or None
    )


def _authorized_for_autoreply(extracted, config):
    if not config.get("send_enabled"):
        return False, "autoreply_send_disabled"
    if not (
        _is_true(os.environ.get("AI_AUTOREPLY_ENABLED"))
        or _is_true(os.environ.get("AI_TEST_AUTOREPLY_ENABLED"))
        or _is_true(os.environ.get("AI_AUTOREPLY_SEND"))
    ):
        return False, "autoreply_disabled"

    try:
        handle_status = get_conversation_handle_status(config, extracted["conversation_id"])
    except Exception as exc:
        return False, f"handle_status_lookup_failed:{exc}"

    route = WhatsappAgentInboxRoute.match_target(handle_status)
    if not route:
        channel_ref = (
            handle_status.get("channel_phone_number")
            or handle_status.get("channel_name")
            or handle_status.get("channel_id")
            or "unknown"
        )
        return False, f"agent_inbox_route_missing:{channel_ref}"
    if str(handle_status.get("normalized") or "").strip().casefold() == "operator":
        return False, "handle_status_not_agent:operator"
    if route.require_handle_status and not handle_status.get("ok"):
        value = handle_status.get("value")
        return False, f"handle_status_not_agent:{value or 'missing'}"
    return True, _agent_route_scope(route)


def _read_text(value):
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in ("body", "text", "message", "value", "title"):
        nested = value.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        if isinstance(nested, dict):
            text = _read_text(nested)
            if text:
                return text
    return ""


def extract_inbound(payload):
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
    message_conversation = message.get("conversation") if isinstance(message.get("conversation"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    message_metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}

    return {
        "event_id": payload.get("id"),
        "event_type": payload.get("event"),
        "message_id": message.get("id") or payload.get("message_id"),
        "conversation_id": (
            payload.get("conversation_id")
            or message.get("conversation_id")
            or conversation.get("id")
            or message_conversation.get("id")
        ),
        "direction": message.get("direction") or payload.get("direction"),
        "sender_identifier": sender.get("identifier"),
        "content_type": content.get("type"),
        "product_hint": (
            payload.get("product_id")
            or payload.get("product")
            or payload.get("sku")
            or message.get("product_id")
            or message.get("product")
            or message.get("sku")
            or conversation.get("product_id")
            or conversation.get("product")
            or metadata.get("product_id")
            or metadata.get("product")
            or metadata.get("sku")
            or message_metadata.get("product_id")
            or message_metadata.get("product")
            or message_metadata.get("sku")
        ),
        "text": (
            _read_text(content)
            or _read_text(payload.get("content"))
            or _read_text(payload.get("text"))
            or _read_text(payload.get("body"))
        ),
        "created_at": parse_datetime(message.get("created_at") or "") or timezone.now(),
    }


def _advisory_lock(key):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(key or "")])


def _acquire_conversation_lock(conversation_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", [str(conversation_id or "")])


def _release_conversation_lock(conversation_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", [str(conversation_id or "")])


def _ensure_conversation_and_message(extracted, product_id, client_phone, auth_scope):
    now = timezone.now()
    product_id = str(product_id or "").strip()
    phone_locale = infer_phone_country_language(client_phone)
    initial_metadata = {
        "ai_autoreply_scope": auth_scope,
        "phone_country_code": phone_locale["country_code"],
        "phone_country_name": phone_locale["country_name"],
        "phone_default_language_code": phone_locale["language_code"],
    }
    with transaction.atomic():
        _advisory_lock(extracted["message_id"] or extracted["event_id"])
        if Message.objects.filter(message_id=extracted["message_id"]).exists():
            return None, None

        conversation, created = Conversation.objects.get_or_create(
            conversation_id=extracted["conversation_id"],
            defaults={
                "channel": "whats_app",
                "client_phone": client_phone,
                "product_detected": product_id,
                "first_message_at": extracted["created_at"],
                "last_message_at": extracted["created_at"],
                "has_client_reply": True,
                "source": "superchat_webhook_ai",
                "status": "active",
                "metadata": initial_metadata,
                "created_at": now,
                "updated_at": now,
            },
        )
        if not created:
            conversation.channel = "whats_app"
            if client_phone and not conversation.client_phone:
                conversation.client_phone = client_phone
            metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
            for key, value in initial_metadata.items():
                if value and not metadata.get(key):
                    metadata[key] = value
            update_fields = [
                "channel",
                "client_phone",
                "metadata",
                "last_message_at",
                "has_client_reply",
                "status",
                "updated_at",
            ]
            conversation.metadata = metadata
            if product_id and product_id != (conversation.product_detected or ""):
                conversation.product_detected = product_id
                update_fields.append("product_detected")
            conversation.last_message_at = extracted["created_at"]
            conversation.has_client_reply = True
            conversation.status = "active"
            conversation.updated_at = now
            conversation.save(update_fields=update_fields)

        message = Message.objects.create(
            message_pk=uuid.uuid4(),
            message_id=extracted["message_id"],
            conversation=conversation,
            sent_at=extracted["created_at"],
            sender_type="client",
            message_text=extracted["text"],
            message_type=extracted["content_type"] or "text",
            button_clicked=(
                extracted["text"]
                if "quick" in str(extracted["content_type"] or "").lower()
                else None
            ),
            is_client_reply=True,
            raw_payload={
                "event_id": extracted["event_id"],
                "event_type": extracted["event_type"],
                "ai_autoreply_scope": auth_scope,
                "product_hint": extracted.get("product_hint"),
                "detected_product_id": product_id,
                "phone_country_code": phone_locale["country_code"],
                "phone_default_language_code": phone_locale["language_code"],
            },
            created_at=now,
        )
        return conversation, message


def _auto_approve_test_run(run):
    now = timezone.now()
    with transaction.atomic():
        run.status = "human_approved_review_only"
        run.final_action = "test_auto_approved"
        run.save(update_fields=["status", "final_action"])
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=run,
            conversation_id=run.conversation_id,
            product_id=run.product_id,
            step_name="test_auto_approval",
            attempt=1,
            input_json={"source": "allowlisted_test_webhook"},
            output_json={
                "decision": "approve",
                "reviewer": "test_allowlist_runtime",
                "send_enabled": True,
                "scope": "verified_conversation_isolated_context",
            },
            approved=True,
            score=run.final_score,
            severity="info",
            action="test_auto_approved",
            fail_reasons=[],
            blocking_issues=[],
            feedback_for_repair="",
            created_at=now,
        )


ORDER_THANK_YOU_MESSAGES = {
    "ro": {
        "duplicate": "Mulțumim! Solicitarea de comandă este deja preluată. Revenim cu confirmarea pregătirii și detaliile de livrare.",
        "created": "Mulțumim! Comanda a fost preluată și intră la pregătire. Revenim cu confirmarea și detaliile de livrare.",
    },
    "en": {
        "duplicate": "Thank you! The order request is already registered. We will return with preparation confirmation and delivery details.",
        "created": "Thank you! The order has been registered and is going into preparation. We will return with confirmation and delivery details.",
    },
    "ru": {
        "duplicate": "Спасибо! Заявка на заказ уже принята. Мы вернемся с подтверждением подготовки и деталями доставки.",
        "created": "Спасибо! Заказ принят и передается в подготовку. Мы вернемся с подтверждением и деталями доставки.",
    },
    "uk": {
        "duplicate": "Дякуємо! Заявку на замовлення вже прийнято. Ми повернемося з підтвердженням підготовки та деталями доставки.",
        "created": "Дякуємо! Замовлення прийнято і переходить у підготовку. Ми повернемося з підтвердженням і деталями доставки.",
    },
    "es": {
        "duplicate": "Gracias! La solicitud de pedido ya está registrada. Volveremos con la confirmación de preparación y los detalles de entrega.",
        "created": "Gracias! El pedido fue registrado y pasa a preparación. Volveremos con la confirmación y los detalles de entrega.",
    },
    "it": {
        "duplicate": "Grazie! La richiesta d'ordine è già registrata. Torneremo con la conferma della preparazione e i dettagli di consegna.",
        "created": "Grazie! L'ordine è stato registrato e passa alla preparazione. Torneremo con conferma e dettagli di consegna.",
    },
    "fr": {
        "duplicate": "Merci! La demande de commande est déjà enregistrée. Nous reviendrons avec la confirmation de préparation et les détails de livraison.",
        "created": "Merci! La commande a été enregistrée et passe en préparation. Nous reviendrons avec la confirmation et les détails de livraison.",
    },
    "de": {
        "duplicate": "Danke! Die Bestellanfrage ist bereits registriert. Wir melden uns mit der Bestätigung der Vorbereitung und den Lieferdetails.",
        "created": "Danke! Die Bestellung wurde registriert und geht in Vorbereitung. Wir melden uns mit Bestätigung und Lieferdetails.",
    },
}
ORDER_DATA_REQUEST_MESSAGES = {
    "ro": {
        "both": "Perfect, pot înregistra comanda. Te rog trimite numele, prenumele și adresa completă de livrare.",
        "customer_name": "Perfect, am adresa. Te rog trimite și numele/prenumele pentru comandă.",
        "customer_address": "Perfect, am numele. Te rog trimite adresa completă de livrare.",
        "customer_phone": "Perfect, am nevoie și de numărul de telefon pentru comandă.",
    },
    "en": {
        "both": "Perfect, I can register the order. Please send your full name and complete delivery address.",
        "customer_name": "Perfect, I have the address. Please send the full name for the order.",
        "customer_address": "Perfect, I have the name. Please send the complete delivery address.",
        "customer_phone": "Perfect, I also need the phone number for the order.",
    },
    "ru": {
        "both": "Отлично, я могу оформить заказ. Пожалуйста, отправьте имя, фамилию и полный адрес доставки.",
        "customer_name": "Отлично, адрес есть. Пожалуйста, отправьте имя и фамилию для заказа.",
        "customer_address": "Отлично, имя есть. Пожалуйста, отправьте полный адрес доставки.",
        "customer_phone": "Отлично, нужен еще номер телефона для заказа.",
    },
    "uk": {
        "both": "Чудово, можу оформити замовлення. Будь ласка, надішліть ім'я, прізвище та повну адресу доставки.",
        "customer_name": "Чудово, адресу маю. Будь ласка, надішліть ім'я та прізвище для замовлення.",
        "customer_address": "Чудово, ім'я маю. Будь ласка, надішліть повну адресу доставки.",
        "customer_phone": "Чудово, потрібен ще номер телефону для замовлення.",
    },
    "es": {
        "both": "Perfecto, puedo registrar el pedido. Envíame el nombre completo y la dirección completa de entrega.",
        "customer_name": "Perfecto, tengo la dirección. Envíame también el nombre completo para el pedido.",
        "customer_address": "Perfecto, tengo el nombre. Envíame la dirección completa de entrega.",
        "customer_phone": "Perfecto, también necesito el número de teléfono para el pedido.",
    },
    "it": {
        "both": "Perfetto, posso registrare l'ordine. Inviami nome, cognome e indirizzo completo di consegna.",
        "customer_name": "Perfetto, ho l'indirizzo. Inviami anche nome e cognome per l'ordine.",
        "customer_address": "Perfetto, ho il nome. Inviami l'indirizzo completo di consegna.",
        "customer_phone": "Perfetto, mi serve anche il numero di telefono per l'ordine.",
    },
    "fr": {
        "both": "Parfait, je peux enregistrer la commande. Envoyez le nom complet et l'adresse complète de livraison.",
        "customer_name": "Parfait, j'ai l'adresse. Envoyez aussi le nom complet pour la commande.",
        "customer_address": "Parfait, j'ai le nom. Envoyez l'adresse complète de livraison.",
        "customer_phone": "Parfait, j'ai aussi besoin du numéro de téléphone pour la commande.",
    },
    "de": {
        "both": "Perfekt, ich kann die Bestellung registrieren. Bitte senden Sie vollständigen Namen und komplette Lieferadresse.",
        "customer_name": "Perfekt, die Adresse ist da. Bitte senden Sie noch den vollständigen Namen für die Bestellung.",
        "customer_address": "Perfekt, der Name ist da. Bitte senden Sie die komplette Lieferadresse.",
        "customer_phone": "Perfekt, ich brauche auch die Telefonnummer für die Bestellung.",
    },
}


def _thank_you_message(order_result, language_code="ro"):
    messages = ORDER_THANK_YOU_MESSAGES.get(language_code, ORDER_THANK_YOU_MESSAGES["ro"])
    payload = order_result.get("payload") if isinstance(order_result.get("payload"), dict) else {}
    if order_result.get("decision") == "order_already_submitted":
        intro = messages["duplicate"]
    else:
        intro = messages["created"]
    return _order_summary_message(intro, payload, language_code)


def _display_payload_value(value, fallback="-"):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _display_order_product(payload):
    product_id = str(payload.get("product") or "").strip()
    if product_id:
        product = Product.objects.filter(product_id=product_id).first()
        if product:
            return product.product_name
    return product_id or "-"


def _display_order_cost(payload):
    value = payload.get("cost")
    text = str(value or "").strip()
    if not text:
        return "-"
    if text.endswith(".00"):
        text = text[:-3]
    return f"{text} RON"


ORDER_SUMMARY_COPY = {
    "ro": {
        "title": "Rezumat comandă",
        "product": "Produs",
        "quantity": "Cantitate",
        "total": "Total",
        "name": "Nume",
        "phone": "Telefon",
        "address": "Adresă",
        "missing_address": "nu am găsit o adresă salvată; operatorul o va verifica",
        "footer": (
            "Dacă datele sunt corecte, nu trebuie să faci nimic. "
            "Dacă ceva trebuie corectat, scrie aici corectarea."
        ),
    },
    "en": {
        "title": "Order summary",
        "product": "Product",
        "quantity": "Quantity",
        "total": "Total",
        "name": "Name",
        "phone": "Phone",
        "address": "Address",
        "missing_address": "not found in the saved profile; an operator will verify it",
        "footer": (
            "If everything is correct, no action is needed. "
            "If something needs correction, reply here with the correction."
        ),
    },
    "es": {
        "title": "Resumen del pedido",
        "product": "Producto",
        "quantity": "Cantidad",
        "total": "Total",
        "name": "Nombre",
        "phone": "Teléfono",
        "address": "Dirección",
        "missing_address": "no encontrada en el perfil guardado; un operador la verificará",
        "footer": (
            "Si los datos son correctos, no tienes que hacer nada. "
            "Si hay que corregir algo, responde aquí con la corrección."
        ),
    },
    "it": {
        "title": "Riepilogo ordine",
        "product": "Prodotto",
        "quantity": "Quantità",
        "total": "Totale",
        "name": "Nome",
        "phone": "Telefono",
        "address": "Indirizzo",
        "missing_address": "non trovato nel profilo salvato; un operatore lo verificherà",
        "footer": (
            "Se i dati sono corretti, non devi fare nulla. "
            "Se qualcosa va corretto, rispondi qui con la correzione."
        ),
    },
    "fr": {
        "title": "Résumé de la commande",
        "product": "Produit",
        "quantity": "Quantité",
        "total": "Total",
        "name": "Nom",
        "phone": "Téléphone",
        "address": "Adresse",
        "missing_address": "introuvable dans le profil enregistré; un opérateur la vérifiera",
        "footer": (
            "Si les données sont correctes, aucune action n'est nécessaire. "
            "Si quelque chose doit être corrigé, répondez ici avec la correction."
        ),
    },
    "de": {
        "title": "Bestellübersicht",
        "product": "Produkt",
        "quantity": "Menge",
        "total": "Gesamt",
        "name": "Name",
        "phone": "Telefon",
        "address": "Adresse",
        "missing_address": "nicht im gespeicherten Profil gefunden; ein Operator prüft sie",
        "footer": (
            "Wenn alles korrekt ist, müssen Sie nichts tun. "
            "Wenn etwas korrigiert werden muss, antworten Sie hier mit der Korrektur."
        ),
    },
    "ru": {
        "title": "Сводка заказа",
        "product": "Товар",
        "quantity": "Количество",
        "total": "Итого",
        "name": "Имя",
        "phone": "Телефон",
        "address": "Адрес",
        "missing_address": "не найден в сохраненном профиле; оператор проверит его",
        "footer": (
            "Если данные верны, ничего делать не нужно. "
            "Если что-то нужно исправить, ответьте здесь с исправлением."
        ),
    },
    "uk": {
        "title": "Підсумок замовлення",
        "product": "Товар",
        "quantity": "Кількість",
        "total": "Разом",
        "name": "Ім'я",
        "phone": "Телефон",
        "address": "Адреса",
        "missing_address": "не знайдено у збереженому профілі; оператор її перевірить",
        "footer": (
            "Якщо дані правильні, нічого робити не потрібно. "
            "Якщо щось треба виправити, напишіть тут виправлення."
        ),
    },
}


def _order_summary_copy(language_code):
    return ORDER_SUMMARY_COPY.get(language_code, ORDER_SUMMARY_COPY["en"])


def _display_order_address(payload, language_code):
    address = _display_payload_value(payload.get("customer_address"), "")
    if address and address.lower() not in {"no address", "unknown", "necunoscut", "none", "n/a", "-"}:
        return address
    return _order_summary_copy(language_code)["missing_address"]


def _order_summary_message(intro, payload, language_code="ro"):
    copy = _order_summary_copy(language_code)
    product = _display_order_product(payload)
    quantity = _display_payload_value(payload.get("quantity"), "1")
    cost = _display_order_cost(payload)
    name = _display_payload_value(payload.get("customer_name"))
    phone = _display_payload_value(payload.get("customer_phone"))
    address = _display_order_address(payload, language_code)

    return (
        f"{intro}\n\n"
        f"{copy['title']}:\n"
        f"- {copy['product']}: {product}\n"
        f"- {copy['quantity']}: {quantity}\n"
        f"- {copy['total']}: {cost}\n"
        f"- {copy['name']}: {name}\n"
        f"- {copy['phone']}: {phone}\n"
        f"- {copy['address']}: {address}\n\n"
        f"{copy['footer']}"
    )


def _order_data_request_message(missing, language_code="ro"):
    messages = ORDER_DATA_REQUEST_MESSAGES.get(language_code, ORDER_DATA_REQUEST_MESSAGES["ro"])
    missing = set(missing or [])
    if {"customer_name", "customer_address"} <= missing:
        return messages["both"]
    if "customer_name" in missing:
        return messages["customer_name"]
    if "customer_address" in missing:
        return messages["customer_address"]
    if "customer_phone" in missing:
        return messages["customer_phone"]
    return messages["both"]


def _conversation_language_code(conversation, current_text):
    phone_locale = infer_phone_country_language(conversation.client_phone)
    recent_texts = list(
        conversation.messages
        .filter(is_client_reply=True)
        .exclude(message_text__isnull=True)
        .exclude(message_text="")
        .order_by("-sent_at")
        .values_list("message_text", flat=True)[:8]
    )
    language = detect_language_from_texts(
        list(recent_texts) + [current_text],
        fallback=phone_locale["language_code"],
    )
    return language["code"]


RECOVERY_MESSAGES = {
    "ro": "În regulă, ca să nu repet același lucru: putem merge pe pasul următor. Alege ce te ajută mai mult acum.",
    "en": "Sure, to avoid repeating the same thing, we can move to the next step. Choose what helps you most now.",
    "ru": "Хорошо, чтобы не повторять одно и то же, можем перейти к следующему шагу. Выберите, что сейчас полезнее.",
    "uk": "Добре, щоб не повторювати те саме, можемо перейти до наступного кроку. Оберіть, що зараз корисніше.",
    "es": "Perfecto, para no repetir lo mismo, podemos pasar al siguiente paso. Elige qué te ayuda más ahora.",
    "it": "Va bene, per non ripetere la stessa cosa possiamo passare al passo successivo. Scegli cosa ti aiuta di più ora.",
    "fr": "D'accord, pour éviter de répéter la même chose, nous pouvons passer à l'étape suivante. Choisissez ce qui vous aide le plus maintenant.",
    "de": "Alles klar, damit wir nicht dasselbe wiederholen, können wir zum nächsten Schritt gehen. Wählen Sie, was jetzt am meisten hilft.",
}


EXIT_MESSAGES = {
    "ro": "În regulă, nu insistăm. Dacă revine interesul, poți apăsa oricând «Vezi oferta» și îți trimitem oferta disponibilă.",
    "en": "No problem, we will not insist. If you become interested again, tap “See offer” anytime and we will send the available offer.",
    "ru": "Хорошо, не будем настаивать. Если интерес появится позже, нажмите «Посмотреть предложение», и мы отправим доступное предложение.",
    "uk": "Добре, не будемо наполягати. Якщо інтерес з’явиться пізніше, натисніть «Переглянути пропозицію», і ми надішлемо доступну пропозицію.",
    "es": "Sin problema, no insistimos. Si vuelve el interés, toca “Ver oferta” cuando quieras y te enviaremos la oferta disponible.",
    "it": "Va bene, non insistiamo. Se torna l’interesse, tocca “Vedi offerta” quando vuoi e ti invieremo l’offerta disponibile.",
    "fr": "D’accord, nous n’insistons pas. Si l’intérêt revient, appuyez sur « Voir l’offre » quand vous voulez et nous vous enverrons l’offre disponible.",
    "de": "Alles klar, wir drängen nicht. Wenn später Interesse besteht, tippen Sie jederzeit auf „Angebot ansehen“, und wir senden das verfügbare Angebot.",
}


EXIT_ALIASES = {
    "ro": {"nu acum", "nu mersi", "nu merci", "nu sunt interesat", "nu sunt interesată"},
    "en": {"not now", "no thanks", "not interested"},
    "ru": {"не сейчас", "нет спасибо", "не интересно"},
    "uk": {"не зараз", "ні дякую", "не цікаво"},
    "es": {"ahora no", "no gracias", "no me interesa"},
    "it": {"non ora", "no grazie", "non mi interessa"},
    "fr": {"pas maintenant", "non merci", "pas intéressé", "pas interesse"},
    "de": {"nicht jetzt", "nein danke", "kein interesse"},
}


def _run_step_output(run, step_name):
    step = run.steps.filter(step_name=step_name).order_by("-created_at").first()
    return step.output_json or {} if step else {}


def _button_key(value):
    return str(value or "").strip().casefold()


def _is_not_now_request(text, language_code):
    current_key = _button_key(text)
    if not current_key:
        return False
    labels = CTA_LABELS.get(language_code, CTA_LABELS["ro"])
    if current_key == _button_key(labels.get("not_now")):
        return True
    aliases = set(EXIT_ALIASES.get(language_code, set())) | set(EXIT_ALIASES["ro"])
    return current_key in {_button_key(alias) for alias in aliases}


def _exit_body(language_code):
    return EXIT_MESSAGES.get(language_code, EXIT_MESSAGES["ro"])


def _send_not_now_exit(conversation, active_product_id, current_text, client_phone):
    language_code = _conversation_language_code(conversation, current_text)
    labels = CTA_LABELS.get(language_code, CTA_LABELS["ro"])
    body = _exit_body(language_code)
    buttons = [labels["see_offer"]]
    now = timezone.now()
    send_result = send_quick_reply_to_conversation(
        conversation.conversation_id,
        body,
        buttons,
        product_id=active_product_id,
        intent="not_now_exit",
        expected_phone=client_phone,
    )
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=None,
        conversation_id=conversation.conversation_id,
        product_id=active_product_id,
        step_name="not_now_exit",
        attempt=1,
        input_json={"current_text": current_text, "language_code": language_code},
        output_json={"body": body, "buttons": buttons, "send_result": send_result},
        approved=True,
        score=100,
        severity="info",
        action="not_now_exit_sent",
        fail_reasons=[],
        blocking_issues=[],
        feedback_for_repair="",
        created_at=now,
    )
    return {
        "ok": True,
        "decision": "not_now_exit_sent",
        "message_id": send_result.get("message_id"),
        "buttons": buttons,
        "context_recorded": send_result.get("context_recorded"),
    }


def _recovery_buttons(language_code, current_text, run):
    labels = CTA_LABELS.get(language_code, CTA_LABELS["ro"])
    context = _run_step_output(run, "conversation_context")
    last_buttons = {_button_key(button) for button in (context.get("last_buttons") or [])}
    current_key = _button_key(current_text)

    if current_key in {
        _button_key(labels["see_offer"]),
        _button_key(labels["choose_offer"]),
        _button_key(labels["want_2"]),
        _button_key(labels["want_premium"]),
    }:
        candidates = [
            labels["delivery_details"],
            labels["confirm_order"],
            labels["question"],
            labels["not_now"],
            labels["product_details"],
        ]
    else:
        candidates = [
            labels["see_offer"],
            labels["delivery_details"],
            labels["confirm_order"],
            labels["question"],
            labels["not_now"],
            labels["product_details"],
        ]

    fresh = [button for button in candidates if _button_key(button) != current_key and _button_key(button) not in last_buttons]
    fallback = [button for button in candidates if _button_key(button) != current_key]
    result = []
    for button in fresh + fallback:
        key = _button_key(button)
        if key and key not in {_button_key(item) for item in result}:
            result.append(button)
        if len(result) == 3:
            break
    return result


def _recovery_body(language_code):
    return RECOVERY_MESSAGES.get(language_code, RECOVERY_MESSAGES["ro"])


def _send_recovery_flow(conversation, run, active_product_id, current_text, client_phone):
    language_code = _conversation_language_code(conversation, current_text)
    body = _recovery_body(language_code)
    buttons = _recovery_buttons(language_code, current_text, run)
    if not buttons:
        return {
            "ok": True,
            "decision": "needs_review",
            "run_id": str(run.run_id),
            "score": run.final_score,
            "recovery_reason": "no_recovery_buttons",
        }

    now = timezone.now()
    try:
        send_result = send_quick_reply_to_conversation(
            conversation.conversation_id,
            body,
            buttons,
            product_id=active_product_id,
            intent="recovery_flow",
            expected_phone=client_phone,
        )
    except Exception as exc:
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=run,
            conversation_id=run.conversation_id,
            product_id=run.product_id,
            step_name="recovery_flow",
            attempt=1,
            input_json={
                "current_text": current_text,
                "blocked_run_status": run.status,
                "blocked_run_error": run.error,
            },
            output_json={"error": str(exc), "body": body, "buttons": buttons},
            approved=False,
            score=0,
            severity="warning",
            action="recovery_failed",
            fail_reasons=["recovery_send_failed"],
            blocking_issues=[],
            feedback_for_repair=str(exc),
            created_at=now,
        )
        return {
            "ok": True,
            "decision": "needs_review",
            "run_id": str(run.run_id),
            "score": run.final_score,
            "recovery_error": str(exc),
        }

    with transaction.atomic():
        run = run.__class__.objects.select_for_update().get(run_id=run.run_id)
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=run,
            conversation_id=run.conversation_id,
            product_id=run.product_id,
            step_name="recovery_flow",
            attempt=1,
            input_json={
                "current_text": current_text,
                "blocked_run_status": run.status,
                "blocked_run_error": run.error,
                "excluded_cta": current_text,
            },
            output_json={
                "body": body,
                "buttons": buttons,
                "send_result": send_result,
            },
            approved=True,
            score=80,
            severity="info",
            action="recovery_sent",
            fail_reasons=[],
            blocking_issues=[],
            feedback_for_repair="",
            created_at=now,
        )
        run.status = "recovery_sent"
        run.final_action = "recovery_sent"
        run.final_body = body
        run.final_buttons = buttons
        run.finished_at = now
        run.save(update_fields=["status", "final_action", "final_body", "final_buttons", "finished_at"])

    return {
        "ok": True,
        "decision": "recovery_sent",
        "run_id": str(run.run_id),
        "message_id": send_result.get("message_id"),
        "buttons": buttons,
        "context_recorded": send_result.get("context_recorded"),
    }


def _handoff_to_operator_after_order(config, conversation, product_id, order_result):
    result = set_conversation_handle_status(config, conversation.conversation_id, "operator")
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=None,
        conversation_id=conversation.conversation_id,
        product_id=product_id,
        step_name="handle_status_handoff",
        attempt=1,
        input_json={
            "target_status": "operator",
            "order_decision": order_result.get("decision"),
            "order_http_status": order_result.get("http_status"),
        },
        output_json=result,
        approved=bool(result.get("ok")),
        score=100 if result.get("ok") else 0,
        severity="info" if result.get("ok") else "warning",
        action="operator_handoff" if result.get("ok") else "operator_handoff_failed",
        fail_reasons=[] if result.get("ok") else [result.get("reason") or "handle_status_update_failed"],
        blocking_issues=[],
        feedback_for_repair="" if result.get("ok") else str(result.get("reason") or ""),
        created_at=timezone.now(),
    )
    return result


def _mark_peeko_send_failed(run, conversation_id, product_id, step_name, error, decision=None):
    now = timezone.now()
    error_text = str(error or "")[:2000]
    decision = decision or {}
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=run,
        conversation_id=conversation_id,
        product_id=product_id or "business:peeko",
        step_name=step_name,
        attempt=1,
        input_json={
            "template_name": decision.get("template_name"),
            "template_id": decision.get("template_id"),
            "intent": decision.get("intent"),
            "variables": decision.get("variables") or [],
            "buttons": decision.get("buttons") or [],
        },
        output_json={"ok": False, "error": error_text},
        approved=False,
        score=0,
        severity="error",
        action="send_failed",
        fail_reasons=[error_text[:300] or "send_failed"],
        blocking_issues=[],
        feedback_for_repair=error_text,
        created_at=now,
    )
    if run is not None:
        run.status = "send_failed"
        run.error = error_text
        run.finished_at = now
        run.save(update_fields=["status", "error", "finished_at"])


def _latest_failed_peeko_run(conversation_id, client_message):
    runs = AiResponseProcessRun.objects.filter(
        conversation_id=conversation_id,
        client_message=client_message,
        product_id__startswith="business:peeko",
    ).order_by("-created_at")[:5]
    failure_steps = {"peeko_template_send_failed", "peeko_discovery_send_failed"}
    required_template_variables = {
        "bestsellers_link": 1,
        "browse_choice": 2,
        "topcategory_select": 2,
    }
    for run in runs:
        if run.status == "send_failed":
            return run
        if run.steps.filter(
            step_name__in={
                "peeko_template_sent",
                "peeko_discovery_sent",
                "peeko_operator_handoff",
            }
        ).exists():
            return None
        if run.steps.filter(step_name__in=failure_steps).exists():
            return run
        orchestrator_step = (
            run.steps.filter(step_name="peeko_template_orchestrator")
            .order_by("-created_at")
            .first()
        )
        output = orchestrator_step.output_json if orchestrator_step else {}
        if isinstance(output, dict) and output.get("action") == "send_template":
            template_key = str(output.get("template_key") or "").strip()
            required_count = required_template_variables.get(template_key, 0)
            if required_count and len(output.get("variables") or []) < required_count:
                return run
    return None


def _process_peeko_business_message(config, conversation, message_text, client_phone):
    decision = generate_peeko_template_decision(
        conversation,
        message_text,
        client_phone,
    )

    if decision["action"] == "operator_handoff":
        if _peeko_test_keep_handle_status() and is_peeko_test_phone(client_phone):
            handoff_result = {
                "ok": True,
                "skipped": True,
                "reason": "peeko_test_keep_handle_status",
                "target_status": "operator",
            }
        else:
            handoff_result = set_conversation_handle_status(
                config,
                conversation.conversation_id,
                "operator",
            )
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=decision.get("run"),
            conversation_id=conversation.conversation_id,
            product_id=decision.get("product_focus") or "business:peeko",
            step_name="peeko_operator_handoff",
            attempt=1,
            input_json={
                "target_status": "operator",
                "reason": decision.get("reason"),
                "message": message_text,
            },
            output_json=handoff_result,
            approved=bool(handoff_result.get("ok")),
            score=100 if handoff_result.get("ok") else 0,
            severity="info" if handoff_result.get("ok") else "warning",
            action=(
                "operator_handoff_suppressed_test"
                if handoff_result.get("skipped")
                else "operator_handoff" if handoff_result.get("ok") else "operator_handoff_failed"
            ),
            fail_reasons=[] if handoff_result.get("ok") else [handoff_result.get("reason") or "handle_status_update_failed"],
            blocking_issues=[],
            feedback_for_repair="" if handoff_result.get("ok") else str(handoff_result.get("reason") or ""),
            created_at=timezone.now(),
        )
        return {
            "ok": bool(handoff_result.get("ok")),
            "decision": "peeko_operator_handoff",
            "business": "peeko",
            "run_id": str(decision["run"].run_id),
            "handle_status_update": handoff_result,
        }

    if decision["action"] == "send_discovery":
        try:
            send_result = send_quick_reply_to_conversation(
                conversation.conversation_id,
                decision["body"],
                decision.get("buttons") or [],
                product_id=decision["product_focus"],
                intent=decision["intent"],
                expected_phone=client_phone,
            )
        except Exception as exc:
            _mark_peeko_send_failed(
                decision.get("run"),
                conversation.conversation_id,
                decision.get("product_focus") or "business:peeko",
                "peeko_discovery_send_failed",
                exc,
                decision=decision,
            )
            raise
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=decision.get("run"),
            conversation_id=conversation.conversation_id,
            product_id=decision.get("product_focus") or "business:peeko",
            step_name="peeko_discovery_sent",
            attempt=1,
            input_json={
                "intent": decision.get("intent"),
                "body": decision.get("body"),
                "buttons": decision.get("buttons") or [],
            },
            output_json=send_result,
            approved=True,
            score=100,
            severity="info",
            action="discovery_sent",
            fail_reasons=[],
            blocking_issues=[],
            feedback_for_repair="",
            created_at=timezone.now(),
        )
        return {
            "ok": True,
            "decision": "peeko_discovery_sent",
            "business": "peeko",
            "run_id": str(decision["run"].run_id),
            "message_id": send_result.get("message_id"),
            "buttons": decision.get("buttons"),
            "reason": decision.get("reason"),
            "context_recorded": send_result.get("context_recorded"),
        }

    contact_attribute_updates = []
    for attribute_name, attribute_value in (decision.get("contact_attributes") or {}).items():
        try:
            update_result = set_conversation_custom_attribute(
                config,
                conversation.conversation_id,
                attribute_name,
                attribute_value,
                expected_phone=client_phone,
            )
        except Exception as exc:
            update_result = {
                "ok": False,
                "reason": str(exc),
                "attribute_name": attribute_name,
            }
        contact_attribute_updates.append(
            {
                "attribute_name": attribute_name,
                "value": attribute_value,
                "ok": bool(update_result.get("ok")),
                "reason": update_result.get("reason"),
                "attribute_id": update_result.get("attribute_id"),
            }
        )

    if contact_attribute_updates:
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=decision.get("run"),
            conversation_id=conversation.conversation_id,
            product_id=decision.get("product_focus") or "business:peeko",
            step_name="peeko_template_contact_attributes",
            attempt=1,
            input_json={
                "template_name": decision.get("template_name"),
                "attribute_names": [
                    update["attribute_name"] for update in contact_attribute_updates
                ],
            },
            output_json={"updates": contact_attribute_updates},
            approved=all(update["ok"] for update in contact_attribute_updates),
            score=100 if all(update["ok"] for update in contact_attribute_updates) else 60,
            severity=(
                "info"
                if all(update["ok"] for update in contact_attribute_updates)
                else "warning"
            ),
            action=(
                "contact_attributes_updated"
                if all(update["ok"] for update in contact_attribute_updates)
                else "contact_attributes_update_partial"
            ),
            fail_reasons=[
                update["reason"] or update["attribute_name"]
                for update in contact_attribute_updates
                if not update["ok"]
            ],
            blocking_issues=[],
            feedback_for_repair="",
            created_at=timezone.now(),
        )

    try:
        send_result = send_whats_app_template_to_conversation(
            conversation.conversation_id,
            decision["template_id"],
            variables=decision.get("variables") or None,
            product_id=decision["product_focus"],
            intent=decision["intent"],
            expected_phone=client_phone,
            template_name=decision["template_name"],
            schedule_operator_handoff=decision.get("schedule_operator_handoff", True),
            handoff_delay_seconds=int(os.environ.get("PEEKO_URL_HANDOFF_DELAY_SECONDS") or 300),
            aux_cta_labels=decision.get("aux_cta_labels") or [],
        )
    except Exception as exc:
        _mark_peeko_send_failed(
            decision.get("run"),
            conversation.conversation_id,
            decision.get("product_focus") or "business:peeko",
            "peeko_template_send_failed",
            exc,
            decision=decision,
        )
        raise
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=decision.get("run"),
        conversation_id=conversation.conversation_id,
        product_id=decision.get("product_focus") or "business:peeko",
        step_name="peeko_template_sent",
        attempt=1,
        input_json={
            "template_name": decision.get("template_name"),
            "template_id": decision.get("template_id"),
            "intent": decision.get("intent"),
            "variables": decision.get("variables") or [],
            "buttons": decision.get("buttons") or [],
        },
        output_json=send_result,
        approved=True,
        score=100,
        severity="info",
        action="template_sent",
        fail_reasons=[],
        blocking_issues=[],
        feedback_for_repair="",
        created_at=timezone.now(),
    )
    return {
        "ok": True,
        "decision": "peeko_template_sent",
        "business": "peeko",
        "template": decision["template_name"],
        "product": decision["product"].name if decision.get("product") else None,
        "run_id": str(decision["run"].run_id),
        "message_id": send_result.get("message_id"),
        "buttons": decision.get("buttons"),
        "contact_attribute_updates": contact_attribute_updates,
        "scheduled_handoff": send_result.get("scheduled_handoff"),
        "reason": decision.get("reason"),
        "context_recorded": send_result.get("context_recorded"),
    }


def _retry_failed_peeko_duplicate(extracted, config, auth_scope):
    route = _agent_route_from_scope(auth_scope)
    if not _route_is_peeko(route):
        return None
    message = (
        Message.objects.filter(message_id=extracted["message_id"])
        .select_related("conversation")
        .first()
    )
    if message is None:
        return None
    failed_run = _latest_failed_peeko_run(
        extracted["conversation_id"],
        message.message_text or extracted["text"],
    )
    if failed_run is None:
        return None
    client_phone = (
        extracted["sender_identifier"]
        or config.get("allowed_phone")
        or message.conversation.client_phone
        or ""
    )
    return _process_peeko_business_message(
        config,
        message.conversation,
        message.message_text or extracted["text"],
        client_phone,
    )


def _process_authorized_inbound(extracted, config, auth_scope):
    if Message.objects.filter(message_id=extracted["message_id"]).exists():
        retry_result = _retry_failed_peeko_duplicate(extracted, config, auth_scope)
        if retry_result is not None:
            return retry_result
        return {"ok": True, "decision": "ignored", "reason": "duplicate_message"}

    client_phone = extracted["sender_identifier"] or config.get("allowed_phone") or ""
    existing_conversation = Conversation.objects.filter(
        conversation_id=extracted["conversation_id"]
    ).first()
    route = _agent_route_from_scope(auth_scope)
    if _route_is_peeko(route):
        previous_product_id = str(getattr(existing_conversation, "product_detected", "") or "").strip()
        business_slug = str(getattr(getattr(route, "business", None), "slug", "") or "peeko").strip()
        product_id = previous_product_id if previous_product_id.startswith(f"business:{business_slug}") else f"business:{business_slug}"
    elif _route_is_fitexpress_product(route) and str(route.default_product_id or "").strip():
        product_id = str(route.default_product_id or "").strip()
    else:
        product_id = _resolve_product_id_for_inbound(extracted, existing_conversation)
    conversation, message = _ensure_conversation_and_message(
        extracted,
        product_id,
        client_phone,
        auth_scope,
    )
    if message is None:
        return {"ok": True, "decision": "ignored", "reason": "duplicate_message"}
    active_product_id = str(conversation.product_detected or product_id or "").strip()
    if route:
        metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
        metadata.update(
            {
                "agent_route_id": str(route.route_id),
                "agent_route_name": route.name,
                "agent_type": route.agent_type,
                "agent_channel_id": route.channel_id,
                "agent_channel_phone": route.channel_phone,
                "agent_inbox_id": route.inbox_id,
                "agent_inbox_name": route.inbox_name,
            }
        )
        conversation.metadata = metadata
        conversation.updated_at = timezone.now()
        conversation.save(update_fields=["metadata", "updated_at"])
    record_client_message(conversation.conversation_id, active_product_id, extracted["text"])

    if _route_is_peeko(route):
        return _process_peeko_business_message(
            config,
            conversation,
            extracted["text"],
            client_phone,
        )

    language_code = _conversation_language_code(conversation, extracted["text"])
    if _is_not_now_request(extracted["text"], language_code):
        return _send_not_now_exit(
            conversation,
            active_product_id,
            extracted["text"],
            client_phone,
        )

    should_handle_order = (
        is_order_request(extracted["text"])
        or has_pending_order_request(conversation, active_product_id)
    )
    if should_handle_order:
        order_request = prepare_order_request(
            conversation,
            active_product_id,
            extracted["text"],
        )
        if not order_request.get("ready"):
            request_message = _order_data_request_message(
                order_request.get("missing"),
                language_code,
            )
            send_result = send_text_message_to_conversation(
                conversation.conversation_id,
                request_message,
                product_id=active_product_id,
                intent="order_data_request",
                expected_phone=client_phone,
            )
            return {
                "ok": True,
                "decision": "order_data_requested",
                "missing": order_request.get("missing"),
                "message_id": send_result.get("message_id"),
                "context_recorded": send_result.get("context_recorded"),
            }

        order_result = submit_order(
            conversation,
            active_product_id,
            extracted["text"],
            order_details=order_request.get("details"),
        )
        if order_result.get("ok"):
            thank_you = _thank_you_message(
                order_result,
                language_code,
            )
            send_result = send_text_message_to_conversation(
                conversation.conversation_id,
                thank_you,
                product_id=active_product_id,
                intent="order_confirmation",
                expected_phone=client_phone,
            )
            handle_status_update = None
            if order_result.get("decision") == "order_registered":
                handle_status_update = _handoff_to_operator_after_order(
                    config,
                    conversation,
                    active_product_id,
                    order_result,
                )
            return {
                "ok": True,
                "decision": "order_registered_and_thanked",
                "order_decision": order_result.get("decision"),
                "order_http_status": order_result.get("http_status"),
                "message_id": send_result.get("message_id"),
                "context_recorded": send_result.get("context_recorded"),
                "handle_status_update": handle_status_update,
            }
        if order_result.get("decision") != "order_webhook_not_configured":
            return {
                "ok": True,
                "decision": order_result.get("decision"),
                "order_http_status": order_result.get("http_status"),
            }

    run = generate_safe_ai_decision(conversation.conversation_id)
    if run.status != "approved_review_only":
        return _send_recovery_flow(
            conversation,
            run,
            active_product_id,
            extracted["text"],
            client_phone,
        )

    _auto_approve_test_run(run)
    send_result = send_reviewed_to_conversation(
        run.run_id,
        conversation.conversation_id,
        expected_phone=client_phone,
    )
    return {
        "ok": True,
        "decision": "test_sent",
        "run_id": str(run.run_id),
        "message_id": send_result.get("message_id"),
        "context_recorded": send_result.get("context_recorded"),
    }


def process_webhook_payload(payload):
    load_env()
    config = get_config()
    extracted = extract_inbound(payload if isinstance(payload, dict) else {})

    if str(extracted["direction"] or "").lower() != "inbound":
        return {"ok": True, "decision": "ignored", "reason": "not_inbound"}
    if extracted["event_type"] not in {None, "message_inbound"}:
        return {"ok": True, "decision": "ignored", "reason": "not_message_inbound"}
    if not extracted["message_id"] or not extracted["text"]:
        return {"ok": True, "decision": "ignored", "reason": "missing_message_or_text"}
    authorized, auth_scope = _authorized_for_autoreply(extracted, config)
    if not authorized:
        return {"ok": True, "decision": "ignored", "reason": auth_scope}

    if Message.objects.filter(message_id=extracted["message_id"]).exists():
        _acquire_conversation_lock(extracted["conversation_id"])
        try:
            retry_result = _retry_failed_peeko_duplicate(extracted, config, auth_scope)
            if retry_result is not None:
                return retry_result
            return {"ok": True, "decision": "ignored", "reason": "duplicate_message"}
        finally:
            _release_conversation_lock(extracted["conversation_id"])

    _acquire_conversation_lock(extracted["conversation_id"])
    try:
        return _process_authorized_inbound(extracted, config, auth_scope)
    finally:
        _release_conversation_lock(extracted["conversation_id"])
