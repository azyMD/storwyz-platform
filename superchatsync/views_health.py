from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def healthz(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def readyz(request):
    checks = {"database": False, "broker": False}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["database"] = cursor.fetchone() == (1,)
    except Exception:
        pass

    try:
        import redis

        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        checks["broker"] = bool(client.ping())
        client.close()
    except Exception:
        pass

    ready = all(checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks},
        status=200 if ready else 503,
    )
