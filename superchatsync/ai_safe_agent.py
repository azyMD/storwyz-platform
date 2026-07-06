import os
import json
import re
import urllib.error
import urllib.request
import uuid

from django.utils import timezone

from productfeed.models import Offer, ObjectionRule, Product, ProductFAQ, ProductSalesRule
from superchatsync.models import (
    AiResponseProcessRun,
    AiResponseProcessStep,
    Conversation,
    ProductKnowledgeItem,
)
from superchatsync.conversation_context import (
    build_conversation_context,
    context_for_prompt,
    detect_language_from_texts,
    evaluate_repetition,
)
from superchatsync.response_enrichment import build_response_enrichment


PRICE_WORDS = (
    "pret", "preț", "costa", "costă", "cat costa", "cât costă", "lei", "ron",
    "price", "cost", "how much", "цена", "сколько", "стоит", "коштує",
    "precio", "cuanto", "cuánto", "prezzo", "prix", "preis",
)
DELIVERY_WORDS = (
    "livrare", "curier", "ajunge", "transport", "delivery", "shipping", "courier",
    "доставка", "курьер", "envio", "envío", "entrega", "consegna", "livraison",
    "lieferung", "versand",
)
ORDER_WORDS = (
    "comanda", "comandă", "vreau", "doresc", "cumpar", "cumpăr", "order",
    "place order", "buy", "purchase", "заказ", "заказать", "купить", "замовити",
    "comprar", "pedido", "ordinare", "commande", "commander", "bestellen",
)
DETAIL_WORDS = (
    "detalii", "mai multe", "stiu", "știu", "despre", "informatii", "informații",
    "material", "calitate", "rezistent", "dimensiune", "spune-mi",
    "grătar", "gratar", "bucătărie", "bucatarie", "cadou", "carne",
    "details", "more details", "about", "info", "information", "quality", "material",
    "grill", "grilling", "kitchen", "gift", "parrilla", "cocina", "regalo",
    "подроб", "детал", "качество", "кухн", "грил", "подар",
    "деталі", "dettagli", "detalles", "détails", "details",
)
GENERIC_WANT_WORDS = (
    "vreau", "doresc", "quiero", "i want", "хочу", "je veux", "voglio", "ich möchte",
)
ORDER_COMMITMENT_PATTERN = (
    r"\b(?:\d+\s*(?:buc|bucat[ăa]|bucăți|bucati|pcs|шт|uds|pz)|confirm|confirmă|"
    r"premium|standard|set|comand|cump[ăa]r|buy|purchase|pedido|comprar|"
    r"заказ|заказать|купить|замовити|commande|commander|ordinare|bestellen)\b"
)
CORROSION_WORDS = (
    "rugine", "rugini", "rugină", "coroziune", "rust", "corrosion", "oxidation",
    "ржав", "корроз", "oxidación", "corrosione", "rouille", "rost",
)
OFFER_WORDS = (
    "oferta", "ofertă", "promoție", "promotie", "reducere", "offer", "discount",
    "promo", "скидка", "акция", "знижка", "oferta", "offerta", "réduction",
    "angebot", "rabatt",
)
FAQ_STOP_WORDS = {
    "a", "ai", "al", "ale", "are", "care", "ce", "cu", "cutit", "cutitul",
    "de", "din", "este", "in", "la", "o", "pentru", "premium", "produs", "produsul",
    "satar", "si", "sunt", "un", "unei", "versiune", "versiunea", "the", "for",
    "and", "with", "product", "please", "about",
}
INTERNAL_SOURCE_PATTERNS = (
    r"\bdocumentul\s+spune\b",
    r"\bconform\s+documentului\b",
    r"\binformațiile\s+din\s+document\b",
    r"\binformațiile\s+disponibile\s+(?:arată|indică|spun)\b",
    r"\bthe\s+document\s+says\b",
    r"\baccording\s+to\s+the\s+document\b",
    r"\bavailable\s+information\s+(?:shows|indicates|says)\b",
    r"\bв\s+документе\s+(?:сказано|указано)\b",
    r"\bсогласно\s+документу\b",
    r"\bдоступная\s+информация\s+(?:показывает|указывает)\b",
    r"\bretrieved_knowledge\b",
)
ABSOLUTE_RUST_PATTERNS = (
    r"\bnu\s+va\s+rugini\s+niciodat[ăa]\b",
    r"\bnu\s+ruginește\s+niciodat[ăa]\b",
    r"\bwill\s+never\s+rust\b",
    r"\bnever\s+rusts\b",
    r"\bникогда\s+не\s+(?:заржавеет|ржавеет)\b",
)
DISCOUNT_AMOUNT_PATTERN = r"\b(?:reducere[^\n.]{0,50}50\s*ron|50\s*ron[^\n.]{0,50}reducere)\b"
UNSUPPORTED_BUNDLE_PRICE_PATTERN = (
    r"\b(?:[3-9]|[1-9]\d+)\s*(?:buc|bucat[ăa]|bucăți|bucati|pcs?|шт|uds?\.?|pz\.?|stk)\b"
    r"[^\n.;!?]{0,60}\b(?:ron|lei|eur|€|usd|\$)\b"
    r"|"
    r"\b(?:ron|lei|eur|€|usd|\$)\b[^\n.;!?]{0,60}"
    r"\b(?:[3-9]|[1-9]\d+)\s*(?:buc|bucat[ăa]|bucăți|bucati|pcs?|шт|uds?\.?|pz\.?|stk)\b"
)
UNVERIFIED_NAME_GREETING_PATTERN = r"^(?:salut|bună(?:\s+ziua)?)\s*,\s*[^!?\n]{2,50}!\s*"
DIACRITIC_TRANSLATION = str.maketrans(
    "ăâîșțĂÂÎȘȚşţŞŢ",
    "aaistAAISTstST",
)

