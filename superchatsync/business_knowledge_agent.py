import os
import re
import uuid
from decimal import Decimal
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from django.db import connection
from django.db.models import Q
from django.utils import timezone

from superchatsync.models import (
    AiResponseProcessRun,
    AiResponseProcessStep,
    BusinessClient,
    BusinessKnowledgeItem,
    BusinessMediaAsset,
    BusinessProduct,
    BusinessProductRanking,
)


PEEKO_TEST_PHONE_DIGITS = {"37368200969", "447896887292"}
PEEKO_BUSINESS_SLUG = "peeko"
DEFAULT_BUTTONS = ["Show another", "Snacks", "Beauty"]
TRENDING_BUTTONS = ["Trending deals", "Snacks", "Beauty"]
MAX_PRODUCTS_PER_REPLY = 3
WHATSAPP_BUTTON_LABEL_LIMIT = 20

PEEKO_TEMPLATE_DEFAULTS = {
    "browse_choice": "tn_02YDQ7tklePHdQbfBfhfN",
    "bestsellers_link": "tn_EsJyIqA3TZFzJueF7S9cC",
    "topcategory_select": "tn_J83r4A7vTBuFPa6piBVoG",
    "product_link": "tn_ShyIVFyIiZ8ZV1tIwCAq3",
}
PEEKO_TEMPLATE_NAMES = {
    "browse_choice": "peeko_browse_choice",
    "bestsellers_link": "peeko_bestsellers_shortlink",
    "topcategory_select": "peeko_category_select",
    "product_link": "peeko_product_shortlink",
}
PEEKO_TEMPLATE_BUTTONS = {
    "browse_choice": ["Help me choose", "Bestsellers", "New Deals"],
    "bestsellers_link": ["Browse deals", "View bestsellers"],
    "topcategory_select": ["More options", "Groceries", "Beauty"],
    "product_link": ["View product"],
}
PEEKO_AUX_CTA_LABELS = {
    "browse_choice": ["Help me choose"],
    "bestsellers_link": ["Browse deals"],
    "topcategory_select": ["More options"],
    "product_link": [],
}
PEEKO_PRODUCT_URL_SUFFIX_ATTRIBUTE = "peeko_product_url_suffix"
PEEKO_PRODUCT_SHORTLINK_CODE_ATTRIBUTE = "peeko_product_shortlink_code"
PEEKO_AGE_RESTRICTED_TERMS = {
    "nicotine", "pouch", "pouches", "vape", "hayati", "gin", "whisky",
    "whiskey", "vodka", "rum", "wine", "beer", "alcohol",
}
PEEKO_GENERIC_GREETINGS = {
    "hi", "hello", "hey", "salut", "buna", "bună", "ciao", "hola",
}
PEEKO_DISCOVERY_BUTTONS = {
    "welcome": ["Groceries", "Beauty", "Deals"],
    "help": ["Groceries", "Beauty", "Bestsellers"],
    "groceries": ["Snacks", "Best deals", "Other category"],
    "snacks": ["Sweet snacks", "Snack deals", "Other category"],
    "sweet_snacks": ["Best deals", "All snacks", "Other category"],
    "savoury_snacks": ["Best deals", "All snacks", "Other category"],
    "drinks": ["Soft drinks", "Best deals", "Other category"],
    "beauty": ["Skin care", "Beauty deals", "Other category"],
    "category": ["Groceries", "Beauty", "Deals"],
    "fallback": ["Bestsellers", "Help me choose", "Deals"],
}


SYNONYMS = {
    "batoane": ["bars", "bar", "chocolate"],
    "baton": ["bar", "bars", "chocolate"],
    "ciocolata": ["chocolate"],
    "ciocolată": ["chocolate"],
    "neagra": ["dark"],
    "neagră": ["dark"],
    "dulciuri": ["sweets", "candy", "chocolate"],
    "biscuiti": ["biscuits", "cookies"],
    "biscuiți": ["biscuits", "cookies"],
    "gustari": ["snacks"],
    "gustări": ["snacks"],
    "frumusete": ["beauty"],
    "frumusețe": ["beauty"],
}


STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "what", "how", "much",
    "price", "delivery", "order", "product", "products", "please", "tell",
    "about", "more", "info", "information", "show", "another", "all",
    "salut", "buna", "bună", "care", "este", "pret", "preț", "livrare",
    "produs", "produse", "vreau", "detalii", "comanda", "comandă", "toate",
}


def digits(value):
    return re.sub(r"\D", "", str(value or ""))


def is_peeko_test_phone(phone):
    return digits(phone) in PEEKO_TEST_PHONE_DIGITS


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_lower(value):
    return normalize_text(value).casefold()


