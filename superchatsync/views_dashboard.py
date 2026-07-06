from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection
from django.http import HttpResponse


def q(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def one(sql, params=None):
    rows = q(sql, params)
    return rows[0] if rows else {}


def table_html(rows):
    import html
    from urllib.parse import quote

    if not rows:
        return "<p>Nu există date.</p>"

    headers = rows[0].keys()

    html_out = "<table><thead><tr>"
    for h in headers:
        html_out += f"<th>{html.escape(str(h))}</th>"
    html_out += "</tr></thead><tbody>"

    for row in rows:
        html_out += "<tr>"

        for h in headers:
            value = row.get(h, "")
            safe_value = html.escape(str(value if value is not None else ""))

            if h == "conversation_id" and value:
                url_value = quote(str(value))
                cell = f'<a href="/admin/ai-conversation/{url_value}/">{safe_value}</a>'
            else:
                cell = safe_value

            html_out += f"<td>{cell}</td>"

        html_out += "</tr>"

    html_out += "</tbody></table>"
    return html_out


@staff_member_required
def ai_dashboard(request):
    product_id = request.GET.get("product_id") or ""

    product_filter = ""
    params = []

    if product_id:
        product_filter = "AND ar.product_id = %s"
        params.append(product_id)

    overview = one(f"""
        SELECT
            COUNT(*) AS total_analyses,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_lead_score,
            SUM(CASE WHEN ar.client_intent = 'wants_to_order' THEN 1 ELSE 0 END) AS wants_to_order,
            SUM(CASE WHEN ar.client_intent = 'interested' THEN 1 ELSE 0 END) AS interested,
            SUM(CASE WHEN ar.client_intent = 'no_reply' THEN 1 ELSE 0 END) AS no_reply,
            SUM(CASE WHEN ar.sale_outcome = 'sold' THEN 1 ELSE 0 END) AS sold,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
    """, params)

    products = q("""
        SELECT DISTINCT
            ar.product_id,
            COALESCE(p.product_name, ar.product_id) AS product_name
        FROM analysis_results ar
        LEFT JOIN products p ON p.product_id = ar.product_id
        WHERE ar.analysis_status = 'completed'
        ORDER BY product_name
    """)

    by_product = q(f"""
        SELECT
            ar.product_id,
            COALESCE(p.product_name, ar.product_id) AS product_name,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.client_intent = 'wants_to_order' THEN 1 ELSE 0 END) AS wants_to_order,
            SUM(CASE WHEN ar.sale_outcome = 'sold' THEN 1 ELSE 0 END) AS sold,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        LEFT JOIN products p ON p.product_id = ar.product_id
        WHERE ar.analysis_status = 'completed'
        {product_filter}
        GROUP BY ar.product_id, p.product_name
        ORDER BY total DESC
    """, params)

    objections = q(f"""
        SELECT
            COALESCE(ar.main_objection, 'unclear') AS main_objection,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
        GROUP BY COALESCE(ar.main_objection, 'unclear')
        ORDER BY total DESC
    """, params)

    stages = q(f"""
        SELECT
            COALESCE(ar.lead_stage, 'unclear') AS lead_stage,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
        GROUP BY COALESCE(ar.lead_stage, 'unclear')
        ORDER BY total DESC
    """, params)

    suggestions_filter = ""
    suggestions_params = []

    if product_id:
        suggestions_filter = "WHERE product_id = %s"
        suggestions_params.append(product_id)

    suggestions = q(f"""
        SELECT
            status,
            suggestion_type,
            COUNT(*) AS total
        FROM product_feed_suggestions
        {suggestions_filter}
        GROUP BY status, suggestion_type
        ORDER BY status, total DESC
    """, suggestions_params)

    lost = q(f"""
        SELECT
            ar.conversation_id,
            ar.product_id,
            COALESCE(p.product_name, ar.product_id) AS product_name,
            ar.lead_score,
            ar.client_intent,
            ar.lead_stage,
            ar.main_objection,
            ar.sale_outcome,
            LEFT(COALESCE(ar.summary, ''), 180) AS summary,
            LEFT(COALESCE(ar.missed_opportunity, ''), 220) AS missed_opportunity,
            LEFT(COALESCE(ar.recommended_action, ''), 220) AS recommended_action
        FROM analysis_results ar
        LEFT JOIN products p ON p.product_id = ar.product_id
        WHERE ar.analysis_status = 'completed'
        {product_filter}
          AND (
                ar.sale_outcome IN ('not_sold', 'pending', 'unclear')
             OR ar.sale_outcome IS NULL
          )
        ORDER BY ar.lead_score DESC NULLS LAST, ar.analyzed_at DESC NULLS LAST
        LIMIT 30
    """, params)

    product_options = '<option value="">Toate produsele</option>'
    for p in products:
        selected = "selected" if str(p["product_id"]) == str(product_id) else ""
        product_options += f'<option value="{p["product_id"]}" {selected}>{p["product_name"]} — {p["product_id"]}</option>'

    html = f"""
    <!doctype html>
    <html lang="ro">
    <head>
        <meta charset="utf-8">
        <title>AI Sales Dashboard</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background: #f5f7fb;
                color: #111827;
                padding: 24px;
            }}
            h1, h2 {{
                margin-top: 0;
            }}
            .top {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }}
            .cards {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 14px;
                margin: 20px 0;
            }}
            .card {{
                background: white;
                border-radius: 12px;
                padding: 16px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .label {{
                font-size: 13px;
                color: #6b7280;
            }}
            .value {{
                font-size: 28px;
                font-weight: bold;
                margin-top: 6px;
            }}
            .section {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 22px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }}
            th, td {{
                border-bottom: 1px solid #e5e7eb;
                text-align: left;
                padding: 9px;
                vertical-align: top;
            }}
            th {{
                background: #f9fafb;
            }}
            a {{
                color: #2563eb;
                text-decoration: none;
            }}
            select, button {{
                padding: 8px 10px;
                border-radius: 8px;
                border: 1px solid #d1d5db;
            }}
            button {{
                background: #111827;
                color: white;
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <div>
                <h1>AI Sales Dashboard</h1>
                <div>Analiză conversații Superchat + Product Feed Suggestions</div>
            </div>
            <div>
                <a href="/admin/">← Admin</a> | <a href="/admin/ai-quality-report/">AI Quality Report</a>
            </div>
        </div>

        <div class="section">
            <form method="get">
                <strong>Filtru produs:</strong>
                <select name="product_id">{product_options}</select>
                <button type="submit">Aplică</button>
                <a href="/admin/ai-dashboard/" style="margin-left: 10px;">Resetează</a>
            </form>
        </div>

        <div class="cards">
            <div class="card"><div class="label">Analize AI</div><div class="value">{overview.get("total_analyses", 0)}</div></div>
            <div class="card"><div class="label">Scor mediu lead</div><div class="value">{overview.get("avg_lead_score", 0)}</div></div>
            <div class="card"><div class="label">Intenție comandă</div><div class="value">{overview.get("wants_to_order", 0)}</div></div>
            <div class="card"><div class="label">Interested</div><div class="value">{overview.get("interested", 0)}</div></div>
            <div class="card"><div class="label">No reply</div><div class="value">{overview.get("no_reply", 0)}</div></div>
            <div class="card"><div class="label">Sold</div><div class="value">{overview.get("sold", 0)}</div></div>
            <div class="card"><div class="label">Not sold</div><div class="value">{overview.get("not_sold", 0)}</div></div>
            <div class="card"><div class="label">Pending</div><div class="value">{overview.get("pending", 0)}</div></div>
        </div>

        <div class="section">
            <h2>Performanță pe produs</h2>
            {table_html(by_product)}
        </div>

        <div class="section">
            <h2>Obiecții principale</h2>
            {table_html(objections)}
        </div>

        <div class="section">
            <h2>Etape lead</h2>
            {table_html(stages)}
        </div>

        <div class="section">
            <h2>Product Feed Suggestions</h2>
            {table_html(suggestions)}
        </div>

        <div class="section">
            <h2>Top oportunități pierdute / pending</h2>
            {table_html(lost)}
        </div>
    </body>
    </html>
    """

    return HttpResponse(html)


@staff_member_required
def ai_conversation_detail(request, conversation_id):
    conversation = one("""
        SELECT
            c.conversation_id,
            c.client_name,
            c.channel,
            c.product_detected,
            p.product_name,
            c.product_detection_score,
            c.product_detection_source,
            c.has_client_reply,
            c.first_message_at,
            c.last_message_at
        FROM conversations c
        LEFT JOIN products p ON p.product_id = c.product_detected
        WHERE c.conversation_id = %s
    """, [conversation_id])

    analysis = one("""
        SELECT
            analysis_id,
            conversation_id,
            product_id,
            model,
            prompt_version,
            analysis_status,
            lead_score,
            client_intent,
            lead_stage,
            main_objection,
            sale_outcome,
            summary,
            missed_opportunity,
            operator_issue,
            workflow_issue,
            recommended_action,
            recommended_message,
            error,
            analyzed_at
        FROM analysis_results
        WHERE conversation_id = %s
        ORDER BY analyzed_at DESC NULLS LAST
        LIMIT 1
    """, [conversation_id])

    messages = q("""
        SELECT
            sent_at,
            sender_type,
            sender_name,
            LEFT(COALESCE(message_text, ''), 3000) AS message_text
        FROM messages
        WHERE conversation_id = %s
        ORDER BY sent_at ASC NULLS LAST, created_at ASC
    """, [conversation_id])

    suggestions = q("""
        SELECT
            suggestion_id,
            product_id,
            suggestion_type,
            title,
            suggested_question,
            suggested_answer,
            suggested_rule,
            suggested_keyword,
            reason,
            evidence,
            confidence_score,
            status,
            applied_target_table,
            applied_target_id,
            apply_error,
            created_at
        FROM product_feed_suggestions
        WHERE conversation_id = %s
        ORDER BY created_at DESC
    """, [conversation_id])

    def esc(value):
        import html
        return html.escape(str(value or ""))

    def nl(value):
        return esc(value).replace("\n", "<br>")

    messages_html = ""

    if messages:
        for m in messages:
            sender = esc(m.get("sender_type"))
            sender_name = esc(m.get("sender_name"))
            sent_at = esc(m.get("sent_at"))
            text = nl(m.get("message_text"))

            cls = "client" if sender == "client" else "operator"

            messages_html += f"""
            <div class="msg {cls}">
                <div class="msg-meta">
                    <strong>{sender}</strong>
                    <span>{sender_name}</span>
                    <span>{sent_at}</span>
                </div>
                <div class="msg-text">{text}</div>
            </div>
            """
    else:
        messages_html = "<p>Nu există mesaje.</p>"

    suggestions_html = suggestions_table_with_actions(suggestions)

    html = f"""
    <!doctype html>
    <html lang="ro">
    <head>
        <meta charset="utf-8">
        <title>AI Conversation Detail</title>
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
                margin-bottom: 20px;
            }}
            a {{
                color: #2563eb;
                text-decoration: none;
            }}
            .grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 18px;
                margin-bottom: 20px;
            }}
            .section {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 20px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .cards {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 12px;
                margin-bottom: 20px;
            }}
            .card {{
                background: white;
                border-radius: 12px;
                padding: 14px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .label {{
                font-size: 12px;
                color: #6b7280;
            }}
            .value {{
                font-size: 22px;
                font-weight: bold;
                margin-top: 5px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 13px;
            }}
            th, td {{
                border-bottom: 1px solid #e5e7eb;
                text-align: left;
                padding: 8px;
                vertical-align: top;
            }}
            th {{
                background: #f9fafb;
            }}
            .msg {{
                border-radius: 12px;
                padding: 12px;
                margin-bottom: 10px;
                max-width: 900px;
            }}
            .msg.client {{
                background: #ecfdf5;
                border-left: 4px solid #10b981;
            }}
            .msg.operator {{
                background: #eff6ff;
                border-left: 4px solid #3b82f6;
            }}
            .msg-meta {{
                display: flex;
                gap: 12px;
                color: #6b7280;
                font-size: 12px;
                margin-bottom: 7px;
            }}
            .msg-text {{
                white-space: normal;
                line-height: 1.45;
            }}
            .ai-box {{
                line-height: 1.45;
            }}
            .ai-box strong {{
                display: block;
                margin-top: 10px;
            }}
            .badge {{
                display: inline-block;
                background: #eef2ff;
                color: #3730a3;
                padding: 4px 8px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: 600;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <div>
                <h1>AI Conversation Detail</h1>
                <div>{esc(conversation_id)}</div>
            </div>
            <div>
                <a href="/admin/ai-dashboard/">← AI Dashboard</a> | <a href="/admin/ai-conversation/{esc(conversation_id)}/reanalyze/">Re-analyze AI</a> |
                <a href="/admin/ai-conversation/{esc(conversation_id)}/">Open in Admin</a>
            </div>
        </div>

        <div class="cards">
            <div class="card">
                <div class="label">Produs</div>
                <div class="value">{esc(conversation.get("product_name"))}</div>
                <div>{esc(conversation.get("product_detected"))}</div>
            </div>
            <div class="card">
                <div class="label">Product score</div>
                <div class="value">{esc(conversation.get("product_detection_score"))}</div>
                <div>{esc(conversation.get("product_detection_source"))}</div>
            </div>
            <div class="card">
                <div class="label">Lead score</div>
                <div class="value">{esc(analysis.get("lead_score"))}</div>
            </div>
            <div class="card">
                <div class="label">Outcome</div>
                <div class="value">{esc(analysis.get("sale_outcome"))}</div>
            </div>
        </div>

        <div class="grid">
            <div class="section">
                <h2>Conversation Info</h2>
                <p><strong>Client:</strong> {esc(conversation.get("client_name"))}</p>
                <p><strong>Channel:</strong> {esc(conversation.get("channel"))}</p>
                <p><strong>Has client reply:</strong> {esc(conversation.get("has_client_reply"))}</p>
                <p><strong>First message:</strong> {esc(conversation.get("first_message_at"))}</p>
                <p><strong>Last message:</strong> {esc(conversation.get("last_message_at"))}</p>
            </div>

            <div class="section ai-box">
                <h2>AI Analysis</h2>
                <p><span class="badge">{esc(analysis.get("client_intent"))}</span>
                   <span class="badge">{esc(analysis.get("lead_stage"))}</span>
                   <span class="badge">{esc(analysis.get("main_objection"))}</span></p>

                <strong>Summary</strong>
                <div>{nl(analysis.get("summary"))}</div>

                <strong>Missed opportunity</strong>
                <div>{nl(analysis.get("missed_opportunity"))}</div>

                <strong>Operator issue</strong>
                <div>{nl(analysis.get("operator_issue"))}</div>

                <strong>Workflow issue</strong>
                <div>{nl(analysis.get("workflow_issue"))}</div>

                <strong>Recommended action</strong>
                <div>{nl(analysis.get("recommended_action"))}</div>

                <strong>Recommended message</strong>
                <div>{nl(analysis.get("recommended_message"))}</div>
            </div>
        </div>

        <div class="section">
            <h2>Messages</h2>
            {messages_html}
        </div>

        <div class="section">
            <h2>Product Feed Suggestions from this conversation</h2>
            {suggestions_html}
        </div>
    </body>
    </html>
    """

    return HttpResponse(html)


# --- AI dashboard action views ---
import os
import sys
import subprocess
from django.shortcuts import redirect
from django.contrib import messages


AI_ACTION_WEB_DIR = "/opt/superchat-ai-agent/web"
AI_ACTION_LOG_DIR = "/opt/superchat-ai-agent/logs"


def _start_background_command(cmd, log_name):
    os.makedirs(AI_ACTION_LOG_DIR, exist_ok=True)
    log_path = os.path.join(AI_ACTION_LOG_DIR, log_name)

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd=AI_ACTION_WEB_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    return log_path


@staff_member_required
def ai_conversation_reanalyze(request, conversation_id):
    log_name = f"reanalyze_conversation_{conversation_id}.log"

    cmd = [
        sys.executable,
        os.path.join(AI_ACTION_WEB_DIR, "manage.py"),
        "analyze_conversations_ai",
        "--conversation-id",
        conversation_id,
        "--force",
    ]

    log_path = _start_background_command(cmd, log_name)

    messages.success(
        request,
        f"Re-analiza AI a pornit pentru conversația {conversation_id}. Log: {log_path}"
    )

    return redirect(f"/admin/ai-conversation/{conversation_id}/")


def _get_suggestion_conversation_id(suggestion_id):
    row = one("""
        SELECT conversation_id
        FROM product_feed_suggestions
        WHERE suggestion_id = %s
    """, [str(suggestion_id)])

    return row.get("conversation_id")


@staff_member_required
def ai_suggestion_approve(request, suggestion_id):
    conversation_id = _get_suggestion_conversation_id(suggestion_id)

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'approved',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [str(request.user), str(suggestion_id)])

    messages.success(request, "Sugestia a fost aprobată.")

    if conversation_id:
        return redirect(f"/admin/ai-conversation/{conversation_id}/")

    return redirect("/admin/ai-dashboard/")