LANGUAGE_FALLBACKS = {
    "ro": {
        "missing_product": "Mulțumim pentru mesaj. Ca să te pot ajuta corect, am nevoie să știu despre ce produs este vorba.",
        "offer_intro": "Pentru {product_name}, variantele disponibile sunt:",
        "offer_tail": "Îmi poți spune câte bucăți dorești și confirmăm comanda.",
        "delivery_unknown": "livrarea se confirmă în momentul comenzii",
        "delivery_tail": "Dacă dorești, pot continua cu confirmarea comenzii.",
        "order": "Perfect, pot continua comanda pentru {product_name}. Verific datele salvate și îți trimit rezumatul comenzii; dacă ceva nu e corect, îl poți corecta în răspuns.",
        "faq_tail": "Dacă dorești, îți pot arăta și cum se folosește sau detaliile de livrare.",
        "benefits": "{product_name} poate fi potrivit pentru tine: {benefits}\n\nTe interesează mai mult utilizarea, calitatea sau livrarea?",
        "generic": "Pot să te ajut cu detalii despre {product_name}. Pentru ce vrei să îl folosești?",
        "soft_corrosion": "are rezistență foarte bună la coroziune",
        "offer_phrase": "ofertă disponibilă",
        "media": "Îți trimit și un {media_word} scurt cu {product_name}, ca să vezi mai clar produsul în utilizare.",
        "media_tail": "După ce îl vezi, poți alege oferta: {offers}.",
        "media_cta": "Dacă îți place, putem trece direct la comandă.",
        "two_units": "2 bucăți",
    },
    "en": {
        "missing_product": "Thanks for your message. To help correctly, I need to know which product you mean.",
        "offer_intro": "For {product_name}, the available options are:",
        "offer_tail": "Tell me how many pieces you want and we can confirm the order.",
        "delivery_unknown": "delivery is confirmed when the order is placed",
        "delivery_tail": "If you want, I can continue with the order confirmation.",
        "order": "Perfect, I can continue the order for {product_name}. I will use the saved details and send the order summary; if anything is incorrect, you can correct it in reply.",
        "faq_tail": "If you want, I can also show how it is used or the delivery details.",
        "benefits": "{product_name} could be a good option for you: {benefits}\n\nAre you more interested in usage, quality, or delivery?",
        "generic": "I can help with details about {product_name}. What do you want to use it for?",
        "soft_corrosion": "has very good corrosion resistance",
        "offer_phrase": "available offer",
        "media": "I am also sending a short {media_word} with {product_name}, so you can see the product more clearly in use.",
        "media_tail": "After you see it, you can choose the offer: {offers}.",
        "media_cta": "If you like it, we can go straight to the order.",
        "two_units": "2 pcs",
    },
    "ru": {
        "missing_product": "Спасибо за сообщение. Чтобы помочь правильно, уточните, пожалуйста, о каком товаре идет речь.",
        "offer_intro": "Для {product_name} доступны такие варианты:",
        "offer_tail": "Напишите, сколько штук хотите, и мы подтвердим заказ.",
        "delivery_unknown": "доставка подтверждается при оформлении заказа",
        "delivery_tail": "Если хотите, я могу продолжить оформление заказа.",
        "order": "Отлично, могу продолжить заказ на {product_name}. Проверю сохраненные данные и отправлю сводку заказа; если что-то неверно, можно исправить в ответе.",
        "faq_tail": "Если хотите, могу также отправить доступное предложение.",
        "benefits": "{product_name} может быть хорошим вариантом для вас: {benefits}\n\nОтправить доступное предложение?",
        "generic": "Я могу помочь с деталями о {product_name}. Хотите узнать цену, доставку или доступное предложение?",
        "soft_corrosion": "имеет очень хорошую устойчивость к коррозии",
        "offer_phrase": "доступное предложение",
        "media": "Отправляю короткий {media_word} с {product_name}, чтобы вы лучше увидели товар в использовании.",
        "media_tail": "После просмотра можно выбрать предложение: {offers}.",
        "media_cta": "Если понравится, можем сразу перейти к заказу.",
        "two_units": "2 шт.",
    },
    "uk": {
        "missing_product": "Дякуємо за повідомлення. Щоб допомогти правильно, уточніть, будь ласка, про який товар йдеться.",
        "offer_intro": "Для {product_name} доступні такі варіанти:",
        "offer_tail": "Напишіть, скільки штук хочете, і ми підтвердимо замовлення.",
        "delivery_unknown": "доставка підтверджується під час оформлення замовлення",
        "delivery_tail": "Якщо хочете, я можу продовжити оформлення замовлення.",
        "order": "Добре, можу продовжити замовлення на {product_name}. Перевірю збережені дані й надішлю підсумок замовлення; якщо щось неправильно, можна виправити у відповіді.",
        "faq_tail": "Якщо хочете, можу також надіслати доступну пропозицію.",
        "benefits": "{product_name} може бути хорошим варіантом для вас: {benefits}\n\nНадіслати доступну пропозицію?",
        "generic": "Я можу допомогти з деталями про {product_name}. Хочете ціну, доставку чи доступну пропозицію?",
        "soft_corrosion": "має дуже хорошу стійкість до корозії",
        "offer_phrase": "доступна пропозиція",
        "media": "Надсилаю короткий {media_word} з {product_name}, щоб ви краще побачили товар у використанні.",
        "media_tail": "Після перегляду можна обрати пропозицію: {offers}.",
        "media_cta": "Якщо сподобається, можемо одразу перейти до замовлення.",
        "two_units": "2 шт.",
    },
    "es": {
        "missing_product": "Gracias por tu mensaje. Para ayudarte bien, necesito saber de qué producto se trata.",
        "offer_intro": "Para {product_name}, las opciones disponibles son:",
        "offer_tail": "Dime cuántas unidades quieres y confirmamos el pedido.",
        "delivery_unknown": "la entrega se confirma al hacer el pedido",
        "delivery_tail": "Si quieres, puedo continuar con la confirmación del pedido.",
        "order": "Perfecto, puedo continuar el pedido de {product_name}. Usaré los datos guardados y enviaré el resumen; si algo no está correcto, puedes corregirlo en la respuesta.",
        "faq_tail": "Si quieres, también puedo enviarte la oferta disponible.",
        "benefits": "{product_name} puede ser una buena opción para ti: {benefits}\n\nQuieres que te envíe la oferta disponible?",
        "generic": "Puedo ayudarte con detalles sobre {product_name}. Quieres ver el precio, la entrega o la oferta disponible?",
        "soft_corrosion": "tiene muy buena resistencia a la corrosión",
        "offer_phrase": "oferta disponible",
        "media": "También te envío un {media_word} corto con {product_name}, para que veas mejor el producto en uso.",
        "media_tail": "Después de verlo, puedes elegir la oferta: {offers}.",
        "media_cta": "Si te gusta, podemos pasar directamente al pedido.",
        "two_units": "2 uds.",
    },
    "it": {
        "missing_product": "Grazie per il messaggio. Per aiutarti correttamente, devo sapere di quale prodotto si tratta.",
        "offer_intro": "Per {product_name}, le opzioni disponibili sono:",
        "offer_tail": "Dimmi quante unità desideri e confermiamo l'ordine.",
        "delivery_unknown": "la consegna viene confermata al momento dell'ordine",
        "delivery_tail": "Se vuoi, posso continuare con la conferma dell'ordine.",
        "order": "Perfetto, posso continuare l'ordine per {product_name}. Userò i dati salvati e invierò il riepilogo; se qualcosa non è corretto, puoi correggerlo nella risposta.",
        "faq_tail": "Se vuoi, posso inviarti anche l'offerta disponibile.",
        "benefits": "{product_name} può essere una buona opzione per te: {benefits}\n\nVuoi che ti invii l'offerta disponibile?",
        "generic": "Posso aiutarti con i dettagli su {product_name}. Vuoi vedere prezzo, consegna o offerta disponibile?",
        "soft_corrosion": "ha un'ottima resistenza alla corrosione",
        "offer_phrase": "offerta disponibile",
        "media": "Ti invio anche un breve {media_word} con {product_name}, così puoi vedere meglio il prodotto in uso.",
        "media_tail": "Dopo averlo visto, puoi scegliere l'offerta: {offers}.",
        "media_cta": "Se ti piace, possiamo passare direttamente all'ordine.",
        "two_units": "2 pz.",
    },
    "fr": {
        "missing_product": "Merci pour votre message. Pour bien vous aider, j'ai besoin de savoir de quel produit il s'agit.",
        "offer_intro": "Pour {product_name}, les options disponibles sont:",
        "offer_tail": "Dites-moi combien de pièces vous souhaitez et nous confirmons la commande.",
        "delivery_unknown": "la livraison est confirmée au moment de la commande",
        "delivery_tail": "Si vous voulez, je peux continuer avec la confirmation de la commande.",
        "order": "Parfait, je peux continuer la commande pour {product_name}. J'utiliserai les données enregistrées et j'enverrai le résumé; si quelque chose est incorrect, vous pouvez le corriger en réponse.",
        "faq_tail": "Si vous voulez, je peux aussi vous envoyer l'offre disponible.",
        "benefits": "{product_name} peut être une bonne option pour vous: {benefits}\n\nVoulez-vous que je vous envoie l'offre disponible?",
        "generic": "Je peux vous aider avec les détails sur {product_name}. Voulez-vous le prix, la livraison ou l'offre disponible?",
        "soft_corrosion": "a une très bonne résistance à la corrosion",
        "offer_phrase": "offre disponible",
        "media": "Je vous envoie aussi un court {media_word} avec {product_name}, pour mieux voir le produit en utilisation.",
        "media_tail": "Après l'avoir vu, vous pouvez choisir l'offre: {offers}.",
        "media_cta": "Si cela vous convient, nous pouvons passer directement à la commande.",
        "two_units": "2 pcs",
    },
    "de": {
        "missing_product": "Danke für Ihre Nachricht. Damit ich richtig helfen kann, muss ich wissen, um welches Produkt es geht.",
        "offer_intro": "Für {product_name} sind diese Optionen verfügbar:",
        "offer_tail": "Sagen Sie mir, wie viele Stück Sie möchten, und wir bestätigen die Bestellung.",
        "delivery_unknown": "die Lieferung wird bei der Bestellung bestätigt",
        "delivery_tail": "Wenn Sie möchten, kann ich mit der Bestellbestätigung fortfahren.",
        "order": "Perfekt, ich kann die Bestellung für {product_name} fortsetzen. Ich nutze die gespeicherten Daten und sende die Zusammenfassung; falls etwas nicht stimmt, können Sie es in der Antwort korrigieren.",
        "faq_tail": "Wenn Sie möchten, kann ich Ihnen auch das verfügbare Angebot senden.",
        "benefits": "{product_name} kann eine gute Option für Sie sein: {benefits}\n\nSoll ich Ihnen das verfügbare Angebot senden?",
        "generic": "Ich kann Ihnen mit Details zu {product_name} helfen. Möchten Sie Preis, Lieferung oder verfügbares Angebot sehen?",
        "soft_corrosion": "hat eine sehr gute Korrosionsbeständigkeit",
        "offer_phrase": "verfügbares Angebot",
        "media": "Ich sende Ihnen auch ein kurzes {media_word} mit {product_name}, damit Sie das Produkt in der Anwendung besser sehen.",
        "media_tail": "Nach dem Ansehen können Sie das Angebot wählen: {offers}.",
        "media_cta": "Wenn es Ihnen gefällt, können wir direkt zur Bestellung gehen.",
        "two_units": "2 Stk.",
    },
}
ONE_UNIT_LABELS = {
    "ro": "1 bucată",
    "en": "1 pc",
    "ru": "1 шт.",
    "uk": "1 шт.",
    "es": "1 ud.",
    "it": "1 pz.",
    "fr": "1 pc",
    "de": "1 Stk.",
}