def tokens(value):
    raw = normalized_lower(value)
    found = []
    for token in re.findall(r"[a-z0-9ăâîșțñáéíóúàèêëïôùûüäöß]+", raw, flags=re.IGNORECASE):
        if len(token) <= 2 or token in STOP_WORDS:
            continue
        found.append(token)
        found.extend(SYNONYMS.get(token, []))
    return list(dict.fromkeys(found))


def recent_transcript(conversation_id, limit=12):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT role, message_text
            FROM ai_chat_transcript
            WHERE conversation_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            [conversation_id, limit],
        )
        rows = cursor.fetchall()
    return [{"role": role, "text": text} for role, text in reversed(rows)]


def asked_delivery_or_support(message_text):
    lowered = normalized_lower(message_text)
    return any(word in lowered for word in ["delivery", "shipping", "livrare", "return", "refund", "contact"])


def asked_best_sellers(message_text):
    lowered = normalized_lower(message_text)
    return any(word in lowered for word in ["best", "seller", "sellers", "trending", "popular", "deals", "arata produse", "arată produse"])


def is_show_another(message_text):
    lowered = normalized_lower(message_text)
    return any(phrase in lowered for phrase in ["show another", "more", "mai multe", "another", "altul", "alta"])


def category_hint(message_text):
    lowered = normalized_lower(message_text)
    if any(word in lowered for word in ["chocolate", "sweet", "sweets", "candy", "batoane", "ciocol", "dulciuri", "biscuit"]):
        return "sweet_snacks"
    if any(word in lowered for word in ["crisps", "chips", "savoury", "savory", "nuts", "popcorn"]):
        return "savoury_snacks"
    if any(word in lowered for word in ["snack", "snacks", "gustari", "gustări"]):
        return "snacks"
    if any(word in lowered for word in ["drink", "drinks", "juice", "water", "soda", "bubble tea"]):
        return "drinks"
    if any(word in lowered for word in ["beauty", "makeup", "skin", "hair", "frumusete", "frumusețe"]):
        return "beauty"
    if any(word in lowered for word in ["household", "cleaning", "laundry"]):
        return "household"
    if any(word in lowered for word in ["health", "wellness", "protein", "supplement"]):
        return "health"
    if any(word in lowered for word in ["grocery", "groceries", "food"]):
        return "groceries"
    return ""


def tracked_url(product, campaign="peeko_trending"):
    url = product.url or f"https://peeko.co.uk/products/{product.slug}"
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(
        {
            "utm_source": "whatsapp",
            "utm_medium": "ai_agent",
            "utm_campaign": campaign,
            "utm_content": product.slug,
        }
    )
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def product_price(product):
    price = product.min_price
    if price is None or price == Decimal("0"):
        return ""
    currency = product.currency or "GBP"
    symbol = "£" if currency == "GBP" else f"{currency} "
    if product.max_price and product.max_price != product.min_price:
        return f"{symbol}{product.min_price} - {symbol}{product.max_price}"
    return f"{symbol}{product.min_price}"


def product_image_url(product):
    asset = (
        BusinessMediaAsset.objects.filter(product=product, status="approved", asset_type="image")
        .order_by("image_role", "title")
        .first()
    )
    return asset.source_url if asset else ""


