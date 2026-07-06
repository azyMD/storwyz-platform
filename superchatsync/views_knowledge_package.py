import html
import os
import sys
import subprocess
from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.safestring import mark_safe


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


@staff_member_required
def product_knowledge_package_preview(request, import_id):
    item = get_import(import_id)

    if not item:
        return HttpResponse("Import not found", status=404)

    package_dir = item.get("knowledge_package_dir")
    status = item.get("knowledge_package_status")
    error = item.get("knowledge_package_error")

    rows_html = ""

    if package_dir and Path(package_dir).exists():
        package_path = Path(package_dir)

        for file_path in sorted(package_path.iterdir()):
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")[:60000]
                except Exception as e:
                    content = f"Cannot read file: {e}"

                rows_html += f"""
                <div class="file-block">
                    <h3>{html.escape(file_path.name)}</h3>
                    <pre>{html.escape(content)}</pre>
                </div>
                """
    else:
        rows_html = """
        <div class="empty">
            Knowledge package încă nu există. Rulează acțiunea: Create AI knowledge package.
        </div>
        """

    convert_button = ""

    if package_dir and Path(package_dir).exists():
        convert_button = f"""
        <a class="btn green" href="/admin/product-knowledge-import/{import_id}/convert-package/">
            Convert package to Product Feed Suggestions
        </a>
        """

    page = f"""
    <!doctype html>
    <html lang="ro">
    <head>
        <meta charset="utf-8">
        <title>Knowledge Package Preview</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                color: #111827;
                padding: 24px;
            }}
            .top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 22px;
            }}
            .card {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 18px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .file-block {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 18px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            pre {{
                white-space: pre-wrap;
                background: #111827;
                color: #e5e7eb;
                padding: 14px;
                border-radius: 8px;
                max-height: 600px;
                overflow: auto;
                font-size: 13px;
            }}
            .btn {{
                display: inline-block;
                padding: 9px 14px;
                border-radius: 8px;
                background: #1d4ed8;
                color: white;
                text-decoration: none;
                font-weight: 600;
                margin-right: 8px;
            }}
            .green {{
                background: #15803d;
            }}
            .red {{
                color: #b91c1c;
                font-weight: bold;
            }}
            .empty {{
                background: #fff7ed;
                border: 1px solid #fed7aa;
                padding: 16px;
                border-radius: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <div>
                <h1>Knowledge Package Preview</h1>
                <div>Import: {html.escape(str(import_id))}</div>
            </div>
            <div>
                <a class="btn" href="/admin/superchatsync/productknowledgeimport/">Back to imports</a>
                {convert_button}
            </div>
        </div>

        <div class="card">
            <strong>Product:</strong> {html.escape(str(item.get("product_id")))}<br>
            <strong>Title:</strong> {html.escape(str(item.get("title") or ""))}<br>
            <strong>Status:</strong> {html.escape(str(status))}<br>
            <strong>Package dir:</strong> {html.escape(str(package_dir or ""))}<br>
            <strong>Error:</strong> <span class="red">{html.escape(str(error or ""))}</span>
        </div>

        {rows_html}
    </body>
    </html>
    """

    return HttpResponse(page)


@staff_member_required
def product_knowledge_package_convert(request, import_id):
    log_dir = "/opt/superchat-ai-agent/logs"
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, f"convert_knowledge_package_{import_id}.log")

    cmd = [
        sys.executable,
        "/opt/superchat-ai-agent/web/manage.py",
        "convert_knowledge_package_to_suggestions",
        "--import-id",
        str(import_id),
        "--force",
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd="/opt/superchat-ai-agent/web",
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    messages.success(
        request,
        f"Conversia package → Product Feed Suggestions a pornit. Log: {log_path}"
    )

    return redirect(f"/admin/product-knowledge-import/{import_id}/package/")