def normalize_text(value):
    return (value or "").strip()


def _language_code(conversation_context=None):
    code = str((conversation_context or {}).get("language_code") or "ro").lower()
    return code if code in LANGUAGE_FALLBACKS else "ro"


def _fallbacks(conversation_context=None):
    return LANGUAGE_FALLBACKS[_language_code(conversation_context)]


def _quantity_label(quantity, language_code, labels):
    try:
        quantity = int(quantity or 0)
    except (TypeError, ValueError):
        quantity = 0
    if quantity == 1:
        return ONE_UNIT_LABELS.get(language_code, ONE_UNIT_LABELS["ro"])
    if quantity == 2:
        return labels["two_units"]
    return ""


EARLY_DISCOVERY_PROMPTS = {
    "ro": "Pot să te ajut cu detalii despre {product_name}. Pentru ce vrei să îl folosești?",
    "en": "I can help with details about {product_name}. What do you want to use it for?",
    "ru": "Могу помочь с деталями о {product_name}. Для чего вы хотите его использовать?",
    "uk": "Можу допомогти з деталями про {product_name}. Для чого хочете його використовувати?",
    "es": "Puedo ayudarte con detalles sobre {product_name}. Para qué quieres usarlo?",
    "it": "Posso aiutarti con i dettagli su {product_name}. Per cosa vuoi usarlo?",
    "fr": "Je peux vous aider avec les détails sur {product_name}. Pour quel usage le souhaitez-vous?",
    "de": "Ich kann Ihnen mit Details zu {product_name} helfen. Wofür möchten Sie es verwenden?",
}


LOOP_BREAKER_PROMPTS = {
    "ro": "Ca să nu repet aceleași detalii despre {product_name}: îl vrei mai mult pentru grătar, bucătărie sau cadou?",
    "en": "To avoid repeating the same details about {product_name}: is it mainly for grilling, kitchen use, or a gift?",
    "ru": "Чтобы не повторять те же детали о {product_name}: вам он нужен больше для гриля, кухни или в подарок?",
    "uk": "Щоб не повторювати ті самі деталі про {product_name}: він потрібен більше для гриля, кухні чи як подарунок?",
    "es": "Para no repetir los mismos detalles sobre {product_name}: lo quieres más para parrilla, cocina o regalo?",
    "it": "Per non ripetere gli stessi dettagli su {product_name}: ti serve più per griglia, cucina o regalo?",
    "fr": "Pour éviter de répéter les mêmes détails sur {product_name}: c'est plutôt pour le barbecue, la cuisine ou un cadeau?",
    "de": "Damit ich nicht dieselben Details zu {product_name} wiederhole: eher für Grill, Küche oder als Geschenk?",
}