def shorten_button_label(value, used_labels=None):
    used = used_labels if used_labels is not None else set()
    base = normalize_text(value)
    if not base:
        base = "View product"

    compact = re.sub(r"\b\d+\s*x\s*[\d.]+\s*(g|kg|ml|l)\b", "", base, flags=re.IGNORECASE)
    compact = re.sub(r"\b[\d.]+\s*(g|kg|ml|l)\b", "", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\b(multibuy|classic|original|pack|case|single)\b", "", compact, flags=re.IGNORECASE)
    compact = compact.replace("Chocolate", "Choc").replace("chocolate", "choc")
    compact = compact.replace("Peanut", "Pnut").replace("peanut", "pnut")
    compact = normalize_text(compact) or base

    label = compact[:WHATSAPP_BUTTON_LABEL_LIMIT].strip()
    if len(compact) > WHATSAPP_BUTTON_LABEL_LIMIT:
        label = f"{compact[:WHATSAPP_BUTTON_LABEL_LIMIT - 3].rstrip()}..."
    if label not in used:
        used.add(label)
        return label

    for index in range(2, 10):
        suffix = f" {index}"
        candidate = f"{label[:WHATSAPP_BUTTON_LABEL_LIMIT - len(suffix)].rstrip()}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    return label


def product_button_labels(products):
    used = set()
    return [shorten_button_label(product.name, used) for product in products]


def product_link_buttons(products, labels=None):
    button_labels = labels or product_button_labels(products)
    result = []
    for index, product in enumerate(products[:MAX_PRODUCTS_PER_REPLY]):
        result.append(
            {
                "type": "url",
                "title": button_labels[index] if index < len(button_labels) else shorten_button_label(product.name),
                "target": tracked_url(product),
            }
        )
    return result


def button_key(value):
    return re.sub(r"[^a-z0-9]+", "", normalized_lower(value))


def seen_product_slugs(transcript):
    text = "\n".join(item.get("text") or "" for item in transcript)
    return set(re.findall(r"/products/([a-z0-9-]+)", text, flags=re.IGNORECASE))


def selected_product_from_previous_reply(conversation_id, message_text):
    current_key = button_key(message_text)
    if not current_key:
        return None

    steps = (
        AiResponseProcessStep.objects.filter(
            conversation_id=conversation_id,
            step_name="peeko_trending_catalog_agent",
        )
        .order_by("-created_at")[:5]
    )
    for step in steps:
        output = step.output_json or {}
        for product in output.get("products") or []:
            possible_labels = [
                product.get("button_label"),
                product.get("name"),
                f"View {product.get('name') or ''}",
            ]
            if current_key in {button_key(label) for label in possible_labels if label}:
                return product
    return None


def ranking_queryset(business):
    return (
        BusinessProductRanking.objects.filter(
            business=business,
            rank_type="best_seller",
            active=True,
            product__status="active",
        )
        .select_related("product")
        .order_by("rank", "-score", "product__name")
    )


def product_score(ranking, query_tokens, category, already_seen):
    product = ranking.product
    haystack = " ".join(
        [
            product.name or "",
            product.slug or "",
            product.product_type or "",
            ranking.collection_slug or "",
            ranking.collection_title or "",
        ]
    ).casefold()
    score = 10000 - int(ranking.rank or 0)
    for token in query_tokens:
        if token and token.casefold() in haystack:
            score += 600
    if category and category in (ranking.collection_slug or "").casefold():
        score += 1500
    if product.slug in already_seen:
        score -= 5000
    if not product_price(product):
        score -= 1200
    return score


def select_trending_products(business, message_text, transcript):
    query_tokens = tokens(message_text)
    category = category_hint(message_text)
    already_seen = seen_product_slugs(transcript)
    candidates = []

    rankings = list(ranking_queryset(business)[:500])
    if not rankings:
        return fallback_products(business, query_tokens, already_seen)

    for ranking in rankings:
        score = product_score(ranking, query_tokens, category, already_seen)
        if query_tokens and score < 10000:
            continue
        candidates.append((score, ranking))

    if not candidates:
        for ranking in rankings:
            candidates.append((product_score(ranking, [], category, already_seen), ranking))

    unique = []
    used = set()
    for _, ranking in sorted(candidates, key=lambda item: item[0], reverse=True):
        if ranking.product_id in used:
            continue
        used.add(ranking.product_id)
        unique.append(ranking.product)
        if len(unique) >= MAX_PRODUCTS_PER_REPLY:
            break
    return unique


def fallback_products(business, query_tokens, already_seen):
    qs = BusinessProduct.objects.filter(business=business, status="active")
    if query_tokens:
        query = Q()
        for token in query_tokens:
            query |= Q(name__icontains=token) | Q(slug__icontains=token) | Q(product_type__icontains=token)
        qs = qs.filter(query)
    products = []
    for product in qs.order_by("name")[:200]:
        if product.slug in already_seen:
            continue
        products.append(product)
        if len(products) >= MAX_PRODUCTS_PER_REPLY:
            break
    return products


def delivery_or_support_reply(business):
    delivery_url = f"https://{business.domain}/pages/delivery-information?utm_source=whatsapp&utm_medium=ai_agent&utm_campaign=peeko_support&utm_content=delivery"
    return {
        "body": (
            "You can check Peeko delivery details directly on the site here:\n"
            f"{delivery_url}\n\n"
            "If you want to keep browsing, I can show you trending deals or popular snacks."
        ),
        "buttons": TRENDING_BUTTONS,
        "link_buttons": [
            {
                "type": "url",
                "title": "Delivery info",
                "target": delivery_url,
            }
        ],
        "products": [],
        "intent": "peeko_site_handoff_support",
    }


def format_products_reply(products, message_text):
    if not products:
        return {
            "body": (
                "I could not find a clean match from the current trending picks. "
                "The fastest route is to browse Peeko trending deals here:\n"
                "https://peeko.co.uk/collections/trending-deals?utm_source=whatsapp&utm_medium=ai_agent&utm_campaign=peeko_trending&utm_content=no_match"
            ),
            "buttons": TRENDING_BUTTONS,
            "products": [],
            "intent": "peeko_trending_no_match",
        }

    intro = "Here are a few trending Peeko picks:"
    if asked_best_sellers(message_text) or is_show_another(message_text):
        intro = "Here are trending Peeko picks:"
    lines = [intro]
    for index, product in enumerate(products, start=1):
        price = product_price(product)
        price_text = f" — {price}" if price else ""
        lines.append(f"{index}. {product.name}{price_text}")
    lines.append("")
    lines.append("Tap a product below to open it, or ask me to narrow the list.")
    labels = product_button_labels(products)
    return {
        "body": "\n".join(lines),
        "buttons": labels,
        "link_buttons": product_link_buttons(products, labels),
        "products": products,
        "intent": "peeko_trending_products",
    }


def selected_product_reply(product):
    name = product.get("name") or "this product"
    price = product.get("price") or ""
    price_text = f" — {price}" if price else ""
    url = product.get("url") or "https://peeko.co.uk/?utm_source=whatsapp&utm_medium=ai_agent&utm_campaign=peeko_product_handoff"
    return {
        "body": (
            f"Great choice: {name}{price_text}.\n"
            "Tap below to open it on Peeko."
        ),
        "buttons": TRENDING_BUTTONS,
        "link_buttons": [
            {
                "type": "url",
                "title": "Open product",
                "target": url,
            }
        ],
        "products": [],
        "intent": "peeko_product_link_handoff",
        "selected_product": product,
    }


def build_peeko_reply(business, conversation, message_text):
    selected_product = selected_product_from_previous_reply(conversation.conversation_id, message_text)
    if selected_product:
        return selected_product_reply(selected_product)
    transcript = recent_transcript(conversation.conversation_id)
    if asked_delivery_or_support(message_text):
        return delivery_or_support_reply(business)
    products = select_trending_products(business, message_text, transcript)
    return format_products_reply(products, message_text)


def summarize_products(products, buttons=None):
    labels = buttons or product_button_labels(products)
    summary = []
    for index, product in enumerate(products):
        summary.append(
            {
                "slug": product.slug,
                "name": product.name,
                "price": product_price(product),
                "url": tracked_url(product),
                "image_url": product_image_url(product),
                "button_label": labels[index] if index < len(labels) else product.name,
            }
        )
    return summary


def peeko_template_id(template_key):
    env_name = f"PEEKO_TEMPLATE_{template_key.upper()}_ID"
    return os.environ.get(env_name) or PEEKO_TEMPLATE_DEFAULTS[template_key]


def peeko_site_url(path):
    clean_path = str(path or "").strip()
    if not clean_path.startswith("/"):
        clean_path = f"/{clean_path}"
    split = urlsplit(f"https://peeko.co.uk{clean_path}")
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query["utm_source"] = "whatsapp"
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def recent_category_context(conversation_id, message_text):
    category = category_hint(message_text)
    if category:
        return category
    for item in reversed(recent_transcript(conversation_id, limit=8)):
        if str(item.get("role") or "").casefold() not in {"client", "customer", "user"}:
            continue
        category = category_hint(item.get("text") or "")
        if category:
            return category
    return ""


def peeko_browse_target_for_context(category):
    if category in {"groceries", "snacks", "sweet_snacks", "savoury_snacks", "drinks"}:
        return {
            "title": "Peeko groceries deals",
            "target_url": peeko_site_url("/collections/groceries-trending"),
            "kind": "groceries_trending",
        }
    if category == "beauty":
        return {
            "title": "Peeko beauty deals",
            "target_url": peeko_site_url("/collections/beauty-trending"),
            "kind": "beauty_trending",
        }
    return {
        "title": "Peeko bestsellers",
        "target_url": peeko_site_url("/collections/trending-deals"),
        "kind": "trending_deals",
    }


def peeko_static_template_links(template_key, category=""):
    if template_key == "bestsellers_link":
        target = peeko_browse_target_for_context(category)
        return [{"position": 1, **target}]
    if template_key == "browse_choice":
        return [
            {
                "position": 1,
                "title": "Peeko bestsellers",
                "target_url": peeko_site_url("/collections/trending-deals"),
                "kind": "trending_deals",
            },
            {
                "position": 2,
                "title": "Peeko new deals",
                "target_url": peeko_site_url("/collections/new-in"),
                "kind": "new_in",
            },
        ]
    if template_key == "topcategory_select":
        return [
            {
                "position": 1,
                "title": "Peeko groceries",
                "target_url": peeko_site_url("/collections/groceries-trending"),
                "kind": "groceries_trending",
            },
            {
                "position": 2,
                "title": "Peeko beauty",
                "target_url": peeko_site_url("/collections/beauty-trending"),
                "kind": "beauty_trending",
            },
        ]
    return []


def peeko_shortlink_suffix(target_url, title, template_key, conversation=None, product_focus="", intent="", metadata=None):
    from superchatsync.shortlinks import create_short_link

    link = create_short_link(
        target_url,
        title=title,
        business_slug=PEEKO_BUSINESS_SLUG,
        conversation_id=getattr(conversation, "conversation_id", "") or "",
        phone=getattr(conversation, "client_phone", "") or "",
        product_id=product_focus or f"business:{PEEKO_BUSINESS_SLUG}",
        product_name=title,
        source_template=PEEKO_TEMPLATE_NAMES[template_key],
        intent=intent or f"peeko_{template_key}_template",
        metadata=metadata or {},
    )
    return link.code


def static_template_variables(template_key, conversation=None, product_focus="", intent="", category=""):
    variables = []
    for link_info in peeko_static_template_links(template_key, category=category):
        suffix = peeko_shortlink_suffix(
            link_info["target_url"],
            link_info["title"],
            template_key,
            conversation=conversation,
            product_focus=product_focus,
            intent=intent,
            metadata={
                "template_variable": "url_extension",
                "position": link_info["position"],
                "target_kind": link_info["kind"],
            },
        )
        variables.append({"position": link_info["position"], "value": suffix})
    return variables


def is_age_restricted_product(product):
    haystack = " ".join(
        [
            str(getattr(product, "name", "") or ""),
            str(getattr(product, "slug", "") or ""),
            str(getattr(product, "product_type", "") or ""),
            " ".join(str(tag) for tag in (getattr(product, "tags", None) or [])),
        ]
    ).casefold()
    return any(term in haystack for term in PEEKO_AGE_RESTRICTED_TERMS)


def is_generic_greeting(message_text):
    lowered = normalized_lower(message_text)
    if not lowered:
        return True
    cleaned = re.sub(r"[^a-zăâîșț ]+", " ", lowered).strip()
    words = [word for word in cleaned.split() if word]
    return bool(words) and all(word in PEEKO_GENERIC_GREETINGS for word in words)


def wants_help_or_more_options(message_text):
    lowered = normalized_lower(message_text)
    return any(
        phrase in lowered
        for phrase in [
            "help me choose",
            "more options",
            "not sure",
            "nu stiu",
            "nu știu",
            "help",
        ]
    )


def wants_other_category(message_text):
    lowered = normalized_lower(message_text)
    return any(
        phrase in lowered
        for phrase in [
            "other category",
            "choose another category",
            "another category",
            "switch category",
            "change category",
        ]
    )


def wants_browse_link(message_text):
    lowered = normalized_lower(message_text)
    return any(
        phrase in lowered
        for phrase in [
            "browse deals",
            "best seller",
            "best sellers",
            "bestsellers",
            "popular",
            "show popular",
            "trending deals",
            "hot deals",
            "new deals",
            "deals",
            "offers",
        ]
    )


def wants_browse_choice_template(message_text):
    lowered = normalized_lower(message_text)
    if lowered in {"deals", "browse deals", "see deals", "show deals"}:
        return True
    return any(
        phrase in lowered
        for phrase in [
            "browse deals",
            "see deals",
            "show deals",
            "new deal",
            "new deals",
            "new offer",
            "new offers",
            "latest deal",
            "latest deals",
            "latest offer",
            "latest offers",
            "current offer",
            "current offers",
        ]
    )


def wants_product_recommendation(message_text):
    lowered = normalized_lower(message_text)
    return any(
        phrase in lowered
        for phrase in [
            "a product",
            "one product",
            "show product",
            "show me a product",
            "send product",
            "send me a product",
            "product recommendation",
            "recommend a product",
        ]
    )


def wants_impulse_product(message_text):
    lowered = normalized_lower(message_text)
    return any(
        phrase in lowered
        for phrase in [
            "show another",
            "show me more",
            "send me something",
            "recommend",
            "pick for me",
            "something",
            "surprise",
        ]
    )


def recent_template_keys(conversation_id, limit=5):
    keys = []
    steps = (
        AiResponseProcessStep.objects.filter(
            conversation_id=conversation_id,
            step_name="peeko_template_orchestrator",
        )
        .order_by("-created_at")[:limit]
    )
    for step in steps:
        output = step.output_json if isinstance(step.output_json, dict) else {}
        key = str(output.get("template_key") or "").strip()
        if key:
            keys.append(key)
    return keys


def last_template_key(conversation_id):
    keys = recent_template_keys(conversation_id, limit=1)
    return keys[0] if keys else ""


def first_safe_product(products):
    for product in products:
        if product and not is_age_restricted_product(product):
            return product
    return None


def peeko_product_hook(product):
    price = product_price(product)
    if price:
        return "A trending Peeko pick at a good price - easy to add to your basket."
    return "A trending Peeko pick people are checking right now."


def peeko_product_target_url(product):
    return f"https://peeko.co.uk/products/{product.slug}?utm_source=whatsapp"


def product_url_suffix(product, conversation=None, product_focus="", intent=""):
    if conversation is not None:
        try:
            from superchatsync.shortlinks import create_short_link, shortlinks_enabled

            if shortlinks_enabled():
                link = create_short_link(
                    peeko_product_target_url(product),
                    title=product.name,
                    business_slug=PEEKO_BUSINESS_SLUG,
                    conversation_id=conversation.conversation_id,
                    phone=conversation.client_phone or "",
                    product_id=product_focus or f"business:{PEEKO_BUSINESS_SLUG}:{product.slug}",
                    product_name=product.name,
                    source_template=PEEKO_TEMPLATE_NAMES["product_link"],
                    intent=intent or "peeko_product_template",
                    metadata={
                        "product_slug": product.slug,
                        "template_variable": "url_extension",
                    },
                )
                return link.code
        except Exception:
            pass
    return f"{product.slug}?utm_source=whatsapp"


def product_template_payload(product, conversation=None, product_focus="", intent=""):
    name = product.name
    hook = peeko_product_hook(product)
    price = product_price(product) or "See today's price"
    url_suffix = product_url_suffix(
        product,
        conversation=conversation,
        product_focus=product_focus,
        intent=intent,
    )
    variables = [
        {"position": 1, "value": name},
        {"position": 2, "value": hook},
        {"position": 3, "value": price},
        {"position": 4, "value": url_suffix},
    ]
    contact_attributes = {
        "peeko_product_name": name,
        "peeko_product_hook": hook,
        "peeko_product_price": price,
        PEEKO_PRODUCT_SHORTLINK_CODE_ATTRIBUTE: url_suffix,
        PEEKO_PRODUCT_URL_SUFFIX_ATTRIBUTE: url_suffix,
    }
    return variables, contact_attributes


def product_template_variables(product, conversation=None, product_focus="", intent=""):
    variables, _contact_attributes = product_template_payload(
        product,
        conversation=conversation,
        product_focus=product_focus,
        intent=intent,
    )
    return variables


def template_decision(
    template_key,
    intent,
    product_focus,
    product=None,
    variables=None,
    contact_attributes=None,
    reason="",
):
    return {
        "action": "send_template",
        "template_key": template_key,
        "template_name": PEEKO_TEMPLATE_NAMES[template_key],
        "template_id": peeko_template_id(template_key),
        "variables": variables or [],
        "contact_attributes": contact_attributes or {},
        "buttons": PEEKO_TEMPLATE_BUTTONS[template_key],
        "aux_cta_labels": PEEKO_AUX_CTA_LABELS[template_key],
        "schedule_operator_handoff": True,
        "product": product,
        "product_focus": product_focus,
        "intent": intent,
        "reason": reason,
    }


def discovery_decision(kind, intent, product_focus, body, reason=""):
    return {
        "action": "send_discovery",
        "intent": intent,
        "product_focus": product_focus,
        "body": body,
        "buttons": PEEKO_DISCOVERY_BUTTONS.get(kind, PEEKO_DISCOVERY_BUTTONS["fallback"]),
        "reason": reason,
        "product": None,
        "variables": [],
        "aux_cta_labels": [],
        "schedule_operator_handoff": False,
    }


def peeko_discovery_intro(message_text, last_template):
    if is_generic_greeting(message_text):
        return (
            "Hey 😊 I can help you find something useful on Peeko.\n\n"
            "Are you looking more for everyday groceries, beauty/self-care, or the current deals?"
        ), "welcome", "generic_greeting_start_discovery"

    if wants_other_category(message_text):
        return (
            "Sure, let’s switch category.\n\n"
            "Would you like groceries, beauty/self-care, or the current deals?"
        ), "category", "switch_category_requested"

    if wants_help_or_more_options(message_text):
        return (
            "Sure 😊 Let’s narrow it down first.\n\n"
            "What kind of thing would be most useful right now: groceries, beauty, or best-value picks?"
        ), "help", "help_request_needs_discovery"

    category = category_hint(message_text)
    if category == "groceries":
        return (
            "Good direction. For groceries, I can start with snacks or the strongest current deals. "
            "You can also switch category.\n\n"
            "What should I look for first?"
        ), "groceries", "groceries_interest_needs_context"
    if category == "snacks":
        return (
            "Snacks, nice 😊 I can narrow this to sweet snacks, show snack deals, "
            "or switch category.\n\n"
            "What should I show first?"
        ), "snacks", "snacks_interest_needs_context"
    if category == "sweet_snacks":
        return (
            "Sweet snacks it is. I can show the best current sweet/snack deals, "
            "go back to all snacks, or switch category.\n\n"
            "What should I show first?"
        ), "sweet_snacks", "sweet_snacks_interest_needs_context"
    if category == "savoury_snacks":
        return (
            "Savoury snacks, got it. I can show the strongest snack deals, "
            "go back to all snacks, or switch category.\n\n"
            "What should I show first?"
        ), "savoury_snacks", "savoury_snacks_interest_needs_context"
    if category == "drinks":
        return (
            "Drinks, got it. I can start with soft drinks, show the best current deals, "
            "or switch category.\n\n"
            "What should I show first?"
        ), "drinks", "drinks_interest_needs_context"
    if category == "beauty":
        return (
            "Got it. For beauty, it helps to know whether you want skincare, hair/body care, "
            "or beauty deals. You can also switch category.\n\n"
            "Where should I start?"
        ), "beauty", "beauty_interest_needs_context"

    if last_template:
        return (
            "Before I send another link, let’s make it a bit more relevant.\n\n"
            "Do you want me to show popular products, current deals, or help you choose by category?"
        ), "category", "post_template_returns_to_discovery"

    return (
        "I can help, but I need one small direction first.\n\n"
        "Would you rather browse bestsellers, get help choosing, or see current deals?"
    ), "fallback", "default_discovery_before_template"


def selected_business_product(business, conversation_id, message_text):
    selected = selected_product_from_previous_reply(conversation_id, message_text)
    if not selected:
        return None
    slug = str(selected.get("slug") or "").strip()
    if not slug:
        return None
    return BusinessProduct.objects.filter(business=business, slug=slug, status="active").first()


def build_peeko_template_decision(business, conversation, message_text, phone):
    product_focus = f"business:{business.slug}"
    last_template = last_template_key(conversation.conversation_id)

    if asked_delivery_or_support(message_text):
        return {
            "action": "operator_handoff",
            "intent": "peeko_support_operator_handoff",
            "product_focus": product_focus,
            "reason": "support_or_transactional_intent",
            "buttons": [],
        }

    selected_product = selected_business_product(business, conversation.conversation_id, message_text)
    if selected_product and not is_age_restricted_product(selected_product):
        product_focus = f"business:{business.slug}:{selected_product.slug}"
        variables, contact_attributes = product_template_payload(
            selected_product,
            conversation=conversation,
            product_focus=product_focus,
            intent="peeko_product_template_selected_product",
        )
        return template_decision(
            "product_link",
            "peeko_product_template_selected_product",
            product_focus,
            product=selected_product,
            variables=variables,
            contact_attributes=contact_attributes,
            reason="selected_product_from_previous_options",
        )

    if wants_product_recommendation(message_text) or wants_impulse_product(message_text):
        transcript = recent_transcript(conversation.conversation_id)
        products = select_trending_products(business, message_text, transcript)
        impulse_product = first_safe_product(products)
        if impulse_product:
            product_focus = f"business:{business.slug}:{impulse_product.slug}"
            variables, contact_attributes = product_template_payload(
                impulse_product,
                conversation=conversation,
                product_focus=product_focus,
                intent="peeko_product_template_impulse_offer",
            )
            return template_decision(
                "product_link",
                "peeko_product_template_impulse_offer",
                product_focus,
                product=impulse_product,
                variables=variables,
                contact_attributes=contact_attributes,
                reason="requested_or_indecisive_product_offer",
            )

    if wants_help_or_more_options(message_text):
        body, kind, reason = peeko_discovery_intro(message_text, last_template)
        return discovery_decision(
            kind,
            "peeko_discovery_reply",
            product_focus,
            body,
            reason=reason,
        )

    if wants_browse_choice_template(message_text):
        intent = "peeko_browse_choice_template"
        return template_decision(
            "browse_choice",
            intent,
            product_focus,
            variables=static_template_variables(
                "browse_choice",
                conversation=conversation,
                product_focus=product_focus,
                intent=intent,
            ),
            reason="requested_browse_choice_links",
        )

    if wants_browse_link(message_text):
        intent = "peeko_bestsellers_link_template"
        category = recent_category_context(conversation.conversation_id, message_text)
        return template_decision(
            "bestsellers_link",
            intent,
            product_focus,
            variables=static_template_variables(
                "bestsellers_link",
                conversation=conversation,
                product_focus=product_focus,
                intent=intent,
                category=category,
            ),
            reason="requested_browse_or_deals_link",
        )

    if category_hint(message_text):
        body, kind, reason = peeko_discovery_intro(message_text, last_template)
        return discovery_decision(
            kind,
            "peeko_category_discovery_reply",
            product_focus,
            body,
            reason=reason,
        )

    body, kind, reason = peeko_discovery_intro(message_text, last_template)
    return discovery_decision(
        kind,
        "peeko_discovery_reply",
        product_focus,
        body,
        reason=reason,
    )


def generate_peeko_template_decision(conversation, message_text, phone):
    business = BusinessClient.objects.get(slug=PEEKO_BUSINESS_SLUG, status="active")
    decision = build_peeko_template_decision(business, conversation, message_text, phone)
    product_focus = decision.get("product_focus") or f"business:{business.slug}"

    if conversation.product_detected != product_focus:
        conversation.product_detected = product_focus
        conversation.save(update_fields=["product_detected", "updated_at"])

    now = timezone.now()
    product = decision.get("product")
    product_summary = summarize_products([product], decision.get("buttons")) if product else []
    final_body = decision.get("body") or f"template:{decision.get('template_name') or decision['action']}"
    run = AiResponseProcessRun.objects.create(
        run_id=uuid.uuid4(),
        conversation_id=conversation.conversation_id,
        product_id=product_focus,
        client_message=message_text,
        status="approved_review_only",
        final_action=decision["intent"],
        final_score=96,
        final_body=final_body[:1800],
        final_buttons=decision.get("buttons") or [],
        attempts_count=1,
        error=None,
        created_at=now,
        finished_at=now,
    )
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=run,
        conversation_id=conversation.conversation_id,
        product_id=product_focus,
        step_name="peeko_template_orchestrator",
        attempt=1,
        input_json={
            "business": business.slug,
            "message": message_text,
            "phone_digits": digits(phone),
            "mode": "template_inventory_orchestration",
            "language": "en",
        },
        output_json={
            "action": decision["action"],
            "intent": decision["intent"],
            "reason": decision.get("reason"),
            "body": decision.get("body"),
            "template_key": decision.get("template_key"),
            "template_name": decision.get("template_name"),
            "template_id": decision.get("template_id"),
            "buttons": decision.get("buttons") or [],
            "aux_cta_labels": decision.get("aux_cta_labels") or [],
            "variables": decision.get("variables") or [],
            "products": product_summary,
        },
        approved=True,
        score=96,
        severity="info",
        action=decision["intent"],
        fail_reasons=[],
        blocking_issues=[],
        feedback_for_repair="",
        created_at=now,
    )
    decision["business"] = business
    decision["run"] = run
    decision["products"] = product_summary
    return decision


