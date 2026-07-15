import re
import unicodedata

from superchatsync.models import LandingProductMapping


def normalize_product_name(value):
    cleaned = str(value or "").replace("™", "").replace("®", "").replace("©", "")
    normalized = unicodedata.normalize("NFKD", cleaned)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def resolve_landing_product(product_name):
    normalized_name = normalize_product_name(product_name)
    if not normalized_name:
        return None, normalized_name
    mapping = LandingProductMapping.objects.filter(
        normalized_name=normalized_name,
        active=True,
    ).first()
    return mapping, normalized_name