LOOP_BREAKER_AFTER_USE_PROMPTS = {
    "ro": "Am notat: pentru {use_context}. Ca să nu repet aceleași detalii despre {product_name}, pot continua cu livrarea sau cu diferența față de un cuțit obișnuit.",
    "en": "Got it: for {use_context}. To avoid repeating the same details about {product_name}, I can continue with delivery or what makes it different from a regular knife.",
    "ru": "Понял: для {use_context}. Чтобы не повторять те же детали о {product_name}, могу продолжить с доставкой или отличиями от обычного ножа.",
    "uk": "Зрозуміло: для {use_context}. Щоб не повторювати ті самі деталі про {product_name}, можу продовжити з доставкою або відмінностями від звичайного ножа.",
    "es": "Entendido: para {use_context}. Para no repetir los mismos detalles sobre {product_name}, puedo seguir con la entrega o con la diferencia frente a un cuchillo normal.",
    "it": "Capito: per {use_context}. Per non ripetere gli stessi dettagli su {product_name}, posso continuare con la consegna o con le differenze rispetto a un coltello normale.",
    "fr": "Compris: pour {use_context}. Pour éviter de répéter les mêmes détails sur {product_name}, je peux continuer avec la livraison ou la différence avec un couteau classique.",
    "de": "Verstanden: für {use_context}. Damit ich dieselben Details zu {product_name} nicht wiederhole, kann ich mit Lieferung oder dem Unterschied zu einem normalen Messer fortfahren.",
}
FLOW_TRANSITION_PROMPTS = {
    "ro": {
        "with_use": "Pentru {use_context}, {product_name} este potrivit ca variantă practică și rezistentă. Următorul pas util este să alegem varianta potrivită sau să verificăm detaliile de livrare.",
        "generic": "Am acoperit deja detaliile principale despre {product_name}. Următorul pas util este să alegem varianta potrivită sau să verificăm detaliile de livrare.",
    },
    "en": {
        "with_use": "For {use_context}, {product_name} is a practical and resistant option. The useful next step is to choose the right variant or check delivery details.",
        "generic": "We have already covered the main details about {product_name}. The useful next step is to choose the right variant or check delivery details.",
    },
    "ru": {
        "with_use": "Для {use_context} {product_name} подходит как практичный и прочный вариант. Следующий полезный шаг — выбрать подходящую версию или проверить детали доставки.",
        "generic": "Основные детали о {product_name} уже разобрали. Следующий полезный шаг — выбрать подходящую версию или проверить детали доставки.",
    },
    "uk": {
        "with_use": "Для {use_context} {product_name} підходить як практичний і міцний варіант. Наступний корисний крок — вибрати відповідну версію або перевірити деталі доставки.",
        "generic": "Основні деталі про {product_name} уже розібрали. Наступний корисний крок — вибрати відповідну версію або перевірити деталі доставки.",
    },
    "es": {
        "with_use": "Para {use_context}, {product_name} es una opción práctica y resistente. El siguiente paso útil es elegir la variante adecuada o revisar los detalles de entrega.",
        "generic": "Ya cubrimos los detalles principales de {product_name}. El siguiente paso útil es elegir la variante adecuada o revisar los detalles de entrega.",
    },
    "it": {
        "with_use": "Per {use_context}, {product_name} è un'opzione pratica e resistente. Il passo utile successivo è scegliere la variante adatta o controllare i dettagli di consegna.",
        "generic": "Abbiamo già coperto i dettagli principali su {product_name}. Il passo utile successivo è scegliere la variante adatta o controllare i dettagli di consegna.",
    },
    "fr": {
        "with_use": "Pour {use_context}, {product_name} est une option pratique et résistante. La prochaine étape utile est de choisir la variante adaptée ou de vérifier les détails de livraison.",
        "generic": "Nous avons déjà couvert les principaux détails de {product_name}. La prochaine étape utile est de choisir la variante adaptée ou de vérifier les détails de livraison.",
    },
    "de": {
        "with_use": "Für {use_context} ist {product_name} eine praktische und widerstandsfähige Option. Der nächste sinnvolle Schritt ist die passende Variante oder die Lieferdetails.",
        "generic": "Die wichtigsten Details zu {product_name} haben wir bereits abgedeckt. Der nächste sinnvolle Schritt ist die passende Variante oder die Lieferdetails.",
    },
}
USE_CONTEXT_LABELS = {
    "grill": {
        "ro": "grătar", "en": "grilling", "ru": "гриля", "uk": "гриля",
        "es": "parrilla", "it": "griglia", "fr": "barbecue", "de": "Grill",
    },
    "kitchen": {
        "ro": "bucătărie", "en": "kitchen use", "ru": "кухни", "uk": "кухні",
        "es": "cocina", "it": "cucina", "fr": "cuisine", "de": "Küche",
    },
    "gift": {
        "ro": "cadou", "en": "a gift", "ru": "подарка", "uk": "подарунка",
        "es": "regalo", "it": "regalo", "fr": "cadeau", "de": "Geschenk",
    },
}


def _latest_client_text(conversation_context=None):
    for item in reversed((conversation_context or {}).get("recent_dialogue") or []):
        if item.get("role") == "client":
            return str(item.get("text") or "").casefold()
    return ""


def _selected_use_context(conversation_context=None):
    context_value = (
        (conversation_context or {}).get("current_use_context")
        or (conversation_context or {}).get("selected_use_context")
    )
    if context_value:
        return context_value
    text = _latest_client_text(conversation_context)
    if re.search(r"\b(gr[aă]tar|grill|grilling|parrilla|грил)", text, flags=re.IGNORECASE):
        return "grill"
    if re.search(r"\b(buc[aă]t[aă]rie|kitchen|cocina|cucina|кухн)", text, flags=re.IGNORECASE):
        return "kitchen"
    if re.search(r"\b(cadou|gift|regalo|подар)", text, flags=re.IGNORECASE):
        return "gift"
    return None


def _price_exposure_allowed(conversation_context=None):
    return bool((conversation_context or {}).get("price_exposure_allowed"))


def _price_content_allowed(intent, conversation_context=None):
    if intent in {"asks_price", "asks_offer", "wants_to_order"}:
        return True
    stage = str((conversation_context or {}).get("journey_stage") or "")
    return _price_exposure_allowed(conversation_context) and stage in {"desire_offer", "action_lead"}


def loop_breaker_body(product, conversation_context=None):
    language_code = _language_code(conversation_context)
    product_name = product.product_name if product else "produsul"
    use_context = _selected_use_context(conversation_context)
    if use_context:
        label = USE_CONTEXT_LABELS.get(use_context, {}).get(language_code) or USE_CONTEXT_LABELS[use_context]["ro"]
        return LOOP_BREAKER_AFTER_USE_PROMPTS.get(
            language_code,
            LOOP_BREAKER_AFTER_USE_PROMPTS["ro"],
        ).format(product_name=product_name, use_context=label)
    return LOOP_BREAKER_PROMPTS.get(language_code, LOOP_BREAKER_PROMPTS["ro"]).format(
        product_name=product_name
    )


def _should_force_flow_transition(intent, conversation_context=None):
    if intent not in {"asks_product_details", "asks_question", "general_reply"}:
        return False
    return bool((conversation_context or {}).get("details_path_exhausted"))


def flow_transition_body(product, conversation_context=None):
    language_code = _language_code(conversation_context)
    product_name = product.product_name if product else "produsul"
    prompts = FLOW_TRANSITION_PROMPTS.get(language_code, FLOW_TRANSITION_PROMPTS["ro"])
    use_context = _selected_use_context(conversation_context)
    if use_context:
        label = USE_CONTEXT_LABELS.get(use_context, {}).get(language_code) or USE_CONTEXT_LABELS[use_context]["ro"]
        return prompts["with_use"].format(product_name=product_name, use_context=label)
    return prompts["generic"].format(product_name=product_name)


def tokenize(value):
    normalized = normalize_text(value).translate(DIACRITIC_TRANSLATION).lower()
    return set(re.findall(r"\w+", normalized))


def latest_client_message(conversation):
    return (
        conversation.messages
        .filter(is_client_reply=True)
        .exclude(message_text__isnull=True)
        .order_by("-sent_at")
        .first()
    )


def detect_intent(message_text):
    text = normalize_text(message_text).lower()

    if any(word in text for word in CORROSION_WORDS):
        return "asks_corrosion"
    if any(word in text for word in PRICE_WORDS):
        return "asks_price"
    if any(word in text for word in OFFER_WORDS):
        return "asks_offer"
    if any(word in text for word in DELIVERY_WORDS):
        return "asks_delivery"
    if any(word in text for word in DETAIL_WORDS):
        return "asks_product_details"
    strong_order_words = [word for word in ORDER_WORDS if word not in GENERIC_WANT_WORDS]
    has_strong_order = any(word in text for word in strong_order_words)
    has_generic_want_commitment = (
        any(word in text for word in GENERIC_WANT_WORDS)
        and re.search(ORDER_COMMITMENT_PATTERN, text, flags=re.IGNORECASE)
    )
    if has_strong_order or has_generic_want_commitment:
        return "wants_to_order"
    if "?" in text:
        return "asks_question"
    return "general_reply"


