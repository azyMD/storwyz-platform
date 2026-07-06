import re

from django.db import ProgrammingError


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _normalize(value):
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def resolve_fitexpress_country(phone):
    from superchatsync.models import FitexpressCountry

    phone_digits = _digits(phone)
    if not phone_digits:
        return None

    best_match = None
    best_length = 0
    try:
        countries = FitexpressCountry.objects.filter(active=True)
        for country in countries:
            for prefix in country.phone_prefixes or []:
                prefix_digits = _digits(prefix)
                if prefix_digits and phone_digits.startswith(prefix_digits) and len(prefix_digits) > best_length:
                    best_match = country
                    best_length = len(prefix_digits)
    except ProgrammingError:
        return None
    return best_match


def resolve_fitexpress_country_id(phone, default_country_id=1810):
    country = resolve_fitexpress_country(phone)
    if country:
        return country.country_id
    try:
        return int(default_country_id)
    except (TypeError, ValueError):
        return 1810


def resolve_fitexpress_product_mapping(product_id=None, product_name=None):
    from superchatsync.models import FitexpressProductMapping

    product_id = str(product_id or "").strip()
    product_name = str(product_name or "").strip()
    try:
        if product_id:
            mapping = (
                FitexpressProductMapping.objects.filter(active=True, product_id=product_id).first()
                or FitexpressProductMapping.objects.filter(active=True, fitexpress_product_id=product_id).first()
            )
            if mapping:
                return mapping

        normalized_name = _normalize(product_name)
        if normalized_name:
            for mapping in FitexpressProductMapping.objects.filter(active=True):
                names = [mapping.product_name, *(mapping.aliases or [])]
                if normalized_name in {_normalize(name) for name in names if name}:
                    return mapping
    except ProgrammingError:
        return None
    return None


def resolve_fitexpress_product_id(product_id=None, product_name=None):
    mapping = resolve_fitexpress_product_mapping(product_id=product_id, product_name=product_name)
    if mapping:
        return str(mapping.fitexpress_product_id)
    return str(product_id or "").strip()