@staff_member_required
def ai_suggestion_reject(request, suggestion_id):
    conversation_id = _get_suggestion_conversation_id(suggestion_id)

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'rejected',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [str(request.user), str(suggestion_id)])

    messages.success(request, "Sugestia a fost respinsă.")

    if conversation_id:
        return redirect(f"/admin/ai-conversation/{conversation_id}/")

    return redirect("/admin/ai-dashboard/")


@staff_member_required
def ai_suggestion_apply(request, suggestion_id):
    conversation_id = _get_suggestion_conversation_id(suggestion_id)

    # Dacă sugestia nu este încă approved, o marcăm approved înainte de apply.
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'approved',
                reviewed_by = COALESCE(reviewed_by, %s),
                reviewed_at = COALESCE(reviewed_at, NOW()),
                updated_at = NOW()
            WHERE suggestion_id = %s
              AND status IN ('pending_review', 'approved')
        """, [str(request.user), str(suggestion_id)])

    log_name = f"apply_suggestion_{suggestion_id}.log"

    cmd = [
        sys.executable,
        os.path.join(AI_ACTION_WEB_DIR, "manage.py"),
        "apply_product_feed_suggestions",
        "--suggestion-id",
        str(suggestion_id),
    ]

    log_path = _start_background_command(cmd, log_name)

    messages.success(
        request,
        f"Aplicarea sugestiei a pornit. Log: {log_path}"
    )

    if conversation_id:
        return redirect(f"/admin/ai-conversation/{conversation_id}/")

    return redirect("/admin/ai-dashboard/")


def suggestions_table_with_actions(rows):
    import html

    if not rows:
        return "<p>Nu există sugestii pentru această conversație.</p>"

    html_out = """
    <table>
        <thead>
            <tr>
                <th>Tip</th>
                <th>Status</th>
                <th>Confidence</th>
                <th>Title</th>
                <th>Suggestion</th>
                <th>Reason</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
    """

    for row in rows:
        sid = html.escape(str(row.get("suggestion_id") or ""))
        suggestion_type = html.escape(str(row.get("suggestion_type") or ""))
        status = html.escape(str(row.get("status") or ""))
        confidence = html.escape(str(row.get("confidence_score") or ""))
        title = html.escape(str(row.get("title") or ""))

        main_suggestion = (
            row.get("suggested_question")
            or row.get("suggested_answer")
            or row.get("suggested_rule")
            or row.get("suggested_keyword")
            or ""
        )

        main_suggestion = html.escape(str(main_suggestion))
        reason = html.escape(str(row.get("reason") or ""))

        actions = ""

        if status == "pending_review":
            actions += f'<a class="btn approve" href="/admin/ai-suggestion/{sid}/approve/">Approve</a> '
            actions += f'<a class="btn reject" href="/admin/ai-suggestion/{sid}/reject/">Reject</a> '

        if status in ["pending_review", "approved"]:
            actions += f'<a class="btn apply" href="/admin/ai-suggestion/{sid}/apply/">Apply</a> '

        if status == "approved":
            actions += f'<a class="btn reject" href="/admin/ai-suggestion/{sid}/reject/">Reject</a> '

        html_out += f"""
        <tr>
            <td>{suggestion_type}</td>
            <td><span class="badge">{status}</span></td>
            <td>{confidence}</td>
            <td>{title}</td>
            <td>{main_suggestion}</td>
            <td>{reason}</td>
            <td>{actions}</td>
        </tr>
        """

    html_out += "</tbody></table>"

    html_out += """
    <style>
        .btn {
            display: inline-block;
            padding: 5px 8px;
            border-radius: 7px;
            font-size: 12px;
            font-weight: 600;
            margin: 2px;
            color: white !important;
            text-decoration: none;
        }
        .btn.approve { background: #047857; }
        .btn.reject { background: #b91c1c; }
        .btn.apply { background: #1d4ed8; }
    </style>
    """

    return html_out
# --- End AI dashboard action views ---


@staff_member_required
def ai_quality_report(request):
    product_id = request.GET.get("product_id") or ""

    product_filter = ""
    params = []

    if product_id:
        product_filter = "AND ar.product_id = %s"
        params.append(product_id)

    overview = one(f"""
        SELECT
            COUNT(*) AS total_analyses,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN ar.client_intent = 'wants_to_order' THEN 1 ELSE 0 END) AS wants_to_order,
            SUM(CASE WHEN ar.lead_score >= 70 AND ar.sale_outcome IN ('not_sold', 'pending', 'unclear') THEN 1 ELSE 0 END) AS high_score_lost
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
    """, params)

    products = q("""
        SELECT DISTINCT
            ar.product_id,
            COALESCE(p.product_name, ar.product_id) AS product_name
        FROM analysis_results ar
        LEFT JOIN products p ON p.product_id = ar.product_id
        WHERE ar.analysis_status = 'completed'
        ORDER BY product_name
    """)

    objections = q(f"""
        SELECT
            COALESCE(ar.main_objection, 'unclear') AS problem,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
        GROUP BY COALESCE(ar.main_objection, 'unclear')
        ORDER BY total DESC
    """, params)

    lead_stage_losses = q(f"""
        SELECT
            COALESCE(ar.lead_stage, 'unclear') AS lead_stage,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
        {product_filter}
        GROUP BY COALESCE(ar.lead_stage, 'unclear')
        ORDER BY total DESC
    """, params)

    workflow_issues = q(f"""
        SELECT
            LEFT(NULLIF(TRIM(ar.workflow_issue), ''), 300) AS workflow_issue,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
          AND NULLIF(TRIM(ar.workflow_issue), '') IS NOT NULL
          AND LOWER(TRIM(ar.workflow_issue)) NOT IN ('none', 'n/a', 'nu este cazul', 'fără probleme')
          {product_filter}
        GROUP BY LEFT(NULLIF(TRIM(ar.workflow_issue), ''), 300)
        ORDER BY total DESC, avg_score DESC
        LIMIT 30
    """, params)

    operator_issues = q(f"""
        SELECT
            LEFT(NULLIF(TRIM(ar.operator_issue), ''), 300) AS operator_issue,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score,
            SUM(CASE WHEN ar.sale_outcome = 'not_sold' THEN 1 ELSE 0 END) AS not_sold,
            SUM(CASE WHEN ar.sale_outcome = 'pending' THEN 1 ELSE 0 END) AS pending
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
          AND NULLIF(TRIM(ar.operator_issue), '') IS NOT NULL
          AND LOWER(TRIM(ar.operator_issue)) NOT IN ('none', 'n/a', 'nu este cazul', 'fără probleme')
          {product_filter}
        GROUP BY LEFT(NULLIF(TRIM(ar.operator_issue), ''), 300)
        ORDER BY total DESC, avg_score DESC
        LIMIT 30
    """, params)

    recommended_actions = q(f"""
        SELECT
            LEFT(NULLIF(TRIM(ar.recommended_action), ''), 300) AS recommended_action,
            COUNT(*) AS total,
            COALESCE(AVG(ar.lead_score)::INT, 0) AS avg_score
        FROM analysis_results ar
        WHERE ar.analysis_status = 'completed'
          AND NULLIF(TRIM(ar.recommended_action), '') IS NOT NULL
          {product_filter}
        GROUP BY LEFT(NULLIF(TRIM(ar.recommended_action), ''), 300)
        ORDER BY total DESC, avg_score DESC
        LIMIT 30
    """, params)

    high_score_lost = q(f"""
        SELECT
            ar.conversation_id,
            ar.product_id,
            COALESCE(p.product_name, ar.product_id) AS product_name,
            ar.lead_score,
            ar.client_intent,
            ar.lead_stage,
            ar.main_objection,
            ar.sale_outcome,
            LEFT(COALESCE(ar.summary, ''), 180) AS summary,
            LEFT(COALESCE(ar.missed_opportunity, ''), 260) AS missed_opportunity,
            LEFT(COALESCE(ar.recommended_action, ''), 260) AS recommended_action
        FROM analysis_results ar
        LEFT JOIN products p ON p.product_id = ar.product_id
        WHERE ar.analysis_status = 'completed'
          AND ar.lead_score >= 70
          AND (
                ar.sale_outcome IN ('not_sold', 'pending', 'unclear')
             OR ar.sale_outcome IS NULL
          )
          {product_filter}
        ORDER BY ar.lead_score DESC NULLS LAST, ar.analyzed_at DESC NULLS LAST
        LIMIT 50
    """, params)

    suggestion_filter = ""
    suggestion_params = []

    if product_id:
        suggestion_filter = "WHERE product_id = %s"
        suggestion_params.append(product_id)

    suggestion_stats = q(f"""
        SELECT
            status,
            suggestion_type,
            COUNT(*) AS total,
            COALESCE(AVG(confidence_score)::INT, 0) AS avg_confidence
        FROM product_feed_suggestions
        {suggestion_filter}
        GROUP BY status, suggestion_type
        ORDER BY status, total DESC
    """, suggestion_params)

    consolidated_params = []

    consolidated_where = """
        WHERE created_by = 'ai_quality_report'
          AND status IN ('pending_review', 'approved', 'applied')
    """

    if product_id:
        consolidated_where += " AND product_id = %s"
        consolidated_params.append(product_id)

    consolidated_suggestions = q(f"""
        SELECT
            suggestion_id,
            product_id,
            suggestion_type,
            title,
            suggested_question,
            suggested_answer,
            suggested_rule,
            suggested_keyword,
            reason,
            evidence,
            confidence_score,
            status,
            applied_target_table,
            applied_target_id,
            apply_error,
            created_at
        FROM product_feed_suggestions
        {consolidated_where}
        ORDER BY
            CASE
                WHEN status = 'pending_review' THEN 1
                WHEN status = 'approved' THEN 2
                WHEN status = 'applied' THEN 3
                ELSE 4
            END,
            confidence_score DESC NULLS LAST,
            created_at DESC
        LIMIT 50
    """, consolidated_params)

    generate_suffix = f"?product_id={product_id}" if product_id else ""

    product_options = '<option value="">Toate produsele</option>'

    for p in products:
        selected = "selected" if str(p["product_id"]) == str(product_id) else ""
        product_options += f'<option value="{p["product_id"]}" {selected}>{p["product_name"]} — {p["product_id"]}</option>'

    html = f"""
    <!doctype html>
    <html lang="ro">
    <head>
        <meta charset="utf-8">
        <title>AI Quality Report</title>
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
                margin-bottom: 20px;
            }}
            a {{
                color: #2563eb;
                text-decoration: none;
            }}
            .cards {{
                display: grid;
                grid-template-columns: repeat(6, 1fr);
                gap: 14px;
                margin: 20px 0;
            }}
            .card {{
                background: white;
                border-radius: 12px;
                padding: 16px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            .label {{
                font-size: 13px;
                color: #6b7280;
            }}
            .value {{
                font-size: 26px;
                font-weight: bold;
                margin-top: 6px;
            }}
            .section {{
                background: white;
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 22px;
                box-shadow: 0 1px 4px rgba(0,0,0,.08);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 14px;
            }}
            th, td {{
                border-bottom: 1px solid #e5e7eb;
                text-align: left;
                padding: 9px;
                vertical-align: top;
            }}
            th {{
                background: #f9fafb;
            }}
            select, button {{
                padding: 8px 10px;
                border-radius: 8px;
                border: 1px solid #d1d5db;
            }}
            button {{
                background: #111827;
                color: white;
                cursor: pointer;
            }}
            .grid-2 {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 22px;
            }}
            .warning {{
                color: #b91c1c;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="top">
            <div>
                <h1>AI Quality Report</h1>
                <div>Probleme recurente, oportunități pierdute și recomandări de workflow</div>
            </div>
            <div>
                <a href="/admin/ai-dashboard/">← AI Dashboard</a> |
                <a href="/admin/">Admin</a>
            </div>
        </div>

        <div class="section">
            <form method="get">
                <strong>Filtru produs:</strong>
                <select name="product_id">{product_options}</select>
                <button type="submit">Aplică</button>
                
<a href="/admin/ai-quality-report/" style="margin-left: 10px;">Resetează</a>
            </form>
        </div>

        <div class="cards">
            <div class="card">
                <div class="label">Analize AI</div>
                <div class="value">{overview.get("total_analyses", 0)}</div>
            </div>
            <div class="card">
                <div class="label">Scor mediu</div>
                <div class="value">{overview.get("avg_score", 0)}</div>
            </div>
            <div class="card">
                <div class="label">Wants order</div>
                <div class="value">{overview.get("wants_to_order", 0)}</div>
            </div>
            <div class="card">
                <div class="label">Not sold</div>
                <div class="value">{overview.get("not_sold", 0)}</div>
            </div>
            <div class="card">
                <div class="label">Pending</div>
                <div class="value">{overview.get("pending", 0)}</div>
            </div>
            <div class="card">
                <div class="label">High score lost</div>
                <div class="value warning">{overview.get("high_score_lost", 0)}</div>
            </div>
        </div>

        <div class="grid-2">
            <div class="section">
                <h2>Obiecții principale</h2>
                {table_html(objections)}
            </div>

            <div class="section">
                <h2>Etape unde se pierd leadurile</h2>
                {table_html(lead_stage_losses)}
            </div>
        </div>

        <div class="section">
            <h2>Probleme de workflow repetate</h2>
            {table_html(workflow_issues)}
        </div>

        <div class="section">
            <h2>Probleme de operator repetate</h2>
            {table_html(operator_issues)}
        </div>

        <div class="section">
            <h2>Acțiuni recomandate de AI</h2>
            {table_html(recommended_actions)}
        </div>

        <div class="section">
            <div style="display:flex; justify-content:space-between; align-items:center; gap:20px;">
                <div>
                    <h2>Recommended Product Feed Updates</h2>
                    <div style="color:#6b7280; font-size:14px;">
                        Sugestii consolidate generate din probleme recurente, nu dintr-o singură conversație.
                    </div>
                </div>
                <div>
                    <a href="/admin/ai-quality-report/generate-suggestions/{generate_suffix}"
                       style="display:inline-block; padding:9px 14px; background:#1d4ed8; color:white; border-radius:8px; font-weight:600;">
                       Generate consolidated suggestions
                    </a>
                </div>
            </div>

            <div style="margin-top:16px;">
                {suggestions_table_with_actions(consolidated_suggestions)}
            </div>
        </div>

        <div class="section">
            <h2>Product Feed Suggestions — status general</h2>
            {table_html(suggestion_stats)}
        </div>

        <div class="section">
            <h2>High score lost / pending</h2>
            {table_html(high_score_lost)}
        </div>
    </body>
    </html>
    """

    return HttpResponse(html)


@staff_member_required
def ai_quality_generate_suggestions(request):
    import os
    import sys
    import subprocess
    from django.contrib import messages
    from django.shortcuts import redirect
    from urllib.parse import urlencode

    product_id = request.GET.get("product_id") or ""

    log_name = "generate_quality_improvement_suggestions.log"
    log_path = os.path.join("/opt/superchat-ai-agent/logs", log_name)
    os.makedirs("/opt/superchat-ai-agent/logs", exist_ok=True)

    cmd = [
        sys.executable,
        "/opt/superchat-ai-agent/web/manage.py",
        "generate_quality_improvement_suggestions",
    ]

    if product_id:
        cmd.extend(["--product-id", product_id])

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
        f"Generarea sugestiilor consolidate a pornit. Log: {log_path}"
    )

    if product_id:
        return redirect("/admin/ai-quality-report/?" + urlencode({"product_id": product_id}))

    return redirect("/admin/ai-quality-report/")


# --- Override suggestion actions with smart redirect ---
from django.shortcuts import redirect as _smart_redirect
from django.contrib import messages as _smart_messages
from django.utils.http import url_has_allowed_host_and_scheme as _url_is_safe


def _smart_back(request, conversation_id=None):
    next_url = request.GET.get("next") or request.META.get("HTTP_REFERER")

    if next_url and _url_is_safe(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return _smart_redirect(next_url)

    if conversation_id:
        return _smart_redirect(f"/admin/ai-conversation/{conversation_id}/")

    return _smart_redirect("/admin/ai-quality-report/")


def _get_suggestion_conversation_id_safe(suggestion_id):
    row = one("""
        SELECT conversation_id
        FROM product_feed_suggestions
        WHERE suggestion_id = %s
    """, [str(suggestion_id)])

    return row.get("conversation_id")


@staff_member_required
def ai_suggestion_approve(request, suggestion_id):
    conversation_id = _get_suggestion_conversation_id_safe(suggestion_id)

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'approved',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [str(request.user), str(suggestion_id)])

    _smart_messages.success(request, "Sugestia a fost aprobată.")

    return _smart_back(request, conversation_id)


@staff_member_required
def ai_suggestion_reject(request, suggestion_id):
    conversation_id = _get_suggestion_conversation_id_safe(suggestion_id)

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'rejected',
                reviewed_by = %s,
                reviewed_at = NOW(),
                updated_at = NOW()
            WHERE suggestion_id = %s
        """, [str(request.user), str(suggestion_id)])

    _smart_messages.success(request, "Sugestia a fost respinsă.")

    return _smart_back(request, conversation_id)


@staff_member_required
def ai_suggestion_apply(request, suggestion_id):
    import os
    import sys
    import subprocess

    conversation_id = _get_suggestion_conversation_id_safe(suggestion_id)

    with connection.cursor() as cur:
        cur.execute("""
            UPDATE product_feed_suggestions
            SET status = 'approved',
                reviewed_by = COALESCE(reviewed_by, %s),
                reviewed_at = COALESCE(reviewed_at, NOW()),
                updated_at = NOW()
            WHERE suggestion_id = %s
              AND status IN ('pending_review', 'approved')
        """, [str(request.user), str(suggestion_id)])

    os.makedirs("/opt/superchat-ai-agent/logs", exist_ok=True)

    log_path = f"/opt/superchat-ai-agent/logs/apply_suggestion_{suggestion_id}.log"

    cmd = [
        sys.executable,
        "/opt/superchat-ai-agent/web/manage.py",
        "apply_product_feed_suggestions",
        "--suggestion-id",
        str(suggestion_id),
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd="/opt/superchat-ai-agent/web",
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    _smart_messages.success(
        request,
        f"Aplicarea sugestiei a pornit. Log: {log_path}"
    )

    return _smart_back(request, conversation_id)
# --- End override suggestion actions with smart redirect ---