def resolve_product(conversation):
    product_id = normalize_text(conversation.product_detected) or os.getenv("AI_DEFAULT_PRODUCT_ID")
    if not product_id:
        return None
    return Product.objects.filter(product_id=product_id, active=True).first()


def _offer_quantity_value(offer):
    try:
        return int(getattr(offer, "quantity", None) or 0)
    except (TypeError, ValueError):
        return 0


def _customer_facing_offers(offers):
    preferred = [offer for offer in offers if _offer_quantity_value(offer) in {1, 2}]
    if preferred:
        return preferred[:2]
    return offers[:2]


def relevant_faqs(product, message_text, intent, limit=3):
    if not product:
        return []

    message_tokens = tokenize(message_text) - FAQ_STOP_WORDS
    intent_anchors = {
        "asks_price": {"pret", "costa", "cost", "ron", "lei", "price", "цена", "precio", "prezzo", "prix", "preis"},
        "asks_offer": {"oferta", "reducere", "promotie", "pret", "costa", "ron", "lei", "offer", "discount", "скидка", "oferta", "offerta", "rabatt"},
        "asks_delivery": {"livrare", "curier", "transport", "ajunge", "delivery", "shipping", "courier", "доставка", "envio", "consegna", "livraison", "lieferung"},
        "asks_corrosion": {"rugini", "rugineste", "rugina", "coroziune", "oxidare", "rust", "corrosion", "ржавчина", "коррозия"},
        "asks_product_details": {"material", "duritate", "greutate", "calitate", "details", "quality", "hardness", "подробности", "детали"},
    }.get(intent, set())
    scored = []

    for faq in ProductFAQ.objects.filter(product=product, active=True):
        haystack = tokenize(f"{faq.question} {faq.answer}") - FAQ_STOP_WORDS
        anchor_match = bool(intent_anchors & haystack)
        if intent_anchors and not anchor_match:
            continue
        score = len(message_tokens & haystack)
        if anchor_match:
            score += 4
        if score:
            scored.append((score, faq))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [faq for _, faq in scored[:limit]]


def retrieve_knowledge(product, intent, message_text):
    if not product:
        return {
            "product": None,
            "offers": [],
            "faqs": [],
            "objections": [],
            "sales_rules": [],
            "knowledge_items": [],
        }

    offer_rows = list(Offer.objects.filter(product=product, active=True).order_by("quantity", "price")[:8])
    offers = _customer_facing_offers(offer_rows)
    faqs = relevant_faqs(product, message_text, intent)
    objections = list(ObjectionRule.objects.filter(product=product, active=True).order_by("priority")[:3])
    sales_rules = list(ProductSalesRule.objects.filter(product=product, active=True).order_by("priority")[:5])
    knowledge_items = list(
        ProductKnowledgeItem.objects
        .filter(product=product, status__in=["applied", "approved"])
        .filter(category__in=["product_facts", "offers", "sales_rules", "workflow_rules"])
        .order_by("-priority", "-confidence_score")[:8]
    )

    return {
        "product": {
            "product_id": product.product_id,
            "product_name": product.product_name,
            "short_description": product.short_description,
            "main_benefits": product.main_benefits,
            "delivery_info": product.delivery_info,
            "payment_info": product.payment_info,
            "warranty_info": product.warranty_info,
        },
        "offers": [
            {
                "offer_name": offer.offer_name,
                "variant": offer.variant,
                "quantity": offer.quantity,
                "price": str(offer.price) if offer.price is not None else None,
                "currency": offer.currency,
                "delivery_offer": offer.delivery_offer,
                "payment_method": offer.payment_method,
            }
            for offer in offers
        ],
        "offer_policy": {
            "customer_offer_quantities": [1, 2],
            "max_customer_options": 2,
            "ignore_other_bundle_prices": True,
        },
        "faqs": [
            {
                "question": faq.question,
                "answer": faq.answer,
            }
            for faq in faqs
        ],
        "objections": [
            {
                "objection_type": item.objection_type,
                "recommended_answer": item.recommended_answer,
                "next_action": item.next_action,
            }
            for item in objections
        ],
        "sales_rules": [
            {
                "trigger": item.trigger,
                "action": item.action,
                "instruction": item.instruction,
            }
            for item in sales_rules
        ],
        "knowledge_items": [
            {
                "category": item.category,
                "title": item.title,
                "answer": item.answer,
                "rule": item.rule,
                "description": item.description,
                "price": item.price,
            }
            for item in knowledge_items
        ],
    }


def draft_reply(product, intent, knowledge, conversation_context=None):
    labels = _fallbacks(conversation_context)
    language_code = _language_code(conversation_context)
    if not product:
        return labels["missing_product"]

    product_name = product.product_name
    offers = knowledge["offers"]
    faqs = knowledge["faqs"]
    product_info = knowledge["product"]

    if intent in {"asks_price", "asks_offer"} and offers:
        offer_lines = []
        for offer in offers[:2]:
            label = _quantity_label(offer.get("quantity"), language_code, labels)
            label = label or offer["variant"] or offer["offer_name"]
            price = offer["price"]
            currency = offer["currency"] or "RON"
            if price:
                offer_lines.append(f"{label}: {_display_price(price)} {currency}")
        return (
            labels["offer_intro"].format(product_name=product_name)
            + "\n"
            + "\n".join(f"- {line}" for line in offer_lines)
            + "\n\n"
            + labels["offer_tail"]
        )

    if intent == "asks_delivery":
        delivery = product_info.get("delivery_info") if language_code == "ro" else None
        delivery = delivery or labels["delivery_unknown"]
        payment = product_info.get("payment_info")
        tail = f" Plata: {payment}." if payment and language_code == "ro" else ""
        return f"{product_name}: {delivery}.{tail} {labels['delivery_tail']}"

    if intent == "wants_to_order":
        return labels["order"].format(product_name=product_name)

    if not _price_content_allowed(intent, conversation_context):
        if faqs and language_code == "ro":
            answer = faqs[0]["answer"]
            return f"{answer}\n\n{labels['faq_tail']}"
        benefits = product_info.get("main_benefits") or product_info.get("short_description")
        if benefits and language_code == "ro":
            return labels["benefits"].format(product_name=product_name, benefits=benefits)
        return EARLY_DISCOVERY_PROMPTS.get(language_code, EARLY_DISCOVERY_PROMPTS["ro"]).format(
            product_name=product_name
        )

    if faqs and language_code == "ro":
        answer = faqs[0]["answer"]
        return f"{answer}\n\n{labels['faq_tail']}"

    benefits = product_info.get("main_benefits") or product_info.get("short_description")
    if benefits and language_code == "ro":
        return labels["benefits"].format(product_name=product_name, benefits=benefits)

    return labels["generic"].format(product_name=product_name)


def extract_response_text(data):
    if isinstance(data, dict) and data.get("output_text"):
        return normalize_text(data.get("output_text"))

    parts = []
    for output in data.get("output", []) if isinstance(data, dict) else []:
        for content in output.get("content", []) if isinstance(output, dict) else []:
            if isinstance(content, dict):
                text = content.get("text")
                if text:
                    parts.append(text)
    return normalize_text("\n".join(parts))


