import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST


CATALOG_ADMIN_USER = os.getenv("CATALOG_ADMIN_USER", "admin")
CATALOG_ADMIN_PASSWORD = os.getenv("CATALOG_ADMIN_PASSWORD")
CATALOG_SESSION_KEY = "catalog_admin_logged_in"
MAX_PAGES = 10
CATALOG_DIR = Path(settings.MEDIA_ROOT) / "catalog_brochures"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}

COUNTRIES = [
    ("ro", "România"),
    ("md", "Moldova"),
    ("es", "Spania"),
    ("it", "Italia"),
    ("fr", "Franța"),
    ("de", "Germania"),
    ("uk", "Marea Britanie"),
    ("us", "Statele Unite"),
    ("pt", "Portugalia"),
    ("pl", "Polonia"),
    ("bg", "Bulgaria"),
    ("hu", "Ungaria"),
    ("gr", "Grecia"),
]


def _is_admin(request):
    return request.session.get(CATALOG_SESSION_KEY) is True


def _slugify(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug[:80] or "produs"


def _country_label(code):
    labels = dict(COUNTRIES)
    return labels.get(code, code.upper())


def _safe_catalog_path(product_slug, country_code):
    root = CATALOG_DIR.resolve()
    target = (root / product_slug / country_code).resolve()
    if root != target and root not in target.parents:
        raise Http404("Invalid catalog path")
    return target


def _manifest_path(product_slug, country_code):
    return _safe_catalog_path(product_slug, country_code) / "manifest.json"


def _read_manifest(product_slug, country_code):
    path = _manifest_path(product_slug, country_code)
    if not path.exists():
        raise Http404("Broșura nu există.")
    return json.loads(path.read_text(encoding="utf-8"))


def _brochure_url(request, product_slug, country_code, root=False):
    if root:
        path = f"/{product_slug}/{country_code}/"
    else:
        path = reverse("catalog_public", args=[product_slug, country_code])
    url = request.build_absolute_uri(path)
    if url.startswith("http://storwyz.com") or url.startswith("http://www.storwyz.com"):
        return "https://" + url[len("http://"):]
    return url


def _desired_catalog_url(product_slug, country_code):
    return f"https://catalog.storwyz.com/{product_slug}/{country_code}/"


def _list_brochures():
    if not CATALOG_DIR.exists():
        return []

    brochures = []
    for manifest_path in CATALOG_DIR.glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        product_slug = manifest.get("product_slug") or manifest_path.parent.parent.name
        country_code = manifest.get("country_code") or manifest_path.parent.name
        brochures.append(
            {
                "product_name": manifest.get("product_name") or product_slug,
                "product_slug": product_slug,
                "country_code": country_code,
                "country_label": _country_label(country_code),
                "page_count": len(manifest.get("pages") or []),
                "updated_at": manifest.get("updated_at") or "",
                "desired_url": _desired_catalog_url(product_slug, country_code),
                "local_path": f"/catalog/{product_slug}/{country_code}/",
            }
        )
    brochures.sort(key=lambda item: item["updated_at"], reverse=True)
    return brochures


def catalog_admin(request):
    if not _is_admin(request):
        error = request.GET.get("error") == "1"
        return HttpResponse(_login_html(request, error), content_type="text/html; charset=utf-8")

    token = get_token(request)
    brochures = _list_brochures()
    countries_options = "\n".join(
        f'<option value="{escape(code)}">{escape(label)} ({escape(code.upper())})</option>'
        for code, label in COUNTRIES
    )
    brochure_rows = "\n".join(
        f"""
        <tr>
          <td><strong>{escape(item["product_name"])}</strong><span>{escape(item["product_slug"])}</span></td>
          <td>{escape(item["country_label"])} <code>{escape(item["country_code"])}</code></td>
          <td>{escape(str(item["page_count"]))}</td>
          <td><a href="{escape(item["local_path"])}" target="_blank" rel="noopener">storwyz</a></td>
          <td><a href="{escape(item["desired_url"])}" target="_blank" rel="noopener">catalog</a></td>
        </tr>
        """
        for item in brochures
    ) or '<tr><td colspan="5" class="empty">Încă nu există broșuri.</td></tr>'

    return HttpResponse(
        ADMIN_HTML.format(
            csrf_token=escape(token),
            countries_options=countries_options,
            brochure_rows=brochure_rows,
            max_pages=MAX_PAGES,
        ),
        content_type="text/html; charset=utf-8",
    )


@require_POST
def catalog_login(request):
    username = request.POST.get("username", "")
    password = request.POST.get("password", "")
    if CATALOG_ADMIN_PASSWORD and username == CATALOG_ADMIN_USER and password == CATALOG_ADMIN_PASSWORD:
        request.session[CATALOG_SESSION_KEY] = True
        return redirect("catalog_admin")
    return redirect("/catalog-admin/?error=1")


def catalog_logout(request):
    request.session.pop(CATALOG_SESSION_KEY, None)
    return redirect("catalog_admin")


@require_POST
def catalog_create(request):
    if not _is_admin(request):
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    product_name = (request.POST.get("product_name") or "").strip()
    country_code = (request.POST.get("country_code") or "").strip().lower()
    files = request.FILES.getlist("pages")

    if not product_name:
        return JsonResponse({"ok": False, "error": "Completează numele produsului."}, status=400)
    if country_code not in dict(COUNTRIES):
        return JsonResponse({"ok": False, "error": "Alege o țară validă."}, status=400)
    if not files:
        return JsonResponse({"ok": False, "error": "Încarcă cel puțin o pagină."}, status=400)
    if len(files) > MAX_PAGES:
        return JsonResponse({"ok": False, "error": f"Poți încărca maximum {MAX_PAGES} pagini."}, status=400)

    product_slug = _slugify(product_name)
    target = _safe_catalog_path(product_slug, country_code)
    staging = target.with_name(f".{target.name}.staging")
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "assets").mkdir(parents=True, exist_ok=True)

    pages = []
    try:
        for index, uploaded_file in enumerate(files, start=1):
            ext = Path(uploaded_file.name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS or not (uploaded_file.content_type or "").startswith("image/"):
                return JsonResponse(
                    {"ok": False, "error": "Sunt acceptate doar imagini jpg, png, webp sau avif."},
                    status=400,
                )
            filename = f"page-{index:02d}{ext}"
            destination = staging / "assets" / filename
            with destination.open("wb") as output:
                for chunk in uploaded_file.chunks():
                    output.write(chunk)
            pages.append(
                {
                    "number": index,
                    "filename": f"assets/{filename}",
                    "original_name": uploaded_file.name,
                    "size": destination.stat().st_size,
                }
            )

        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        manifest = {
            "product_name": product_name,
            "product_slug": product_slug,
            "country_code": country_code,
            "country_label": _country_label(country_code),
            "created_at": now,
            "updated_at": now,
            "pages": pages,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return JsonResponse(
        {
            "ok": True,
            "product_slug": product_slug,
            "country_code": country_code,
            "page_count": len(pages),
            "public_url": _brochure_url(request, product_slug, country_code),
            "root_url": _brochure_url(request, product_slug, country_code, root=True),
            "desired_url": _desired_catalog_url(product_slug, country_code),
        }
    )


def catalog_public(request, product_slug, country_code):
    manifest = _read_manifest(product_slug, country_code)
    return HttpResponse(_public_html(manifest), content_type="text/html; charset=utf-8")


def catalog_public_root(request, product_slug, country_code):
    return catalog_public(request, product_slug, country_code)


def _product_catalog_html(manifest):
    product_name = manifest.get("product_name") or "Catalog"
    product_slug = manifest.get("product_slug") or _slugify(product_name)
    country_code = manifest.get("country_code") or "uk"
    asset_root = f"{settings.MEDIA_URL}catalog_brochures/{product_slug}/{country_code}/"
    products = []
    for product in (manifest.get("products") or [])[:48]:
        image = product.get("image") or ""
        products.append(
            {
                "title": product.get("title") or "",
                "price": product.get("price") or "",
                "rrp": product.get("rrp") or "",
                "url": product.get("url") or "https://peeko.co.uk/",
                "image": image if image.startswith(("http://", "https://", "/")) else f"{asset_root}{image}",
            }
        )

    pages = [
        {"number": index + 1, "products": products[index * 4 : (index + 1) * 4]}
        for index in range((len(products) + 3) // 4)
    ]
    pages_json = json.dumps(pages, ensure_ascii=False)
    title = escape(product_name)
    country_label = escape(manifest.get("country_label") or country_code.upper())

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - Catalog</title>
  <meta name="description" content="Interactive product catalog {title}.">
  <style>
    :root {{ color-scheme:light; --bg:#f3f4f2; --paper:#fff; --ink:#092323; --muted:#7e8784; --line:#e2e9e5; --green:#0b3b37; --yellow:#ffd436; --red:#f25b4b; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    * {{ box-sizing:border-box; }}
    html,body {{ min-height:100%; }}
    body {{ margin:0; overflow:hidden; color:var(--ink); background:radial-gradient(circle at 12% 10%,rgba(255,212,54,.28),transparent 28%),linear-gradient(135deg,#eef1ee,#f8faf7); }}
    button {{ font:inherit; }}
    .app {{ height:100vh; min-height:100vh; display:grid; grid-template-rows:auto minmax(0,1fr); }}
    .topbar {{ min-height:68px; padding:10px clamp(14px,3vw,36px); display:flex; align-items:center; justify-content:space-between; gap:18px; border-bottom:1px solid var(--line); background:rgba(243,244,242,.9); backdrop-filter:blur(18px); z-index:4; }}
    .brand {{ display:flex; align-items:center; gap:12px; min-width:0; }}
    .mark {{ width:38px; height:38px; border-radius:50%; display:grid; place-items:center; color:var(--green); background:var(--yellow); font-size:24px; line-height:1; font-weight:950; flex:0 0 auto; }}
    .brand strong {{ display:block; color:var(--green); font-size:24px; line-height:1; font-weight:950; letter-spacing:0; }}
    .brand span {{ display:block; margin-top:3px; color:var(--muted); font-size:13px; font-weight:800; }}
    .controls {{ display:flex; align-items:center; gap:9px; }}
    .icon-button {{ width:42px; height:42px; border:1px solid rgba(11,59,55,.14); border-radius:8px; display:inline-grid; place-items:center; color:var(--green); background:#fff; cursor:pointer; box-shadow:0 6px 14px rgba(11,59,55,.06); }}
    .icon-button:disabled {{ opacity:.36; cursor:default; }}
    .counter {{ min-width:96px; height:42px; padding:0 12px; border:1px solid rgba(11,59,55,.12); border-radius:8px; display:grid; place-items:center; color:var(--green); background:#fff; font-size:13px; font-weight:900; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .stage {{ min-height:0; display:grid; place-items:center; padding:clamp(8px,1.4vw,18px); }}
    .book-shell {{ position:relative; width:min(96vw,1360px); height:min(100%,960px); display:grid; place-items:center; perspective:1900px; }}
    .book {{ position:relative; width:min(100%,calc((100vh - 92px) * 1.48)); max-height:100%; aspect-ratio:1.48/1; display:grid; grid-template-columns:1fr 14px 1fr; transform-style:preserve-3d; filter:drop-shadow(0 24px 60px rgba(9,35,35,.22)); user-select:none; }}
    .page {{ position:relative; min-width:0; overflow:hidden; background:var(--paper); border:1px solid rgba(11,59,55,.1); isolation:isolate; }}
    .page-left {{ border-radius:8px 2px 2px 8px; box-shadow:inset -28px 0 42px rgba(9,35,35,.12); }}
    .page-right {{ border-radius:2px 8px 8px 2px; box-shadow:inset 28px 0 42px rgba(9,35,35,.09); }}
    .spine {{ position:relative; z-index:2; background:linear-gradient(90deg,rgba(9,35,35,.28),rgba(255,212,54,.32) 48%,rgba(9,35,35,.22)),#e7eee8; border-block:1px solid rgba(11,59,55,.12); }}
    .hit {{ position:absolute; top:0; width:8%; height:100%; z-index:3; cursor:pointer; }}
    .hit.prev {{ left:0; }} .hit.next {{ right:0; }}
    .page-cue {{ position:absolute; top:50%; z-index:9; width:48px; height:82px; border:1px solid rgba(11,59,55,.16); border-radius:8px; display:grid; place-items:center; color:var(--green); background:rgba(255,255,255,.86); box-shadow:0 14px 34px rgba(9,35,35,.16); transform:translateY(-50%); cursor:pointer; backdrop-filter:blur(10px); transition:background-color .18s ease,color .18s ease,opacity .18s ease,transform .18s ease; }}
    .page-cue:hover:not(:disabled),.page-cue:focus-visible:not(:disabled) {{ color:#fff; background:var(--red); transform:translateY(-50%) scale(1.03); }}
    .page-cue:disabled {{ opacity:.24; cursor:default; }}
    .page-cue.prev {{ left:clamp(4px,1vw,14px); }}
    .page-cue.next {{ right:clamp(4px,1vw,14px); }}
    .page-cue svg {{ width:28px; height:28px; stroke:currentColor; stroke-width:2.6; stroke-linecap:round; stroke-linejoin:round; fill:none; }}
    .flip-page {{ position:absolute; top:0; height:100%; width:calc((100% - 14px)/2); display:none; transform-style:preserve-3d; z-index:6; pointer-events:none; }}
    .flip-page.next {{ right:0; transform-origin:left center; display:block; animation:flipNext .82s cubic-bezier(.2,.68,.22,1) forwards; }}
    .flip-page.prev {{ left:0; transform-origin:right center; display:block; animation:flipPrev .82s cubic-bezier(.2,.68,.22,1) forwards; }}
    .flip-face {{ position:absolute; inset:0; overflow:hidden; background:var(--paper); border:1px solid rgba(11,59,55,.1); backface-visibility:hidden; }}
    .flip-page.next .flip-face.back, .flip-page.prev .flip-face.back {{ transform:rotateY(180deg); }}
    @keyframes flipNext {{ 0%{{transform:rotateY(0)}} 52%{{box-shadow:-24px 18px 42px rgba(9,35,35,.22)}} 100%{{transform:rotateY(-180deg)}} }}
    @keyframes flipPrev {{ 0%{{transform:rotateY(0)}} 52%{{box-shadow:24px 18px 42px rgba(9,35,35,.2)}} 100%{{transform:rotateY(180deg)}} }}
    .catalog-page {{ height:100%; padding:clamp(14px,1.45vw,22px); display:grid; grid-template-rows:auto minmax(0,1fr) auto; gap:12px; background:linear-gradient(180deg,#fff,#fbfcfa); overflow:hidden; }}
    .page-head {{ display:flex; align-items:start; justify-content:space-between; gap:12px; }}
    .page-head h2 {{ margin:0; color:var(--green); font-size:clamp(18px,1.8vw,26px); line-height:1.02; letter-spacing:0; }}
    .page-kicker {{ margin:4px 0 0; color:var(--muted); font-size:12px; font-weight:850; text-transform:uppercase; }}
    .page-badge {{ min-width:42px; height:34px; padding:0 10px; border-radius:8px; display:grid; place-items:center; color:var(--green); background:var(--yellow); font-size:13px; font-weight:950; }}
    .product-grid {{ min-height:0; display:grid; grid-template-columns:1fr 1fr; grid-template-rows:repeat(2,minmax(0,1fr)); gap:12px; }}
    .catalog-card {{ min-width:0; min-height:0; display:grid; grid-template-rows:minmax(142px,46%) minmax(0,1fr); overflow:hidden; border:1px solid var(--line); border-radius:8px; background:#fff; box-shadow:0 8px 18px rgba(9,35,35,.06); }}
    .product-media {{ display:block; min-height:142px; padding:8px 10px 4px; overflow:hidden; background:#fff; }}
    .product-media img {{ width:100%; height:100%; display:block; object-fit:contain; }}
    .product-copy {{ min-height:0; padding:10px 11px 11px; display:grid; grid-template-rows:minmax(0,1fr) auto auto; gap:8px; }}
    .product-title {{ margin:0; min-height:0; color:var(--ink); font-size:clamp(13px,1.05vw,16px); line-height:1.13; font-weight:950; letter-spacing:0; overflow:hidden; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }}
    .price-row {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }}
    .price {{ color:var(--ink); font-size:clamp(22px,2vw,30px); line-height:1; font-weight:950; letter-spacing:0; }}
    .rrp {{ color:#888; font-size:13px; font-weight:850; text-decoration:line-through; }}
    .buy {{ min-height:38px; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; gap:8px; color:var(--green); background:var(--yellow); text-decoration:none; font-size:13px; font-weight:950; transition:background-color .18s ease,color .18s ease,box-shadow .18s ease,transform .18s ease; }}
    .buy:hover,.buy:focus-visible {{ color:#fff; background:var(--red); box-shadow:0 10px 20px rgba(242,91,75,.26); transform:translateY(-1px); }}
    .buy svg,.icon-button svg {{ width:18px; height:18px; stroke:currentColor; stroke-width:2.4; stroke-linecap:round; stroke-linejoin:round; fill:none; }}
    .page-number {{ justify-self:end; min-width:26px; height:24px; padding:0 8px; border-radius:8px; display:grid; place-items:center; color:var(--muted); background:#f1f5f2; font-size:12px; font-weight:900; }}
    @media (max-width:900px) {{ .book-shell{{height:100%}} .book{{width:min(96vw,calc((100vh - 88px)*.72));aspect-ratio:.72/1;grid-template-columns:1fr}} .page-left,.spine{{display:none}} .page-right{{border-radius:8px;box-shadow:none}} .flip-page,.flip-page.next,.flip-page.prev{{left:0;right:auto;width:100%}} .hit{{width:12%}} .page-cue{{width:42px;height:70px}} .catalog-page{{overflow:hidden}} .catalog-card{{grid-template-rows:minmax(126px,45%) minmax(0,1fr)}} .product-media{{min-height:126px}} .product-grid{{gap:10px}} .product-copy{{padding:9px}} .brand span{{display:none}} .counter{{min-width:72px}} }}
    @media (max-width:560px) {{ .topbar{{min-height:62px;padding:10px}} .mark{{width:34px;height:34px}} .brand strong{{font-size:20px}} .icon-button{{width:38px;height:38px}} .stage{{padding:8px}} .book-shell{{width:96vw;height:100%}} .book{{width:min(96vw,calc((100vh - 82px)*.58));aspect-ratio:.58/1}} .product-grid{{grid-template-columns:1fr 1fr}} .page-head h2{{font-size:18px}} .product-title{{font-size:12px}} .price{{font-size:20px}} .rrp{{font-size:11px}} .buy{{min-height:34px;font-size:12px}} .page-cue{{width:38px;height:64px}} }}
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar"><div class="brand"><span class="mark">p</span><div><strong>{title}</strong><span>{country_label} · interactive brochure</span></div></div><div class="controls"><button class="icon-button" id="prevButton" aria-label="Previous page"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"></path></svg></button><div class="counter" id="counter"></div><button class="icon-button" id="nextButton" aria-label="Next page"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"></path></svg></button></div></header>
    <main class="stage"><section class="book-shell"><button class="page-cue prev" id="prevCue" aria-label="Previous page"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"></path></svg></button><div class="book"><article class="page page-left" id="leftPage"></article><div class="spine"></div><article class="page page-right" id="rightPage"></article><div class="hit prev" id="prevHit"></div><div class="hit next" id="nextHit"></div><div class="flip-page" id="flipPage"></div></div><button class="page-cue next" id="nextCue" aria-label="Next page"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"></path></svg></button></section></main>
  </div>
  <script>
    const pages = {pages_json};
    const leftPage = document.getElementById("leftPage"), rightPage = document.getElementById("rightPage"), flipPage = document.getElementById("flipPage"), counter = document.getElementById("counter");
    const prevButton = document.getElementById("prevButton"), nextButton = document.getElementById("nextButton"), prevHit = document.getElementById("prevHit"), nextHit = document.getElementById("nextHit"), prevCue = document.getElementById("prevCue"), nextCue = document.getElementById("nextCue");
    let current = 0, animating = false, animationId = 0;
    const isSinglePage = () => matchMedia("(max-width: 900px)").matches;
    const stepSize = () => isSinglePage() ? 1 : 2;
    const clampStart = i => Math.min(Math.max(0, isSinglePage() ? i : Math.floor(i / 2) * 2), Math.max(0, pages.length - 1));
    const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[char]));
    const pageTemplate = (page, index) => page ? `<section class="catalog-page"><header class="page-head"><div><p class="page-kicker">Peeko best sellers</p><h2>Page ${{index + 1}}</h2></div><span class="page-badge">p</span></header><div class="product-grid">${{page.products.map(product => `<article class="catalog-card"><a class="product-media" href="${{escapeHtml(product.url)}}" target="_blank" rel="noopener"><img src="${{escapeHtml(product.image)}}" alt="${{escapeHtml(product.title)}}" draggable="false"></a><div class="product-copy"><h3 class="product-title">${{escapeHtml(product.title)}}</h3><div class="price-row"><strong class="price">${{escapeHtml(product.price)}}</strong><span class="rrp">RRP ${{escapeHtml(product.rrp)}}</span></div><a class="buy" href="${{escapeHtml(product.url)}}" target="_blank" rel="noopener">Buy now <svg viewBox="0 0 24 24"><path d="M5 12h14"></path><path d="m13 6 6 6-6 6"></path></svg></a></div></article>`).join("")}}</div><span class="page-number">${{index + 1}}</span></section>` : "";
    function displaySpread(start) {{ const spreadStart = clampStart(start); if (isSinglePage()) {{ leftPage.innerHTML = ""; rightPage.innerHTML = pageTemplate(pages[spreadStart], spreadStart); }} else {{ leftPage.innerHTML = pageTemplate(pages[spreadStart], spreadStart); rightPage.innerHTML = pageTemplate(pages[spreadStart + 1], spreadStart + 1); }} }}
    function updateStatus(start) {{ const spreadStart = clampStart(start); counter.textContent = isSinglePage() ? `${{spreadStart + 1}} / ${{pages.length}}` : `${{spreadStart + 1}}-${{Math.min(spreadStart + 2, pages.length)}} / ${{pages.length}}`; const atStart = spreadStart <= 0 || animating; const atEnd = spreadStart + stepSize() >= pages.length || animating; prevButton.disabled = atStart; prevCue.disabled = atStart; nextButton.disabled = atEnd; nextCue.disabled = atEnd; }}
    function render() {{ current = clampStart(current); displaySpread(current); updateStatus(current); }}
    const buildFlipFace = (frontPage, frontIndex, backPage, backIndex) => `<div class="flip-face front">${{pageTemplate(frontPage, frontIndex)}}</div><div class="flip-face back">${{pageTemplate(backPage, backIndex)}}</div>`;
    function animateFlip(direction, target) {{
      animating = true;
      const token = ++animationId;
      current = clampStart(current);
      const from = current;
      const to = clampStart(target);
      if (direction === "next") {{
        const frontIndex = isSinglePage() ? from : from + 1;
        const backIndex = to;
        flipPage.innerHTML = buildFlipFace(pages[frontIndex], frontIndex, pages[backIndex], backIndex);
        if (isSinglePage()) {{ displaySpread(to); }} else {{ leftPage.innerHTML = pageTemplate(pages[from], from); rightPage.innerHTML = pageTemplate(pages[to + 1], to + 1); }}
      }} else {{
        const frontIndex = from;
        const backIndex = isSinglePage() ? to : from - 1;
        flipPage.innerHTML = buildFlipFace(pages[frontIndex], frontIndex, pages[backIndex], backIndex);
        if (isSinglePage()) {{ displaySpread(to); }} else {{ leftPage.innerHTML = pageTemplate(pages[to], to); rightPage.innerHTML = pageTemplate(pages[from + 1], from + 1); }}
      }}
      updateStatus(to);
      flipPage.className = `flip-page ${{direction}}`;
      const finish = () => {{ if (token !== animationId) return; flipPage.className = "flip-page"; flipPage.innerHTML = ""; current = to; animating = false; render(); }};
      flipPage.addEventListener("animationend", finish, {{ once: true }});
      setTimeout(() => {{ if (animating && token === animationId) finish(); }}, 1000);
    }}
    function goNext() {{ if (!animating && current + stepSize() < pages.length) animateFlip("next", current + stepSize()); }}
    function goPrev() {{ if (!animating && current > 0) animateFlip("prev", current - stepSize()); }}
    prevButton.addEventListener("click", goPrev); nextButton.addEventListener("click", goNext); prevHit.addEventListener("click", goPrev); nextHit.addEventListener("click", goNext); prevCue.addEventListener("click", goPrev); nextCue.addEventListener("click", goNext);
    addEventListener("keydown", e => {{ if (e.key === "ArrowRight") goNext(); if (e.key === "ArrowLeft") goPrev(); }}); addEventListener("resize", render); render();
  </script>
</body>
</html>
"""


def _login_html(request, error=False):
    token = get_token(request)
    return f"""
    <!doctype html>
    <html lang="ro">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Catalog Login</title>
      <style>
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: #f7ecdc;
          background: radial-gradient(circle at 20% 20%, rgba(180, 106, 52, .28), transparent 32%), linear-gradient(135deg, #0d0805, #24140c);
        }}
        form {{
          width: min(90vw, 380px);
          padding: 28px;
          border: 1px solid rgba(245, 230, 206, .18);
          border-radius: 8px;
          background: rgba(255, 255, 255, .07);
          box-shadow: 0 24px 70px rgba(0, 0, 0, .42);
        }}
        h1 {{ margin: 0 0 20px; font-family: Georgia, serif; font-size: 30px; }}
        label {{ display: block; margin-top: 12px; color: #d7c3a5; font-size: 13px; }}
        input {{
          width: 100%;
          height: 42px;
          margin-top: 6px;
          border: 1px solid rgba(245, 230, 206, .24);
          border-radius: 8px;
          padding: 0 12px;
          color: #fff;
          background: rgba(0, 0, 0, .24);
        }}
        button {{
          width: 100%;
          height: 44px;
          margin-top: 18px;
          border: 0;
          border-radius: 8px;
          color: #201108;
          background: #e7c58d;
          font-weight: 760;
          cursor: pointer;
        }}
        .error {{ margin: 12px 0 0; color: #ffb4a8; }}
      </style>
    </head>
    <body>
      <form method="post" action="/catalog-admin/login/">
        <input type="hidden" name="csrfmiddlewaretoken" value="{escape(token)}">
        <h1>Catalog Admin</h1>
        <label>Utilizator</label>
        <input name="username" autocomplete="username" autofocus>
        <label>Parolă</label>
        <input name="password" type="password" autocomplete="current-password">
        <button type="submit">Intră</button>
        {'<p class="error">Credentiale greșite.</p>' if error else ''}
      </form>
    </body>
    </html>
    """


ADMIN_HTML = """
<!doctype html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Catalog Admin</title>
  <style>
    :root {{
      --bg: #f3f0eb;
      --panel: #fff;
      --ink: #1c1713;
      --muted: #6f6258;
      --line: #ded4c8;
      --accent: #a4572d;
      --dark: #201108;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--ink); background: var(--bg); }}
    header {{
      min-height: 70px;
      padding: 14px clamp(16px, 3vw, 34px);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 18px;
      color: #f7ecdc;
      background: linear-gradient(135deg, #120a06, #2a160c);
    }}
    h1, h2 {{ margin: 0; font-family: Georgia, "Times New Roman", serif; letter-spacing: 0; }}
    header p {{ margin: 4px 0 0; color: #d8c0a3; }}
    header a {{ color: #f7ecdc; }}
    main {{ width: min(1180px, 94vw); margin: 24px auto 40px; display: grid; gap: 18px; }}
    section {{
      padding: 20px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 10px 30px rgba(42, 28, 18, .08);
    }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    label {{ display: block; margin-bottom: 7px; color: var(--muted); font-size: 13px; font-weight: 680; }}
    input, select {{
      width: 100%;
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      color: var(--ink);
      background: #fff;
    }}
    input[type=file] {{ padding: 9px 12px; }}
    .drop {{
      margin-top: 14px;
      padding: 18px;
      border: 1px dashed #bfae9e;
      border-radius: 8px;
      color: var(--muted);
      background: #fbf8f4;
    }}
    .thumbs {{
      margin-top: 16px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(94px, 1fr));
      gap: 12px;
    }}
    .thumb {{
      position: relative;
      border: 2px solid transparent;
      border-radius: 8px;
      overflow: hidden;
      background: #eee6dc;
      cursor: grab;
      box-shadow: 0 8px 20px rgba(42, 28, 18, .12);
    }}
    .thumb.dragging {{ opacity: .45; }}
    .thumb img {{ width: 100%; aspect-ratio: 9 / 16; object-fit: cover; display: block; }}
    .thumb span {{
      position: absolute;
      left: 6px;
      top: 6px;
      min-width: 24px;
      height: 24px;
      display: grid;
      place-items: center;
      border-radius: 7px;
      background: rgba(255,255,255,.9);
      color: var(--dark);
      font-size: 12px;
      font-weight: 800;
    }}
    .actions {{ margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    button {{
      height: 42px;
      border: 0;
      border-radius: 8px;
      padding: 0 16px;
      color: #fff;
      background: var(--dark);
      font-weight: 760;
      cursor: pointer;
    }}
    button.secondary {{ color: var(--dark); background: #eadfd2; }}
    .note {{ color: var(--muted); font-size: 13px; }}
    .result {{
      display: none;
      margin-top: 14px;
      padding: 14px;
      border-radius: 8px;
      background: #f1f7ef;
      border: 1px solid #c7dfbf;
    }}
    .result.is-visible {{ display: block; }}
    .result a {{ color: #116329; font-weight: 760; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 14px; }}
    th, td {{ padding: 11px 8px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 13px; background: #faf8f5; }}
    td span {{ display: block; margin-top: 3px; color: var(--muted); font-size: 12px; }}
    code {{ padding: 2px 5px; border-radius: 5px; background: #f0e8df; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 760px) {{
      header {{ align-items: flex-start; flex-direction: column; }}
      .grid {{ grid-template-columns: 1fr; }}
      section {{ padding: 16px; }}
      table {{ display: block; overflow-x: auto; white-space: nowrap; }}
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Catalog Admin</h1>
      <p>Generează broșuri HTML din imagini, până la {max_pages} pagini.</p>
    </div>
    <a href="/catalog-admin/logout/">Logout</a>
  </header>

  <main>
    <section>
      <h2>Broșură nouă</h2>
      <form id="createForm">
        <input type="hidden" name="csrfmiddlewaretoken" value="{csrf_token}">
        <div class="grid">
          <div>
            <label for="productName">Nume produs</label>
            <input id="productName" name="product_name" placeholder="Ex: Butchaxe" required>
          </div>
          <div>
            <label for="countryCode">Țară</label>
            <select id="countryCode" name="country_code" required>
              {countries_options}
            </select>
          </div>
        </div>
        <div class="drop">
          <label for="pageFiles">Pagini broșură</label>
          <input id="pageFiles" type="file" accept="image/*" multiple>
          <p class="note">Selectează maximum {max_pages} poze. Trage thumbnail-urile ca să alegi ordinea finală.</p>
        </div>
        <div class="thumbs" id="thumbs"></div>
        <div class="actions">
          <button type="submit">Generează broșura</button>
          <button class="secondary" type="button" id="clearButton">Curăță pozele</button>
          <span class="note" id="statusText"></span>
        </div>
      </form>
      <div class="result" id="resultBox"></div>
    </section>

    <section>
      <h2>Broșuri existente</h2>
      <table>
        <thead>
          <tr>
            <th>Produs</th>
            <th>Țară</th>
            <th>Pagini</th>
            <th>Link curent</th>
            <th>Link subdomeniu</th>
          </tr>
        </thead>
        <tbody>
          {brochure_rows}
        </tbody>
      </table>
    </section>
  </main>

  <script>
    const MAX_PAGES = {max_pages};
    const fileInput = document.getElementById("pageFiles");
    const thumbs = document.getElementById("thumbs");
    const form = document.getElementById("createForm");
    const statusText = document.getElementById("statusText");
    const resultBox = document.getElementById("resultBox");
    const clearButton = document.getElementById("clearButton");
    let selected = [];

    function renderThumbs() {{
      thumbs.innerHTML = "";
      selected.forEach((item, index) => {{
        const card = document.createElement("div");
        card.className = "thumb";
        card.draggable = true;
        card.dataset.index = String(index);
        card.innerHTML = `<img src="${{item.url}}" alt=""><span>${{index + 1}}</span>`;
        card.addEventListener("dragstart", () => card.classList.add("dragging"));
        card.addEventListener("dragend", () => {{
          card.classList.remove("dragging");
          syncOrderFromDom();
        }});
        thumbs.appendChild(card);
      }});
    }}

    function syncOrderFromDom() {{
      const next = [];
      thumbs.querySelectorAll(".thumb").forEach((card) => {{
        next.push(selected[Number(card.dataset.index)]);
      }});
      selected = next;
      renderThumbs();
    }}

    thumbs.addEventListener("dragover", (event) => {{
      event.preventDefault();
      const dragging = thumbs.querySelector(".dragging");
      if (!dragging) return;
      const after = getAfterElement(thumbs, event.clientY, event.clientX);
      if (after == null) {{
        thumbs.appendChild(dragging);
      }} else {{
        thumbs.insertBefore(dragging, after);
      }}
    }});

    function getAfterElement(container, y, x) {{
      const elements = [...container.querySelectorAll(".thumb:not(.dragging)")];
      return elements.reduce((closest, child) => {{
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2 + (x - box.left - box.width / 2) * 0.05;
        if (offset < 0 && offset > closest.offset) {{
          return {{ offset, element: child }};
        }}
        return closest;
      }}, {{ offset: Number.NEGATIVE_INFINITY, element: null }}).element;
    }}

    fileInput.addEventListener("change", () => {{
      selected.forEach((item) => URL.revokeObjectURL(item.url));
      const files = Array.from(fileInput.files).filter((file) => file.type.startsWith("image/")).slice(0, MAX_PAGES);
      selected = files.map((file) => ({{ file, url: URL.createObjectURL(file) }}));
      if (fileInput.files.length > MAX_PAGES) {{
        statusText.textContent = `Am păstrat primele ${{MAX_PAGES}} imagini.`;
      }} else {{
        statusText.textContent = `${{selected.length}} pagini selectate.`;
      }}
      renderThumbs();
    }});

    clearButton.addEventListener("click", () => {{
      selected.forEach((item) => URL.revokeObjectURL(item.url));
      selected = [];
      fileInput.value = "";
      statusText.textContent = "";
      resultBox.classList.remove("is-visible");
      renderThumbs();
    }});

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      if (!selected.length) {{
        statusText.textContent = "Încarcă cel puțin o pagină.";
        return;
      }}
      const formData = new FormData();
      formData.append("product_name", document.getElementById("productName").value);
      formData.append("country_code", document.getElementById("countryCode").value);
      selected.forEach((item) => formData.append("pages", item.file, item.file.name));
      statusText.textContent = "Se generează...";
      resultBox.classList.remove("is-visible");

      const response = await fetch("/catalog-admin/create/", {{
        method: "POST",
        headers: {{ "X-CSRFToken": "{csrf_token}" }},
        body: formData
      }});
      const data = await response.json();
      if (!response.ok || !data.ok) {{
        statusText.textContent = data.error || "Nu am putut genera broșura.";
        return;
      }}
      statusText.textContent = `Publicată cu ${{data.page_count}} pagini.`;
      resultBox.innerHTML = `
        <strong>Broșura este gata.</strong><br>
        Link curent: <a href="${{data.public_url}}" target="_blank" rel="noopener">${{data.public_url}}</a><br>
        Link scurt pe storwyz: <a href="${{data.root_url}}" target="_blank" rel="noopener">${{data.root_url}}</a><br>
        Link dorit după DNS: <a href="${{data.desired_url}}" target="_blank" rel="noopener">${{data.desired_url}}</a>
      `;
      resultBox.classList.add("is-visible");
    }});
  </script>
</body>
</html>
"""


def _public_html(manifest):
    if manifest.get("type") == "product_catalog":
        return _product_catalog_html(manifest)

    pages = manifest.get("pages") or []
    product_name = manifest.get("product_name") or "Catalog"
    product_slug = manifest.get("product_slug") or _slugify(product_name)
    country_code = manifest.get("country_code") or "ro"
    image_items = []
    for page in pages[:MAX_PAGES]:
        filename = page.get("filename", "")
        src = f"{settings.MEDIA_URL}catalog_brochures/{product_slug}/{country_code}/{filename}"
        image_items.append({"src": src, "alt": f"{product_name} pagina {page.get('number', '')}"})

    pages_json = json.dumps(image_items, ensure_ascii=False)
    title = escape(product_name)

    return f"""
<!doctype html>
<html lang="ro">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - Broșură</title>
  <meta name="description" content="Broșură interactivă {title}.">
  <style>
    :root {{ color-scheme: dark; --ink:#f5ead8; --muted:#cdbb9e; --line:rgba(245,234,216,.16); --accent:#e4c68f; --fit:contain; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    * {{ box-sizing:border-box; }}
    html, body {{ min-height:100%; }}
    body {{ margin:0; background:radial-gradient(circle at 20% 12%,rgba(185,106,53,.25),transparent 28%),linear-gradient(135deg,#0b0705 0%,#1b100a 52%,#090605 100%); color:var(--ink); overflow:hidden; }}
    button {{ font:inherit; }}
    .app {{ min-height:100vh; display:grid; grid-template-rows:auto 1fr auto; }}
    .topbar {{ min-height:68px; padding:12px clamp(14px,3vw,34px); display:flex; align-items:center; justify-content:space-between; gap:18px; border-bottom:1px solid var(--line); background:linear-gradient(180deg,rgba(8,5,3,.86),rgba(8,5,3,.55)); backdrop-filter:blur(18px); z-index:4; }}
    .brand strong {{ display:block; font-family:Georgia,"Times New Roman",serif; font-size:22px; line-height:1; }}
    .brand span {{ display:block; margin-top:4px; color:var(--muted); font-size:13px; }}
    .controls {{ display:flex; align-items:center; gap:9px; }}
    .icon-button {{ width:42px; height:42px; border:1px solid rgba(228,198,143,.26); border-radius:8px; display:inline-grid; place-items:center; color:var(--ink); background:rgba(255,255,255,.08); cursor:pointer; }}
    .icon-button:disabled {{ opacity:.38; cursor:default; }}
    .counter {{ min-width:94px; height:42px; padding:0 12px; border:1px solid rgba(228,198,143,.22); border-radius:8px; display:grid; place-items:center; color:var(--muted); background:rgba(255,255,255,.07); font-size:13px; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .stage {{ min-height:0; display:grid; place-items:center; padding:clamp(14px,3vw,30px); }}
    .book-shell {{ width:min(94vw,1180px); height:min(78vh,820px); display:grid; place-items:center; perspective:1900px; }}
    .book {{ position:relative; width:min(100%,calc((78vh - 20px) * 1.125)); max-height:100%; aspect-ratio:1.125/1; display:grid; grid-template-columns:1fr 14px 1fr; transform-style:preserve-3d; filter:drop-shadow(0 26px 70px rgba(0,0,0,.55)); user-select:none; }}
    .page {{ position:relative; min-width:0; overflow:hidden; background:#100906; border:1px solid rgba(228,198,143,.18); isolation:isolate; }}
    .page-left {{ border-radius:8px 2px 2px 8px; box-shadow:inset -34px 0 44px rgba(0,0,0,.34); }}
    .page-right {{ border-radius:2px 8px 8px 2px; box-shadow:inset 34px 0 44px rgba(0,0,0,.26); }}
    .spine {{ position:relative; background:linear-gradient(90deg,rgba(0,0,0,.72),rgba(228,198,143,.14) 45%,rgba(0,0,0,.72)),#20120b; border-block:1px solid rgba(228,198,143,.18); z-index:2; }}
    .page img, .flip-face img {{ width:100%; height:100%; display:block; object-fit:var(--fit); background:#110906; }}
    .page::after, .flip-face::after {{ content:""; position:absolute; inset:0; pointer-events:none; background:linear-gradient(90deg,rgba(255,255,255,.04),transparent 12%,transparent 86%,rgba(0,0,0,.17)); opacity:.8; }}
    .page-number {{ position:absolute; right:11px; bottom:10px; min-width:25px; height:24px; padding:0 8px; display:grid; place-items:center; border-radius:8px; color:var(--ink); background:rgba(20,11,7,.64); border:1px solid rgba(228,198,143,.22); font-size:12px; z-index:2; }}
    .page-left .page-number {{ left:11px; right:auto; }}
    .hit {{ position:absolute; top:0; width:24%; height:100%; z-index:3; cursor:pointer; }}
    .hit.prev {{ left:0; }} .hit.next {{ right:0; }}
    .flip-page {{ position:absolute; top:0; height:100%; width:calc((100% - 14px)/2); display:none; transform-style:preserve-3d; z-index:6; pointer-events:none; }}
    .flip-page.next {{ right:0; transform-origin:left center; display:block; animation:flipNext .82s cubic-bezier(.2,.68,.22,1) forwards; }}
    .flip-page.prev {{ left:0; transform-origin:right center; display:block; animation:flipPrev .82s cubic-bezier(.2,.68,.22,1) forwards; }}
    .flip-face {{ position:absolute; inset:0; overflow:hidden; background:#100906; border:1px solid rgba(228,198,143,.18); backface-visibility:hidden; }}
    .flip-page.next .flip-face.back, .flip-page.prev .flip-face.back {{ transform:rotateY(180deg); }}
    @keyframes flipNext {{ 0%{{transform:rotateY(0)}} 52%{{box-shadow:-24px 18px 42px rgba(0,0,0,.42)}} 100%{{transform:rotateY(-180deg)}} }}
    @keyframes flipPrev {{ 0%{{transform:rotateY(0)}} 52%{{box-shadow:24px 18px 42px rgba(0,0,0,.42)}} 100%{{transform:rotateY(180deg)}} }}
    .filmstrip {{ min-height:84px; padding:10px clamp(12px,3vw,28px) 14px; display:flex; gap:10px; overflow-x:auto; border-top:1px solid var(--line); background:linear-gradient(180deg,rgba(8,5,3,.45),rgba(8,5,3,.88)); }}
    .thumb {{ position:relative; width:48px; height:68px; padding:0; border:2px solid transparent; border-radius:7px; overflow:hidden; background:#160d08; cursor:pointer; flex:0 0 auto; }}
    .thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }} .thumb.is-active {{ border-color:var(--accent); }}
    .thumb span {{ position:absolute; left:4px; bottom:4px; min-width:18px; height:18px; display:grid; place-items:center; border-radius:6px; color:#1b100a; background:rgba(245,234,216,.9); font-size:10px; }}
    svg {{ width:20px; height:20px; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; fill:none; }}
    @media (max-width:760px) {{ .brand span{{display:none}} .icon-button{{width:38px;height:38px}} .counter{{min-width:68px;height:38px;padding-inline:8px}} .stage{{padding:10px}} .book-shell{{width:min(94vw,520px);height:min(74vh,760px)}} .book{{width:min(94vw,calc((74vh - 20px)*.5625));aspect-ratio:.5625/1;grid-template-columns:1fr}} .page-left,.spine{{display:none}} .page-right{{border-radius:8px;box-shadow:none}} .flip-page,.flip-page.next,.flip-page.prev{{left:0;right:auto;width:100%}} .hit{{width:34%}} .thumb{{width:42px;height:60px}} }}
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar"><div class="brand"><strong>{title}</strong><span>{escape(manifest.get("country_label") or country_code.upper())}</span></div><div class="controls"><button class="icon-button" id="prevButton" aria-label="Pagina anterioară"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"></path></svg></button><div class="counter" id="counter"></div><button class="icon-button" id="nextButton" aria-label="Pagina următoare"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"></path></svg></button><button class="icon-button" id="fitButton" aria-label="Schimbă încadrarea"><svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3"></path><path d="M16 3h3a2 2 0 0 1 2 2v3"></path><path d="M8 21H5a2 2 0 0 1-2-2v-3"></path><path d="M16 21h3a2 2 0 0 0 2-2v-3"></path></svg></button></div></header>
    <main class="stage"><section class="book-shell"><div class="book"><article class="page page-left" id="leftPage"></article><div class="spine"></div><article class="page page-right" id="rightPage"></article><div class="hit prev" id="prevHit"></div><div class="hit next" id="nextHit"></div><div class="flip-page" id="flipPage"></div></div></section></main>
    <nav class="filmstrip" id="filmstrip"></nav>
  </div>
  <script>
    const pages = {pages_json};
    const leftPage = document.getElementById("leftPage"), rightPage = document.getElementById("rightPage"), flipPage = document.getElementById("flipPage"), counter = document.getElementById("counter"), filmstrip = document.getElementById("filmstrip");
    const prevButton = document.getElementById("prevButton"), nextButton = document.getElementById("nextButton"), fitButton = document.getElementById("fitButton"), prevHit = document.getElementById("prevHit"), nextHit = document.getElementById("nextHit");
    let current = 0, animating = false, fitMode = "contain", animationId = 0;
    const isSinglePage = () => matchMedia("(max-width: 760px)").matches;
    const stepSize = () => isSinglePage() ? 1 : 2;
    const clampStart = i => Math.min(Math.max(0, isSinglePage() ? i : Math.floor(i / 2) * 2), Math.max(0, pages.length - 1));
    const pageTemplate = (page, index) => page ? `<img src="${{page.src}}" alt="${{page.alt}}" draggable="false"><span class="page-number">${{index + 1}}</span>` : "";
    function displaySpread(start) {{ const spreadStart = clampStart(start); if (isSinglePage()) {{ leftPage.innerHTML = ""; rightPage.innerHTML = pageTemplate(pages[spreadStart], spreadStart); }} else {{ leftPage.innerHTML = pageTemplate(pages[spreadStart], spreadStart); rightPage.innerHTML = pageTemplate(pages[spreadStart + 1], spreadStart + 1); }} }}
    function updateStatus(start) {{ const spreadStart = clampStart(start); if (isSinglePage()) {{ counter.textContent = `${{spreadStart + 1}} / ${{pages.length}}`; }} else {{ counter.textContent = `${{spreadStart + 1}}-${{Math.min(spreadStart + 2, pages.length)}} / ${{pages.length}}`; }} prevButton.disabled = spreadStart <= 0 || animating; nextButton.disabled = spreadStart + stepSize() >= pages.length || animating; renderFilmstrip(spreadStart); }}
    function render() {{ document.documentElement.style.setProperty("--fit", fitMode); current = clampStart(current); displaySpread(current); updateStatus(current); }}
    function renderFilmstrip(active = current) {{ const activeStart = clampStart(active), activeEnd = activeStart + stepSize() - 1; filmstrip.innerHTML = pages.map((page, index) => `<button class="thumb ${{index >= activeStart && index <= activeEnd ? "is-active" : ""}}" data-page="${{index}}" aria-label="Pagina ${{index + 1}}"><img src="${{page.src}}" alt=""><span>${{index + 1}}</span></button>`).join(""); }}
    const buildFlipFace = (frontPage, frontIndex, backPage, backIndex) => `<div class="flip-face front">${{pageTemplate(frontPage, frontIndex)}}</div><div class="flip-face back">${{pageTemplate(backPage, backIndex)}}</div>`;
    function animateFlip(direction, target) {{
      animating = true;
      const token = ++animationId;
      current = clampStart(current);
      const from = current;
      const to = clampStart(target);
      document.documentElement.style.setProperty("--fit", fitMode);
      if (direction === "next") {{
        const frontIndex = isSinglePage() ? from : from + 1;
        const backIndex = to;
        flipPage.innerHTML = buildFlipFace(pages[frontIndex], frontIndex, pages[backIndex], backIndex);
        if (isSinglePage()) {{
          displaySpread(to);
        }} else {{
          leftPage.innerHTML = pageTemplate(pages[from], from);
          rightPage.innerHTML = pageTemplate(pages[to + 1], to + 1);
        }}
      }} else {{
        const frontIndex = isSinglePage() ? from : from;
        const backIndex = isSinglePage() ? to : from - 1;
        flipPage.innerHTML = buildFlipFace(pages[frontIndex], frontIndex, pages[backIndex], backIndex);
        if (isSinglePage()) {{
          displaySpread(to);
        }} else {{
          leftPage.innerHTML = pageTemplate(pages[to], to);
          rightPage.innerHTML = pageTemplate(pages[from + 1], from + 1);
        }}
      }}
      updateStatus(to);
      flipPage.className = `flip-page ${{direction}}`;
      const finish = () => {{ if (token !== animationId) return; flipPage.className = "flip-page"; flipPage.innerHTML = ""; current = to; animating = false; render(); }};
      flipPage.addEventListener("animationend", finish, {{ once: true }});
      setTimeout(() => {{ if (animating && token === animationId) finish(); }}, 1000);
    }}
    function goNext() {{ if (!animating && current + stepSize() < pages.length) animateFlip("next", current + stepSize()); }}
    function goPrev() {{ if (!animating && current > 0) animateFlip("prev", current - stepSize()); }}
    prevButton.addEventListener("click", goPrev); nextButton.addEventListener("click", goNext); prevHit.addEventListener("click", goPrev); nextHit.addEventListener("click", goNext);
    fitButton.addEventListener("click", () => {{ fitMode = fitMode === "contain" ? "cover" : "contain"; render(); }});
    filmstrip.addEventListener("click", e => {{ const b = e.target.closest(".thumb"); if (b && !animating) {{ current = clampStart(Number(b.dataset.page)); render(); }} }});
    addEventListener("keydown", e => {{ if (e.key === "ArrowRight") goNext(); if (e.key === "ArrowLeft") goPrev(); }}); addEventListener("resize", render); render();
  </script>
</body>
</html>
"""
