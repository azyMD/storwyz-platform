import html
import os
import sys
import subprocess

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect


CATEGORY_LABELS = {
    "product_facts": "Product Facts",
    "offers": "Offers",
    "objection_rules": "Objection Rules",
    "sales_rules": "Sales Rules",
    "cross_sell_rules": "Cross-sell Rules",
    "product_faq": "Product FAQ",
    "detection_keywords": "Detection Keywords",
    "conversation_examples": "Conversation Examples",
    "workflow_rules": "Workflow Rules",
}


def dictfetchall(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_import(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM product_knowledge_imports
            WHERE import_id = %s
        """, [str(import_id)])
        rows = dictfetchall(cur)

    return rows[0] if rows else None


def get_items(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT *
            FROM product_knowledge_items
            WHERE import_id = %s
            ORDER BY
                category,
                CASE
                    WHEN status = 'pending_review' THEN 1
                    WHEN status = 'approved' THEN 2
                    WHEN status = 'applied' THEN 3
                    WHEN status = 'rejected' THEN 4
                    ELSE 5
                END,
                confidence_score DESC NULLS LAST,
                created_at DESC
        """, [str(import_id)])
        return dictfetchall(cur)


def get_counts(import_id):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT status, COUNT(*)
            FROM product_knowledge_items
            WHERE import_id = %s
            GROUP BY status
        """, [str(import_id)])
        return {row[0]: row[1] for row in cur.fetchall()}


def esc(value):
    return html.escape(str(value or ""))


def item_card(item):
    item_id = item["item_id"]
    status = item.get("status")

    actions = ""

    if status == "pending_review":
        actions += f'<a class="btn green" href="/admin/product-knowledge-item/{item_id}/approve/">Approve</a>'
        actions += f'<a class="btn red" href="/admin/product-knowledge-item/{item_id}/reject/">Reject</a>'

    if status in ["pending_review", "approved", "error"]:
        actions += f'<a class="btn blue" href="/admin/product-knowledge-item/{item_id}/apply/">Apply</a>'

    content_parts = []

    for label, key in [
        ("Title", "title"),
        ("Question", "question"),
        ("Answer", "answer"),
        ("Rule", "rule"),
        ("Keyword", "keyword"),
        ("Description", "description"),
        ("Price", "price"),
        ("Target product", "target_product_name"),
        ("Evidence", "evidence"),
    ]:
        value = item.get(key)

        if value:
            content_parts.append(f"<div><strong>{label}:</strong> {esc(value)}</div>")

    error_html = ""

    if item.get("apply_error"):
        error_html = f'<div class="error"><strong>Apply error:</strong> {esc(item.get("apply_error"))}</div>'

    return f"""
    <div class="item-card status-{esc(status)}">
        <div class="item-head">
            <div>
                <strong>{esc(item.get("category"))}</strong>
                <span class="badge">{esc(status)}</span>
                <span class="confidence">confidence: {esc(item.get("confidence_score"))}</span>
            </div>
            <div>{actions}</div>
        </div>
        <div class="item-body">
            {''.join(content_parts)}
            {error_html}
            <div class="small">
                item_id: {esc(item_id)}
                {f' | applied: {esc(item.get("applied_target_table"))} / {esc(item.get("applied_target_id"))}' if item.get("applied_target_table") else ''}
            </div>
        </div>
    </div>
    """


@staff_member_required
def product_knowledge_items_page(request, import_id):
    item = get_import(import_id)

    if not item:
        return HttpResponse("Import not found", status=404)

    items = get_items(import_id)
    counts = get_counts(import_id)

    grouped = {}

    for row in items:
        grouped.setdefault(row["category"], []).append(row)

    groups_html = ""

    if not items:
        groups_html = """
        <div class="empty">
            Încă nu există Product Knowledge Items.
            Rulează acțiunea: Create AI Knowledge Items from DOCX.
        </div>
        """
    else:
        for category, rows in grouped.items():
            label = CATEGORY_LABELS.get(category, category)
            cards = "\n".join(item_card(x) for x in rows)

            groups_html += f"""
            <div class="section">
                <h2>{esc(label)} <span class="count">{len(rows)}</span></h2>
                {cards}
            </div>
            """

    page = f"""
    <!doctype html>
    <html lang="ro">
    <head>
        <meta charset="utf-8">
        <title>Extracted Product Knowledge</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                color: #111827;
                padding: 24px;
            }}
            a {{
                text-decoration: none;
            }}
            .top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 22px;
            }}
            .card, .section {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 18px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .stats {{
                display: flex;
                gap: 12px;
                flex-wrap: wrap;
            }}
            .stat {{
                background: #f3f4f6;
                border-radius: 8px;
                padding: 8px 12px;
            }}
            .item-card {{
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 14px;
                margin-bottom: 12px;
                background: #ffffff;
            }}
            .item-head {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                gap: 12px;
                margin-bottom: 10px;
            }}
            .item-body div {{
                margin: 5px 0;
            }}
            .badge {{
                display: inline-block;
                padding: 3px 7px;
                border-radius: 6px;
                background: #e5e7eb;
                font-size: 12px;
                margin-left: 8px;
            }}
            .confidence {{
                color: #6b7280;
                font-size: 12px;
                margin-left: 8px;
            }}
            .btn {{
                display: inline-block;
                padding: 7px 10px;
                border-radius: 7px;
                color: white;
                font-weight: 600;
                margin-left: 6px;
                font-size: 13px;
            }}
            .green {{ background: #15803d; }}
            .red {{ background: #b91c1c; }}
            .blue {{ background: #1d4ed8; }}
            .dark {{ background: #111827; }}
            .small {{
                color: #6b7280;
                font-size: 12px;
                margin-top: 10px;
            }}
            .error {{
                color: #b91c1c;
                background: #fee2e2;
                padding: 8px;
                border-radius: 8px;
                margin-top: 8px;
            }}
            .empty {{
                background: #fff7ed;
                border: 1px solid #fed7aa;
                padding: 16px;
                border-radius: 10px;
            }}
            .count {{
                color: #6b7280;
                font-size: 16px;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <div>
                <h1>Extracted Product Knowledge</h1>
                <div>Import: {esc(import_id)}</div>
            </div>
            <div>
                <a class="btn dark" href="/admin/superchatsync/productknowledgeimport/">Back to imports</a>
                <a class="btn green" href="/admin/product-knowledge-import/{esc(import_id)}/approve-high-confidence/">Approve high confidence</a>
                <a class="btn blue" href="/admin/product-knowledge-import/{esc(import_id)}/apply-approved/">Apply approved</a>
            </div>
        </div>

        <div class="card">
            <strong>Product:</strong> {esc(item.get("product_id"))}<br>
            <strong>Title:</strong> {esc(item.get("title"))}<br>
            <strong>Package status:</strong> {esc(item.get("knowledge_package_status"))}<br>
            <strong>Package dir:</strong> {esc(item.get("knowledge_package_dir"))}<br>
            <br>
            <div class="stats">
                <div class="stat">pending: {counts.get("pending_review", 0)}</div>
                <div class="stat">approved: {counts.get("approved", 0)}</div>
                <div class="stat">applied: {counts.get("applied", 0)}</div>
                <div class="stat">rejected: {counts.get("rejected", 0)}</div>
                <div class="stat">error: {counts.get("error", 0)}</div>
            </div>
        </div>

        {groups_html}
    </body>
    </html>
    """

    return HttpResponse(page)


@staff_member_required
def approve_item(request, item_id):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_knowledge_items
            SET status = 'approved',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE item_id = %s
            RETURNING import_id
        """, [str(request.user), str(item_id)])
        row = cur.fetchone()

    messages.success(request, "Knowledge item aprobat.")

    if row:
        return redirect(f"/admin/product-knowledge-import/{row[0]}/items/")

    return redirect("/admin/superchatsync/productknowledgeitem/")


@staff_member_required
def reject_item(request, item_id):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_knowledge_items
            SET status = 'rejected',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE item_id = %s
            RETURNING import_id
        """, [str(request.user), str(item_id)])
        row = cur.fetchone()

    messages.success(request, "Knowledge item respins.")

    if row:
        return redirect(f"/admin/product-knowledge-import/{row[0]}/items/")

    return redirect("/admin/superchatsync/productknowledgeitem/")


@staff_member_required
def apply_item(request, item_id):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_knowledge_items
            SET status = 'approved',
                reviewed_by = COALESCE(reviewed_by, %s),
                reviewed_at = COALESCE(reviewed_at, NOW()),
                updated_at = NOW()
            WHERE item_id = %s
            RETURNING import_id
        """, [str(request.user), str(item_id)])
        row = cur.fetchone()

    log_dir = "/opt/superchat-ai-agent/logs"
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"apply_knowledge_item_{item_id}.log")

    cmd = [
        sys.executable,
        "/opt/superchat-ai-agent/web/manage.py",
        "apply_product_knowledge_items",
        "--item-id",
        str(item_id),
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd="/opt/superchat-ai-agent/web",
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    messages.success(request, f"Apply pornit. Log: {log_path}")

    if row:
        return redirect(f"/admin/product-knowledge-import/{row[0]}/items/")

    return redirect("/admin/superchatsync/productknowledgeitem/")


@staff_member_required
def approve_high_confidence(request, import_id):
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_knowledge_items
            SET status = 'approved',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE import_id = %s
              AND status = 'pending_review'
              AND confidence_score >= 70
        """, [str(request.user), str(import_id)])

    messages.success(request, "Toate itemurile cu confidence >= 70 au fost aprobate.")
    return redirect(f"/admin/product-knowledge-import/{import_id}/items/")


@staff_member_required
def apply_approved(request, import_id):
    log_dir = "/opt/superchat-ai-agent/logs"
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"apply_knowledge_import_{import_id}.log")

    cmd = [
        sys.executable,
        "/opt/superchat-ai-agent/web/manage.py",
        "apply_product_knowledge_items",
        "--import-id",
        str(import_id),
        "--approved-only",
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd="/opt/superchat-ai-agent/web",
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    messages.success(request, f"Apply approved pornit. Log: {log_path}")
    return redirect(f"/admin/product-knowledge-import/{import_id}/items/")