def call_llm_writer(
    product,
    intent,
    knowledge,
    message_text,
    conversation_context=None,
    draft_to_avoid=None,
):
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    if not api_key:
        return None

    prompt_context = context_for_prompt(conversation_context or {})
    language_code = prompt_context.get("language_code") or "ro"
    language_name = prompt_context.get("language_name") or "Romanian"
    language_instruction = prompt_context.get("language_instruction") or "Write only in Romanian."
    product_name = product.product_name if product else None
    context = {
        "customer_message": message_text,
        "intent": intent,
        "product_id": product.product_id if product else None,
        "product_name": product_name,
        "retrieved_knowledge": knowledge,
        "conversation_context": prompt_context,
        "draft_to_avoid": draft_to_avoid,
        "constraints": {
            "language_code": language_code,
            "language": language_name,
            "language_instruction": language_instruction,
            "journey_stage": prompt_context.get("journey_stage"),
            "engagement_depth": prompt_context.get("engagement_depth"),
            "price_exposure_allowed": prompt_context.get("price_exposure_allowed"),
            "price_content_allowed": _price_content_allowed(intent, conversation_context),
            "customer_offer_quantities": [1, 2],
            "max_customer_offer_options": 2,
            "detail_path_count": prompt_context.get("detail_path_count"),
            "details_path_exhausted": prompt_context.get("details_path_exhausted"),
            "selected_use_context": prompt_context.get("selected_use_context"),
            "allowed_cta_intents": prompt_context.get("allowed_cta_intents"),
            "blocked_cta_intents": prompt_context.get("blocked_cta_intents"),
            "channel": "WhatsApp",
            "max_characters": 650,
            "must_not_send": True,
            "use_only_retrieved_facts": True,
            "dynamic_product_only": True,
            "avoid_handoff_phrases": True,
            "style": "natural, concise, sales-supportive, not pushy",
        },
    }

    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You are a WhatsApp sales assistant. Write exactly one message to the customer. "
                    f"Write ONLY in {language_name}. {language_instruction} "
                    "Do not switch languages, even if retrieved knowledge is written in another language. "
                    "Use only retrieved_knowledge and the dynamic product_id/product_name supplied in the user payload. "
                    "Never assume any default product unless that exact product_name/product_id is present in the payload. "
                    "Do not invent prices, delivery terms, warranty details, stock, materials, or product characteristics. "
                    "Do not mention documents, internal sources, database records, or retrieved_knowledge. "
                    "Do not claim the product will never rust; if corrosion resistance exists, phrase it as very good corrosion resistance in the customer's language. "
                    "Do not convert percentage discounts into fixed amounts: 50% does not mean 50 RON. "
                    "Answer directly, without openings like 'About the product' or 'Available information shows'. "
                    "The first sentence must answer the customer's exact question; do not replace the answer with an unsolicited price or offer. "
                    "Respect constraints.price_content_allowed. If it is false, do not mention prices, discounts, offers, bundles, quantities, or ordering; instead build interest with usage, benefits, quality, delivery, or one short discovery question. "
                    "Do not treat conversation_context.price_exposure_allowed alone as permission to write actual prices; it can allow an offer CTA without exposing prices in the message body. "
                    "Only present price or offer when the customer explicitly asks for price/offer, shows order intent, or constraints.price_content_allowed is true. "
                    "When presenting prices or offers, mention only retrieved_knowledge.offers and at most the 1-piece and 2-piece customer options; ignore any 3+ piece bundle prices from other knowledge fields. "
                    "Do not use the customer's name unless a verified profile name is included in the payload. "
                    "Do not repeat the same feature in two formulations and do not copy whole knowledge-base phrases when a natural paraphrase is possible. "
                    "Use conversation_context for continuity. Do not repeat information, wording, or CTA ideas already used unless needed for the current question. "
                    "If the customer repeats the same question, acknowledge briefly and move toward next_best_action. Do not mention that you read the history. "
                    "If constraints.details_path_exhausted is true, do not repeat product specs again; transition to a concrete next step such as delivery, offer CTA, media/creative, or a specific unanswered question. "
                    "If draft_to_avoid is present, rewrite so it is not similar to that draft or previous_assistant_replies. "
                    "Do not say you are transferring to an operator. Do not actually send the message."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False),
            },
        ],
        "max_output_tokens": 350,
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    return extract_response_text(data)


def draft_reply_with_llm(product, intent, knowledge, message_text, conversation_context=None):
    if os.getenv("AI_SAFE_USE_LLM", "true").lower() in ("0", "false", "no"):
        return draft_reply(product, intent, knowledge, conversation_context), "deterministic"

    llm_body = call_llm_writer(
        product,
        intent,
        knowledge,
        message_text,
        conversation_context=conversation_context,
    )
    if llm_body:
        return llm_body, "llm"

    return draft_reply(product, intent, knowledge, conversation_context), "deterministic_fallback"