def generate_business_reply(conversation, message_text, phone):
    business = BusinessClient.objects.get(slug=PEEKO_BUSINESS_SLUG, status="active")
    reply = build_peeko_reply(business, conversation, message_text)
    products = reply["products"]
    primary_product = products[0] if products else None
    product_focus = f"business:{business.slug}:{primary_product.slug}" if primary_product else f"business:{business.slug}"

    if conversation.product_detected != product_focus:
        conversation.product_detected = product_focus
        conversation.save(update_fields=["product_detected", "updated_at"])

    now = timezone.now()
    product_summary = summarize_products(products, reply["buttons"])
    selected_product = reply.get("selected_product")
    run = AiResponseProcessRun.objects.create(
        run_id=uuid.uuid4(),
        conversation_id=conversation.conversation_id,
        product_id=product_focus,
        client_message=message_text,
        status="approved_review_only",
        final_action=reply["intent"],
        final_score=95,
        final_body=reply["body"][:1800],
        final_buttons=reply["buttons"],
        attempts_count=1,
        error=None,
        created_at=now,
        finished_at=now,
    )
    AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=run,
        conversation_id=conversation.conversation_id,
        product_id=product_focus,
        step_name="peeko_trending_catalog_agent",
        attempt=1,
        input_json={
            "business": business.slug,
            "message": message_text,
            "phone_digits": digits(phone),
            "mode": "trending_site_handoff",
            "language": "en",
        },
        output_json={
            "intent": reply["intent"],
            "buttons": reply["buttons"],
            "link_buttons": reply.get("link_buttons") or [],
            "products": product_summary,
            "selected_product": selected_product,
            "ranking_source": "business_product_rankings.best_seller_from_trending",
        },
        approved=True,
        score=95,
        severity="info",
        action=reply["intent"],
        fail_reasons=[],
        blocking_issues=[],
        feedback_for_repair="",
        created_at=now,
    )
    return {
        "business": business,
        "product": primary_product,
        "product_focus": product_focus,
        "body": reply["body"][:1800],
        "buttons": reply["buttons"],
        "link_buttons": reply.get("link_buttons") or [],
        "run": run,
        "language_code": "en",
        "knowledge_count": 0,
        "media_count": sum(1 for item in product_summary if item.get("image_url")),
        "products": product_summary,
    }