def sanitize_draft(body, language_code="ro"):
    original = normalize_text(body)
    sanitized = original
    changes = []
    labels = LANGUAGE_FALLBACKS.get(language_code, LANGUAGE_FALLBACKS["ro"])

    for pattern in INTERNAL_SOURCE_PATTERNS:
        updated = re.sub(
            rf"{pattern}\s*(?:că\s*)?",
            "",
            sanitized,
            flags=re.IGNORECASE,
        )
        if updated != sanitized:
            changes.append("removed_internal_source_phrase")
            sanitized = updated

    updated = re.sub(
        r"^Despre\s+[^:]{1,80}:\s*",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    if updated != sanitized:
        changes.append("removed_generic_product_prefix")
        sanitized = updated

    updated = re.sub(
        UNVERIFIED_NAME_GREETING_PATTERN,
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    if updated != sanitized:
        changes.append("removed_unverified_name_greeting")
        sanitized = updated

    updated = re.sub(
        r"rezistență\s+excepțională\s+la\s+coroziune",
        labels["soft_corrosion"],
        sanitized,
        flags=re.IGNORECASE,
    )
    if updated != sanitized:
        changes.append("softened_corrosion_claim")
        sanitized = updated

    for pattern in ABSOLUTE_RUST_PATTERNS:
        updated = re.sub(
            pattern,
            labels["soft_corrosion"],
            sanitized,
            flags=re.IGNORECASE,
        )
        if updated != sanitized:
            changes.append("softened_absolute_rust_claim")
            sanitized = updated

    corrosion_claim = r"are\s+(?:o\s+)?rezistență\s+foarte\s+bună\s+la\s+coroziune"
    updated = re.sub(
        rf"{corrosion_claim}(?:\s*,?\s*(?:și|iar)\s*(?:că\s*)?{corrosion_claim})+",
        "are rezistență foarte bună la coroziune",
        sanitized,
        flags=re.IGNORECASE,
    )
    if updated != sanitized:
        changes.append("removed_duplicate_corrosion_claim")
        sanitized = updated

    updated = re.sub(
        DISCOUNT_AMOUNT_PATTERN,
        labels["offer_phrase"],
        sanitized,
        flags=re.IGNORECASE,
    )
    if updated != sanitized:
        changes.append("removed_unsupported_discount_amount")
        sanitized = updated

    sanitized = re.sub(r"\s+([,.;!?])", r"\1", sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip()
    sanitized = re.sub(
        r"(^|[.!?]\s+)([a-zăâîșț])",
        lambda match: match.group(1) + match.group(2).upper(),
        sanitized,
    )
    return sanitized, sorted(set(changes))


def judge_draft(product, body, intent=None, conversation_context=None):
    blockers = []
    score = 90
    text = normalize_text(body).lower()
    expected_language = _language_code(conversation_context)

    if not body:
        blockers.append("empty_body")
        score = 0
    if len(body) > 900:
        blockers.append("body_too_long")
        score = min(score, 70)
    if not product:
        blockers.append("missing_product_context")
        score = min(score, 65)
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in INTERNAL_SOURCE_PATTERNS):
        blockers.append("mentions_internal_source")
        score = min(score, 65)
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in ABSOLUTE_RUST_PATTERNS):
        blockers.append("absolute_rust_claim")
        score = min(score, 65)
    if re.search(DISCOUNT_AMOUNT_PATTERN, text, flags=re.IGNORECASE):
        blockers.append("unsupported_discount_amount")
        score = min(score, 60)
    if intent in {"asks_price", "asks_offer"} and re.search(UNSUPPORTED_BUNDLE_PRICE_PATTERN, text, flags=re.IGNORECASE):
        blockers.append("unsupported_bundle_quantity")
        score = min(score, 60)
    if re.search(UNVERIFIED_NAME_GREETING_PATTERN, body, flags=re.IGNORECASE):
        blockers.append("unverified_name_greeting")
        score = min(score, 65)
    if len(re.findall(r"rezistență\s+(?:foarte\s+bună|excepțională)\s+la\s+coroziune", text)) > 1:
        blockers.append("repeated_claim")
        score = min(score, 65)
    if expected_language in {"ru", "uk"} and not re.search(r"[а-яёіїєґ]", text, flags=re.IGNORECASE):
        blockers.append("wrong_language")
        score = min(score, 55)
    if expected_language not in {"ru", "uk"} and re.search(r"[а-яёіїєґ]{4,}", text, flags=re.IGNORECASE):
        blockers.append("wrong_language")
        score = min(score, 55)
    if expected_language == "ro":
        detected = detect_language_from_texts([body], fallback="ro")
        if detected["code"] in {"en", "ru", "uk"} and detected["confidence"] >= 4:
            blockers.append("wrong_language")
            score = min(score, 60)
    if intent == "asks_corrosion":
        if not re.search(r"(corozi|rugin|corrosion|rust|корроз|ржав)", text, flags=re.IGNORECASE):
            blockers.append("missing_corrosion_answer")
            score = min(score, 55)
        if re.search(r"\b(?:ron|lei|preț|costă)\b", text):
            blockers.append("unrelated_price_for_corrosion_question")
            score = min(score, 60)
    if intent == "asks_price" and not re.search(r"\b(?:ron|lei)\b", text):
        blockers.append("missing_price_answer")
        score = min(score, 55)
    if intent == "asks_offer" and not re.search(r"\b(?:ofert|reducere|ron|lei|offer|discount|скид|акци|зниж|oferta|offerta|rabatt)\w*\b", text, flags=re.IGNORECASE):
        blockers.append("missing_offer_answer")
        score = min(score, 55)
    if intent == "asks_delivery" and not re.search(r"(livr|curier|delivery|shipping|courier|достав|курьер|env[ií]o|entrega|consegna|livraison|lieferung)", text, flags=re.IGNORECASE):
        blockers.append("missing_delivery_answer")
        score = min(score, 55)
    if not _price_content_allowed(intent, conversation_context):
        if re.search(r"\b(?:ron|lei|preț|pret|costă|costa|price|cost|ofert|oferta|ofertă|reducere|discount|comandă\s+acum|comanda\s+acum|order\s+now)\b", text, flags=re.IGNORECASE):
            blockers.append("price_or_offer_too_early")
            score = min(score, 60)

    approved = not blockers and score >= 80
    return {
        "approved": approved,
        "score": score,
        "blocking_issues": blockers,
        "feedback": "" if approved else "Draftul are nevoie de context suplimentar înainte de trimitere.",
    }


def log_step(run, name, attempt, input_json, output_json, approved=None, score=None, action=None, blockers=None, feedback=""):
    return AiResponseProcessStep.objects.create(
        step_id=uuid.uuid4(),
        run=run,
        conversation_id=run.conversation_id,
        product_id=run.product_id,
        step_name=name,
        attempt=attempt,
        input_json=input_json or {},
        output_json=output_json or {},
        approved=approved,
        score=score,
        severity="info" if approved or approved is None else "warning",
        action=action,
        fail_reasons=blockers or [],
        blocking_issues=blockers or [],
        feedback_for_repair=feedback,
        created_at=timezone.now(),
    )


def _display_price(value):
    text = str(value or "").strip()
    if text.endswith(".00"):
        return text[:-3]
    return text


def media_followup_body(product, knowledge, creative, conversation_context=None):
    labels = _fallbacks(conversation_context)
    language_code = _language_code(conversation_context)
    product_name = product.product_name if product else "produsul"
    creative_type = (creative or {}).get("asset_type")
    media_words = {
        "ro": ("video", "material"),
        "en": ("video", "media file"),
        "ru": ("видео", "материал"),
        "uk": ("відео", "матеріал"),
        "es": ("video", "material"),
        "it": ("video", "materiale"),
        "fr": ("vidéo", "support"),
        "de": ("Video", "Material"),
    }
    media_word = media_words.get(language_code, media_words["ro"])[0 if creative_type == "video" else 1]
    offers = knowledge.get("offers") or []
    offer_tail = ""
    if offers:
        prices = []
        for offer in offers[:2]:
            price = offer.get("price")
            currency = offer.get("currency") or "RON"
            quantity = offer.get("quantity")
            label = _quantity_label(quantity, language_code, labels)
            label = label or (offer.get("variant") or offer.get("offer_name") or "").strip()
            if price and label:
                prices.append(f"{label}: {_display_price(price)} {currency}")
        if prices:
            offer_tail = " " + labels["media_tail"].format(offers="; ".join(prices))
    return (
        labels["media"].format(media_word=media_word, product_name=product_name)
        + f"{offer_tail} "
        + labels["media_cta"]
    )


def generate_safe_ai_decision(conversation_id):
    conversation = Conversation.objects.get(conversation_id=conversation_id)
    client_message = latest_client_message(conversation)
    message_text = client_message.message_text if client_message else ""
    intent = detect_intent(message_text)
    product = resolve_product(conversation)
    knowledge = retrieve_knowledge(product, intent, message_text)
    conversation_context = build_conversation_context(
        conversation,
        intent,
        message_text,
    )
    language_code = conversation_context.get("language_code") or "ro"
    body, writer_mode = draft_reply_with_llm(
        product,
        intent,
        knowledge,
        message_text,
        conversation_context=conversation_context,
    )
    raw_body = body
    body, sanitizations = sanitize_draft(body, language_code)
    if _should_force_flow_transition(intent, conversation_context):
        transition_body, transition_sanitizations = sanitize_draft(
            flow_transition_body(product, conversation_context),
            language_code,
        )
        body = transition_body
        sanitizations = sorted(set(sanitizations + transition_sanitizations + ["forced_flow_transition"]))
        writer_mode = f"{writer_mode}_flow_transition"
    judge = judge_draft(product, body, intent, conversation_context)
    repair_blockers = []
    writer_attempts = 1

    if not judge["approved"] and writer_mode == "llm":
        repair_blockers = list(judge["blocking_issues"])
        fallback_body, fallback_sanitizations = sanitize_draft(
            draft_reply(product, intent, knowledge, conversation_context),
            language_code,
        )
        fallback_judge = judge_draft(product, fallback_body, intent, conversation_context)
        if fallback_judge["approved"]:
            body = fallback_body
            sanitizations = sorted(set(sanitizations + fallback_sanitizations))
            judge = fallback_judge
            writer_mode = "deterministic_repair"
            writer_attempts += 1

    repetition = evaluate_repetition(body, conversation_context)
    if repetition["repeated"]:
        repair_blockers.append("repetitive_response")
        repaired_body = call_llm_writer(
            product,
            intent,
            knowledge,
            message_text,
            conversation_context=conversation_context,
            draft_to_avoid=body,
        )
        writer_attempts += 1
        if repaired_body:
            repaired_body, repaired_sanitizations = sanitize_draft(repaired_body, language_code)
            repaired_judge = judge_draft(product, repaired_body, intent, conversation_context)
            repaired_repetition = evaluate_repetition(repaired_body, conversation_context)
            if repaired_judge["approved"] and not repaired_repetition["repeated"]:
                body = repaired_body
                sanitizations = sorted(set(sanitizations + repaired_sanitizations))
                judge = repaired_judge
                repetition = repaired_repetition
                writer_mode = "llm_repetition_repair"

    enrichment = build_response_enrichment(
        product.product_id if product else None,
        intent,
        knowledge,
        conversation.conversation_id,
        context=conversation_context,
    )
    creative = enrichment.get("creative") or {}
    if (
        repetition["repeated"]
        and intent == "asks_product_details"
        and creative.get("asset_type") == "video"
        and conversation_context.get("previous_assistant_replies")
    ):
        media_body, media_sanitizations = sanitize_draft(
            media_followup_body(product, knowledge, creative, conversation_context),
            language_code,
        )
        media_judge = judge_draft(product, media_body, intent, conversation_context)
        media_repetition = evaluate_repetition(media_body, conversation_context)
        writer_attempts += 1
        repair_blockers.append("repetitive_details_media_followup")
        if media_judge["approved"] and not media_repetition["repeated"]:
            body = media_body
            sanitizations = sorted(set(sanitizations + media_sanitizations))
            judge = media_judge
            repetition = media_repetition
            writer_mode = "media_followup_repair"

    if repetition["repeated"]:
        loop_body, loop_sanitizations = sanitize_draft(
            loop_breaker_body(product, conversation_context),
            language_code,
        )
        loop_judge = judge_draft(product, loop_body, intent, conversation_context)
        loop_repetition = evaluate_repetition(loop_body, conversation_context)
        writer_attempts += 1
        repair_blockers.append("loop_breaker_repair")
        if loop_judge["approved"] and not loop_repetition["repeated"]:
            body = loop_body
            sanitizations = sorted(set(sanitizations + loop_sanitizations))
            judge = loop_judge
            repetition = loop_repetition
            writer_mode = "loop_breaker_repair"

    if repetition["repeated"]:
        blockers = list(judge["blocking_issues"])
        if "repetitive_response" not in blockers:
            blockers.append("repetitive_response")
        judge = {
            "approved": False,
            "score": min(judge["score"], 65),
            "blocking_issues": blockers,
            "feedback": "Draftul repetă prea mult un răspuns deja trimis în conversație.",
        }
    now = timezone.now()

    run = AiResponseProcessRun.objects.create(
        run_id=uuid.uuid4(),
        conversation_id=conversation.conversation_id,
        product_id=product.product_id if product else normalize_text(conversation.product_detected),
        client_message=message_text,
        status="approved_review_only" if judge["approved"] else "needs_review",
        final_action="review_only",
        final_score=judge["score"],
        final_body=body,
        final_buttons=enrichment["buttons"],
        attempts_count=writer_attempts,
        error=None if judge["approved"] else judge["feedback"],
        created_at=now,
        finished_at=now,
    )

    log_step(
        run,
        "conversation_context",
        1,
        {"conversation_id": conversation.conversation_id, "intent": intent},
        {
            "summary": conversation_context["summary"],
            "language_code": conversation_context["language_code"],
            "language_name": conversation_context["language_name"],
            "language_confidence": conversation_context["language_confidence"],
            "phone_country_code": conversation_context.get("phone_country_code"),
            "phone_default_language_code": conversation_context.get("phone_default_language_code"),
            "sales_stage": conversation_context["sales_stage"],
            "journey_stage": conversation_context.get("journey_stage"),
            "engagement_depth": conversation_context.get("engagement_depth"),
            "price_exposure_allowed": conversation_context.get("price_exposure_allowed"),
            "detail_path_count": conversation_context.get("detail_path_count"),
            "use_context_count": conversation_context.get("use_context_count"),
            "selected_use_context": conversation_context.get("selected_use_context"),
            "current_use_context": conversation_context.get("current_use_context"),
            "details_path_exhausted": conversation_context.get("details_path_exhausted"),
            "allowed_cta_intents": conversation_context.get("allowed_cta_intents"),
            "blocked_cta_intents": conversation_context.get("blocked_cta_intents"),
            "next_best_action": conversation_context["next_best_action"],
            "answered_topics": conversation_context["answered_topics"],
            "recent_message_count": len(conversation_context["recent_dialogue"]),
            "previous_reply_count": len(conversation_context["previous_assistant_replies"]),
            "last_buttons": conversation_context["last_buttons"],
        },
        approved=True,
        score=100,
        action="context_compacted",
    )
    log_step(
        run,
        "signal",
        1,
        {"message_text_present": bool(message_text), "conversation_id": conversation.conversation_id},
        {
            "intent": intent,
            "product_id": product.product_id if product else None,
            "language_code": language_code,
            "journey_stage": conversation_context.get("journey_stage"),
            "engagement_depth": conversation_context.get("engagement_depth"),
            "price_exposure_allowed": conversation_context.get("price_exposure_allowed"),
            "detail_path_count": conversation_context.get("detail_path_count"),
            "details_path_exhausted": conversation_context.get("details_path_exhausted"),
        },
        approved=True,
        score=100,
        action="detected_context",
    )
    log_step(
        run,
        "retrieve_knowledge",
        1,
        {"intent": intent, "product_id": product.product_id if product else None},
        {
            "offers": len(knowledge["offers"]),
            "faqs": len(knowledge["faqs"]),
            "objections": len(knowledge["objections"]),
            "sales_rules": len(knowledge["sales_rules"]),
            "knowledge_items": len(knowledge["knowledge_items"]),
        },
        approved=True,
        score=100,
        action="retrieved_compact_context",
    )
    log_step(
        run,
        "writer",
        1,
        {"intent": intent, "writer_mode": writer_mode},
        {
            "body": body,
            "raw_body_changed": raw_body != body,
            "sanitizations": sanitizations,
            "send_enabled": False,
            "writer_mode": writer_mode,
            "repair_blockers": repair_blockers,
            "context_used": True,
            "language_code": language_code,
        },
        approved=True,
        score=90,
        action="drafted_reply",
    )
    log_step(
        run,
        "response_enrichment",
        1,
        {"intent": intent, "product_id": product.product_id if product else None},
        enrichment,
        approved=bool(enrichment["buttons"]),
        score=100 if enrichment["buttons"] else 70,
        action="prepared_quick_reply" if enrichment["buttons"] else "prepared_text",
    )
    log_step(
        run,
        "judge",
        1,
        {"body_length": len(body), "context_used": True},
        {**judge, "repetition": repetition},
        approved=judge["approved"],
        score=judge["score"],
        action="approve_for_review" if judge["approved"] else "needs_review",
        blockers=judge["blocking_issues"],
        feedback=judge["feedback"],
    )

    return run
