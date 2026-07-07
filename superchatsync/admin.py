
# --- Global imports for Product Knowledge workflow ---
from django.db import connection, transaction
from django.utils import timezone
# --- End Product Knowledge workflow imports ---

import os
import sys
import subprocess
from datetime import timedelta
from functools import lru_cache
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.paginator import Paginator
from django.db.utils import OperationalError
from django.utils.html import format_html
from django.utils.functional import cached_property
from django.utils import timezone

from .models import SuperchatSyncRun, SuperchatSyncCandidate
from .shortlinks import short_url_for_code


WEB_DIR = "/opt/superchat-ai-agent/web"
LOG_DIR = "/opt/superchat-ai-agent/logs"


class SuperchatSyncCandidateInline(admin.TabularInline):
    model = SuperchatSyncCandidate
    extra = 0
    fields = (
        "conversation_id",
        "superchat_status",
        "inbox_name",
        "local_exists",
        "change_reason",
        "decision",
        "extract_status",
        "export_id",
        "zip_path",
        "pdf_path",
        "error",
    )
    readonly_fields = (
        "conversation_id",
        "superchat_status",
        "inbox_name",
        "local_exists",
        "change_reason",
        "extract_status",
        "export_id",
        "zip_path",
        "pdf_path",
        "error",
    )
    can_delete = False
    show_change_link = True


@admin.register(SuperchatSyncRun)
class SuperchatSyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "short_run_id",
        "run_type",
        "status",
        "progress_bar",
        "total_checked",
        "candidates_found",
        "total_to_extract",
        "processed_count",
        "downloaded_count",
        "parsed_count",
        "error_count",
        "stop_requested",
        "started_at",
        "finished_at",
    )
    list_filter = ("status", "run_type", "stop_requested", "started_at")
    search_fields = ("run_id", "current_conversation_id", "notes", "error")
    readonly_fields = (
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "updated_at",
        "total_checked",
        "candidates_found",
        "total_to_extract",
        "processed_count",
        "downloaded_count",
        "parsed_count",
        "error_count",
        "current_conversation_id",
        "progress_bar",
        "error",
        "metadata",
    )
    fields = (
        "run_id",
        "run_type",
        "status",
        "start_date",
        "end_date",
        "started_at",
        "finished_at",
        "updated_at",
        "stop_requested",
        "progress_bar",
        "total_checked",
        "candidates_found",
        "total_to_extract",
        "processed_count",
        "downloaded_count",
        "parsed_count",
        "error_count",
        "current_conversation_id",
        "notes",
        "error",
        "metadata",
    )
    inlines = [SuperchatSyncCandidateInline]

    actions = [
        "start_discover_updates",
        "start_extract_approved",
        "request_stop",
        "mark_waiting_approval",
        "mark_completed",
    ]

    def short_run_id(self, obj):
        return str(obj.run_id)[:8]

    short_run_id.short_description = "Run ID"

    def progress_bar(self, obj):
        percent = obj.progress_percent

        if obj.status in ("extracting", "stopping", "stopped", "completed"):
            label = f"{obj.processed_count}/{obj.total_to_extract} ({percent}%)"
        elif obj.status in ("discovering", "waiting_approval"):
            label = f"Checked: {obj.total_checked} | Candidates: {obj.candidates_found}"
            percent = 100 if obj.status == "waiting_approval" else 20
        else:
            label = obj.status
            percent = 0

        return format_html(
            """
            <div style="width:220px;border:1px solid #ccc;height:18px;">
                <div style="background:#79aec8;width:{}%;height:18px;"></div>
            </div>
            <small>{}</small>
            """,
            percent,
            label,
        )

    progress_bar.short_description = "Progress"

    @admin.action(description="Start discover updates in background")
    def start_discover_updates(self, request, queryset):
        os.makedirs(LOG_DIR, exist_ok=True)

        if queryset.count() != 1:
            self.message_user(request, "Selectează exact un singur sync run.", messages.ERROR)
            return

        run = queryset.first()

        active_exists = (
            SuperchatSyncRun.objects
            .filter(status__in=["discovering", "extracting", "stopping"])
            .exclude(run_id=run.run_id)
            .exists()
        )

        if active_exists:
            self.message_user(
                request,
                "Update in progress. Nu poți porni alt sync până nu se termină.",
                messages.ERROR,
            )
            return

        if run.status in ("discovering", "extracting", "stopping"):
            self.message_user(request, "Acest run este deja în lucru.", messages.WARNING)
            return

        log_path = os.path.join(LOG_DIR, f"discover_{run.run_id}.log")

        cmd = [
            sys.executable,
            os.path.join(WEB_DIR, "manage.py"),
            "discover_superchat_updates",
            "--run-id",
            str(run.run_id),
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            subprocess.Popen(
                cmd,
                cwd=WEB_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        run.notes = (run.notes or "") + f"\nDiscover started in background. Log: {log_path}"
        run.save(update_fields=["notes", "updated_at"])

        self.message_user(
            request,
            f"Discover started. Refresh pagina pentru progress. Log: {log_path}",
            messages.SUCCESS,
        )


    @admin.action(description="Start extraction for approved candidates in background")
    def start_extract_approved(self, request, queryset):
        os.makedirs(LOG_DIR, exist_ok=True)

        if queryset.count() != 1:
            self.message_user(request, "Selectează exact un singur sync run.", messages.ERROR)
            return

        run = queryset.first()

        active_exists = (
            SuperchatSyncRun.objects
            .filter(status__in=["discovering", "extracting", "stopping"])
            .exclude(run_id=run.run_id)
            .exists()
        )

        if active_exists:
            self.message_user(
                request,
                "Update in progress. Nu poți porni alt sync până nu se termină.",
                messages.ERROR,
            )
            return

        if run.status in ("discovering", "extracting", "stopping"):
            self.message_user(request, "Acest run este deja în lucru.", messages.WARNING)
            return

        approved_count = run.candidates.filter(decision="approved").exclude(
            extract_status__in=["downloaded", "parsed", "skipped"]
        ).count()

        if approved_count == 0:
            self.message_user(
                request,
                "Nu există candidați approved pentru extragere.",
                messages.WARNING,
            )
            return

        log_path = os.path.join(LOG_DIR, f"extract_{run.run_id}.log")

        cmd = [
            sys.executable,
            os.path.join(WEB_DIR, "manage.py"),
            "extract_superchat_exports",
            "--run-id",
            str(run.run_id),
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            subprocess.Popen(
                cmd,
                cwd=WEB_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        run.notes = (run.notes or "") + f"\nExtraction started in background. Log: {log_path}"
        run.total_to_extract = approved_count
        run.save(update_fields=["notes", "total_to_extract", "updated_at"])

        self.message_user(
            request,
            f"Extraction started for {approved_count} candidates. Refresh pagina pentru progress. Log: {log_path}",
            messages.SUCCESS,
        )


    @admin.action(description="Request stop")
    def request_stop(self, request, queryset):
        count = 0
        for run in queryset:
            if run.status in ("discovering", "extracting"):
                run.status = "stopping"
                run.stop_requested = True
                run.updated_at = timezone.now()
                run.save(update_fields=["status", "stop_requested", "updated_at"])
                count += 1
        self.message_user(request, f"Stop requested for {count} sync run(s).", messages.WARNING)

    @admin.action(description="Mark waiting approval")
    def mark_waiting_approval(self, request, queryset):
        queryset.update(status="waiting_approval", updated_at=timezone.now())
        self.message_user(request, "Selected runs marked as waiting approval.", messages.SUCCESS)

    @admin.action(description="Mark completed")
    def mark_completed(self, request, queryset):
        queryset.update(status="completed", finished_at=timezone.now(), updated_at=timezone.now())
        self.message_user(request, "Selected runs marked as completed.", messages.SUCCESS)


@admin.register(SuperchatSyncCandidate)
class SuperchatSyncCandidateAdmin(admin.ModelAdmin):
    list_display = (
        "conversation_id",
        "run_short",
        "decision",
        "extract_status",
        "superchat_status",
        "inbox_name",
        "local_exists",
        "change_reason",
        "export_id",
        "has_zip",
        "has_pdf",
        "updated_at",
    )
    list_filter = (
        "decision",
        "extract_status",
        "superchat_status",
        "local_exists",
        "inbox_name",
        "created_at",
    )
    search_fields = (
        "conversation_id",
        "export_id",
        "inbox_name",
        "change_reason",
        "error",
    )
    readonly_fields = (
        "candidate_id",
        "run",
        "conversation_id",
        "superchat_status",
        "channel_id",
        "channel_type",
        "inbox_id",
        "inbox_name",
        "superchat_url",
        "local_exists",
        "local_last_imported_at",
        "superchat_open_until",
        "change_reason",
        "extract_status",
        "export_id",
        "export_status",
        "export_link",
        "export_link_valid_until",
        "zip_path",
        "pdf_path",
        "messages_found",
        "attachments_found",
        "error",
        "raw_payload",
        "created_at",
        "updated_at",
    )
    actions = [
        "approve_selected",
        "skip_selected",
        "reset_to_pending",
    ]

    def run_short(self, obj):
        return str(obj.run_id)[:8]

    run_short.short_description = "Run"

    def has_zip(self, obj):
        return bool(obj.zip_path)

    has_zip.boolean = True
    has_zip.short_description = "ZIP"

    def has_pdf(self, obj):
        return bool(obj.pdf_path)

    has_pdf.boolean = True
    has_pdf.short_description = "PDF"

    @admin.action(description="Approve selected for extraction")
    def approve_selected(self, request, queryset):
        updated = queryset.update(decision="approved")
        self.message_user(request, f"{updated} candidate(s) approved.", messages.SUCCESS)

    @admin.action(description="Skip selected")
    def skip_selected(self, request, queryset):
        updated = queryset.update(decision="skipped", extract_status="skipped")
        self.message_user(request, f"{updated} candidate(s) skipped.", messages.WARNING)

    @admin.action(description="Reset selected to pending")
    def reset_to_pending(self, request, queryset):
        updated = queryset.update(decision="pending", extract_status="pending", error=None)
        self.message_user(request, f"{updated} candidate(s) reset.", messages.SUCCESS)



from django.utils.html import format_html
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum

from .models import (
    Conversation,
    CustomerProfile,
    Message,
    CustomerChannelIdentity,
    CustomerCommunicationEvent,
    CustomerOrder,
    CustomerOrderPhoneLink,
    CustomerConversionEvent,
    CurrencyMonthlyRate,
    CustomerSegment,
    CustomerSegmentMembership,
    FitexpressCountry,
    FitexpressOrderSnapshot,
    FitexpressProductMapping,
)


def _estimated_model_count(model):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE((SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)), 0)",
                [model._meta.db_table],
            )
            row = cursor.fetchone()
        return max(int(row[0] or 0), 0)
    except Exception:
        return 0


def _bounded_queryset_count(queryset, timeout_ms=800, fallback=None):
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = %s", [int(timeout_ms)])
            return queryset.count()
    except OperationalError:
        return fallback
    except Exception:
        return fallback


def _format_count(value, approximate=False):
    if value is None:
        return "-"
    text = f"{int(value):,}".replace(",", " ")
    return text


class FastAdminPaginator(Paginator):
    @cached_property
    def count(self):
        queryset = self.object_list
        query = getattr(queryset, "query", None)
        model = getattr(queryset, "model", None)
        if query is not None and model is not None:
            if not query.where:
                return _estimated_model_count(model)
            fallback = min(_estimated_model_count(model), self.per_page * 1000)
            return _bounded_queryset_count(queryset, timeout_ms=900, fallback=fallback)
        try:
            return len(queryset)
        except TypeError:
            return 0


def _profile_status_options():
    return (
        ("active", "active"),
        ("inactive", "inactive"),
        ("blocked", "blocked"),
        ("merged", "merged"),
    )


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    readonly_fields = (
        "sent_at",
        "sender_type",
        "sender_name",
        "message_text",
        "message_type",
        "button_clicked",
        "is_client_reply",
    )
    fields = (
        "sent_at",
        "sender_type",
        "sender_name",
        "message_text",
        "message_type",
        "button_clicked",
        "is_client_reply",
    )
    ordering = ("sent_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FitexpressCountry)
class FitexpressCountryAdmin(admin.ModelAdmin):
    list_display = (
        "country_id",
        "country_name",
        "iso2",
        "default_language",
        "phone_prefixes_display",
        "active",
    )
    search_fields = ("country_id", "country_name", "iso2", "default_language")
    list_filter = ("active", "default_language", "iso2")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("country_name",)

    def phone_prefixes_display(self, obj):
        return ", ".join(obj.phone_prefixes or []) or "-"

    phone_prefixes_display.short_description = "phone prefixes"


@admin.register(FitexpressProductMapping)
class FitexpressProductMappingAdmin(admin.ModelAdmin):
    list_display = (
        "product_name",
        "product_id",
        "fitexpress_product_id",
        "match_status",
        "active",
        "landing_link",
    )
    search_fields = (
        "product_id",
        "product_name",
        "fitexpress_product_id",
        "landing_url",
    )
    list_filter = ("active", "match_status")
    readonly_fields = ("mapping_id", "created_at", "updated_at")
    ordering = ("product_name",)

    def landing_link(self, obj):
        if not obj.landing_url:
            return "-"
        return format_html('<a href="{}" target="_blank">open</a>', obj.landing_url)

    landing_link.short_description = "landing"


@admin.register(FitexpressOrderSnapshot)
class FitexpressOrderSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "external_order_id",
        "status_id",
        "product_id",
        "country_id",
        "quantity",
        "cost",
        "shipping_cost",
        "currency_id",
        "payment_type",
        "created_at_remote",
        "updated_at_remote",
        "fetched_at",
    )
    search_fields = (
        "external_order_id",
        "product_id",
        "product_sku",
        "customer_phone",
        "normalized_phone",
        "customer_name",
        "customer_address",
        "customer_comment",
    )
    list_filter = (
        "status_id",
        "country_id",
        "product_id",
        "currency_id",
        "payment_type",
        "customer_paid_online",
        "created_at_remote",
        "updated_at_remote",
        "fetched_at",
    )
    date_hierarchy = "created_at_remote"
    readonly_fields = (
        "snapshot_id",
        "external_order_id",
        "status_id",
        "product_id",
        "country_id",
        "region_id",
        "product_sku",
        "quantity",
        "quantity_number",
        "cost",
        "shipping_cost",
        "currency_id",
        "payment_type",
        "customer_paid_online",
        "customer_name",
        "customer_location",
        "customer_address",
        "customer_phone",
        "normalized_phone",
        "customer_comment",
        "customer_zipcode",
        "customer_email",
        "customer_age",
        "customer_gender",
        "customer_streetnr",
        "customer_blocknr",
        "customer_appartmentnr",
        "deliver_date",
        "created_at_remote",
        "updated_at_remote",
        "referral",
        "source",
        "curier_id",
        "courier_note",
        "tracking_url",
        "tracking_pdf",
        "approve_method",
        "raw_payload",
        "fetch_params",
        "fetched_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at_remote", "-updated_at_remote", "external_order_id")

    def has_add_permission(self, request):
        return False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "short_conversation_id",
        "client_name",
        "client_phone",
        "channel",
        "product_detected",
        "has_client_reply",
        "first_message_at",
        "last_message_at",
        "message_count",
        "pdf_path_short",
    )
    search_fields = (
        "conversation_id",
        "client_name",
        "client_phone",
        "client_email",
        "product_detected",
        "campaign_name",
        "workflow_name",
        "messages__message_text",
    )
    list_filter = (
        "channel",
        "has_client_reply",
        "product_detected",
        "source",
        "status",
    )
    readonly_fields = (
        "conversation_id",
        "channel",
        "client_name",
        "client_phone",
        "client_email",
        "product_detected",
        "campaign_id",
        "campaign_name",
        "workflow_id",
        "workflow_name",
        "first_message_at",
        "first_client_reply_at",
        "last_message_at",
        "has_client_reply",
        "operator_names",
        "raw_pdf_path",
        "raw_zip_path",
        "source",
        "status",
        "metadata",
        "created_at",
        "updated_at",
        "last_imported_at",
    )
    inlines = [MessageInline]
    ordering = ("-last_message_at",)

    def short_conversation_id(self, obj):
        return obj.conversation_id[:18] + "..." if obj.conversation_id and len(obj.conversation_id) > 18 else obj.conversation_id
    short_conversation_id.short_description = "conversation_id"

    def message_count(self, obj):
        return obj.messages.count()
    message_count.short_description = "messages"

    def pdf_path_short(self, obj):
        if not obj.raw_pdf_path:
            return "-"
        return obj.raw_pdf_path.split("/")[-1]
    pdf_path_short.short_description = "PDF"


COUNTRY_PREFIXES = {
    "MD": ("Moldova", ["+373", "373"]),
    "RO": ("Romania", ["+40", "40"]),
    "UA": ("Ukraine", ["+380", "380"]),
    "BG": ("Bulgaria", ["+359", "359"]),
    "PL": ("Poland", ["+48", "48"]),
    "IT": ("Italy", ["+39", "39"]),
    "ES": ("Spain", ["+34", "34"]),
    "DE": ("Germany", ["+49", "49"]),
}


@lru_cache(maxsize=1)
def _country_prefixes():
    try:
        rows = FitexpressCountry.objects.filter(active=True).exclude(iso2__isnull=True).order_by("country_name")
        values = {}
        for country in rows:
            code = str(country.iso2 or "").upper()
            if not code:
                continue
            prefixes = country.phone_prefixes or []
            if code not in values or prefixes:
                values[code] = (country.country_name, prefixes)
        return values or COUNTRY_PREFIXES
    except Exception:
        return COUNTRY_PREFIXES


@lru_cache(maxsize=1)
def _country_records():
    try:
        records = list(FitexpressCountry.objects.filter(active=True).order_by("country_name", "country_id"))
        return records
    except Exception:
        return []


def _country_options():
    records = _country_records()
    if records:
        return [(str(country.country_id), country.country_name) for country in records] + [("unknown", "Unknown")]
    return [(code, label) for code, (label, _) in COUNTRY_PREFIXES.items()] + [("unknown", "Unknown")]


def _country_record_for_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    records = _country_records()
    for country in records:
        if value == str(country.country_id):
            return country
        if country.iso2 and value.upper() == country.iso2.upper():
            return country
    return None


def _phone_country(phone):
    value = str(phone or "").strip().replace(" ", "")
    digits = "".join(ch for ch in value if ch.isdigit())
    records = _country_records()
    if records:
        countries = [
            (str(country.country_id), country.country_name, country.phone_prefixes or [])
            for country in records
        ]
    else:
        countries = [
            (code, label, prefixes)
            for code, (label, prefixes) in _country_prefixes().items()
        ]
    for code, label, prefixes in countries:
        for prefix in prefixes:
            normalized = prefix.replace("+", "")
            if value.startswith(prefix) or digits.startswith(normalized):
                return code, label
    return "unknown", "Unknown"


def _profile_country(profile):
    metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
    fitexpress_country_id = metadata.get("fitexpress_country_id")
    if fitexpress_country_id:
        country = _country_record_for_value(fitexpress_country_id)
        if country:
            return str(country.country_id), country.country_name
    code = str(metadata.get("country_code") or "").strip()
    name = str(metadata.get("country_name") or "").strip()
    if code and name and code != "unknown":
        return code, name
    return _phone_country(profile.phone)


def _filter_values(request, name):
    values = []
    for raw in request.GET.getlist(name):
        values.extend(str(raw or "").split(","))
    return [value.strip() for value in values if value and value.strip()]


def _country_q(code):
    query = Q()
    country = _country_record_for_value(code)
    if country:
        query |= Q(metadata__fitexpress_country_id=country.country_id)
        query |= Q(metadata__country_code=str(country.iso2 or "").upper())
        query |= Q(metadata__country_name=country.country_name)
        prefixes = country.phone_prefixes or []
    else:
        prefixes = _country_prefixes().get(code, ("", []))[1]
        query |= Q(metadata__country_code=code)
    for prefix in prefixes:
        normalized = prefix.replace("+", "")
        query |= Q(phone__startswith=prefix) | Q(phone__startswith=normalized)
    return query


def _known_country_q():
    query = Q()
    for value, _label in _country_options():
        if value != "unknown":
            query |= _country_q(value)
    return query


@lru_cache(maxsize=1)
def _product_label_map():
    mappings = FitexpressProductMapping.objects.filter(active=True).order_by("product_name", "fitexpress_product_id")
    labels = {}
    for mapping in mappings:
        label = mapping.product_name or mapping.fitexpress_product_id or mapping.product_id
        if mapping.fitexpress_product_id:
            labels[str(mapping.fitexpress_product_id)] = label
        if mapping.product_id:
            labels[str(mapping.product_id)] = label
    return labels


def _product_filter_values(values):
    values = [str(value) for value in values if str(value or "").strip()]
    if not values:
        return []
    mappings = FitexpressProductMapping.objects.filter(
        Q(product_id__in=values) | Q(fitexpress_product_id__in=values)
    )
    expanded = set(values)
    for mapping in mappings:
        if mapping.product_id:
            expanded.add(str(mapping.product_id))
        if mapping.fitexpress_product_id:
            expanded.add(str(mapping.fitexpress_product_id))
    return list(expanded)


@lru_cache(maxsize=1)
def _product_options():
    labels = _product_label_map()
    return sorted(
        labels.items(),
        key=lambda item: (str(item[1]).lower(), str(item[0])),
    )


class CustomerCountryFilter(admin.SimpleListFilter):
    title = "country"
    parameter_name = "country"

    def lookups(self, request, model_admin):
        return _country_options()

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        known = _known_country_q()
        query = Q()
        for value in values:
            if value == "unknown":
                query |= ~known
            else:
                query |= _country_q(value)
        return queryset.filter(query)


class CustomerProductFilter(admin.SimpleListFilter):
    title = "product"
    parameter_name = "crm_product"

    def lookups(self, request, model_admin):
        return _product_options()

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        values = _product_filter_values(values)
        order_ids = CustomerOrder.objects.filter(product_id__in=values).values_list("customer_id", flat=True)
        conversion_ids = CustomerConversionEvent.objects.filter(product_id__in=values).values_list("customer_id", flat=True)
        return queryset.filter(
            Q(last_product_detected__in=values)
            | Q(customer_id__in=order_ids)
            | Q(customer_id__in=conversion_ids)
        )


class CustomerChannelFilter(admin.SimpleListFilter):
    title = "channel"
    parameter_name = "crm_channel"

    def lookups(self, request, model_admin):
        return CustomerChannelIdentity.CHANNEL_CHOICES

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        ids = CustomerChannelIdentity.objects.filter(channel__in=values).values_list("customer_id", flat=True)
        return queryset.filter(customer_id__in=ids)


class CustomerStageFilter(admin.SimpleListFilter):
    title = "CRM stage"
    parameter_name = "crm_stage"

    def lookups(self, request, model_admin):
        return (
            ("buyer", "Buyer"),
            ("order_submitted", "Order submitted"),
            ("engaged", "Engaged / replied"),
            ("lead_only", "Lead only"),
            ("no_activity", "No CRM activity"),
        )

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        buyer_ids = CustomerConversionEvent.objects.filter(event_type="buy").values_list("customer_id", flat=True)
        order_ids = CustomerOrder.objects.exclude(status__in=["failed", "cancelled"]).values_list("customer_id", flat=True)
        replied_ids = CustomerConversionEvent.objects.filter(event_type="replied").values_list("customer_id", flat=True)
        event_ids = CustomerCommunicationEvent.objects.values_list("customer_id", flat=True)
        query = Q()
        for value in values:
            if value == "buyer":
                query |= Q(customer_id__in=buyer_ids)
            elif value == "order_submitted":
                query |= Q(customer_id__in=order_ids) & ~Q(customer_id__in=buyer_ids)
            elif value == "engaged":
                query |= Q(customer_id__in=replied_ids) & ~Q(customer_id__in=order_ids)
            elif value == "lead_only":
                query |= Q(customer_id__in=event_ids) & ~Q(customer_id__in=replied_ids) & ~Q(customer_id__in=order_ids)
            elif value == "no_activity":
                query |= ~Q(customer_id__in=event_ids)
        return queryset.filter(query) if query else queryset


class CustomerSegmentFilter(admin.SimpleListFilter):
    title = "segment"
    parameter_name = "crm_segment"

    def lookups(self, request, model_admin):
        segments = (
            CustomerSegment.objects.exclude(status="archived")
            .order_by("audience_type", "name")[:120]
        )
        return [
            (
                str(segment.segment_id),
                f"{segment.name} ({segment.audience_type}, {segment.profile_count})",
            )
            for segment in segments
        ]

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        ids = CustomerSegmentMembership.objects.filter(
            segment_id__in=values,
            status="active",
        ).values_list("customer_id", flat=True)
        return queryset.filter(customer_id__in=ids)


class CustomerActivityFilter(admin.SimpleListFilter):
    title = "activity"
    parameter_name = "crm_activity"

    def lookups(self, request, model_admin):
        return (
            ("has_order", "Has order"),
            ("no_order", "No order"),
            ("has_reply", "Has reply"),
            ("no_reply", "No reply"),
            ("has_email", "Has email"),
            ("has_phone", "Has phone"),
        )

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        order_ids = CustomerOrder.objects.exclude(status__in=["failed", "cancelled"]).values_list("customer_id", flat=True)
        reply_ids = CustomerConversionEvent.objects.filter(event_type="replied").values_list("customer_id", flat=True)
        query = Q()
        for value in values:
            if value == "has_order":
                query |= Q(customer_id__in=order_ids)
            elif value == "no_order":
                query |= ~Q(customer_id__in=order_ids)
            elif value == "has_reply":
                query |= Q(customer_id__in=reply_ids)
            elif value == "no_reply":
                query |= ~Q(customer_id__in=reply_ids)
            elif value == "has_email":
                query |= Q(email__isnull=False) & ~Q(email="")
            elif value == "has_phone":
                query |= Q(phone__isnull=False) & ~Q(phone="")
        return queryset.filter(query) if query else queryset


class CustomerRecencyFilter(admin.SimpleListFilter):
    title = "last seen"
    parameter_name = "crm_recency"

    def lookups(self, request, model_admin):
        return (
            ("7d", "Last 7 days"),
            ("30d", "Last 30 days"),
            ("90d", "Last 90 days"),
            ("stale90", "Older than 90 days"),
            ("missing", "No last seen"),
        )

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        now = timezone.now()
        query = Q()
        for value in values:
            if value == "7d":
                query |= Q(last_seen_at__gte=now - timedelta(days=7))
            elif value == "30d":
                query |= Q(last_seen_at__gte=now - timedelta(days=30))
            elif value == "90d":
                query |= Q(last_seen_at__gte=now - timedelta(days=90))
            elif value == "stale90":
                query |= Q(last_seen_at__lt=now - timedelta(days=90))
            elif value == "missing":
                query |= Q(last_seen_at__isnull=True)
        return queryset.filter(query) if query else queryset


class CustomerStatusFilter(admin.SimpleListFilter):
    title = "profile status"
    parameter_name = "crm_status"

    def lookups(self, request, model_admin):
        return _profile_status_options()

    def queryset(self, request, queryset):
        values = _filter_values(request, self.parameter_name)
        if not values:
            return queryset
        return queryset.filter(status__in=values)


def _apply_customer_stage(queryset, stage):
    if not stage:
        return queryset
    buyer_ids = CustomerConversionEvent.objects.filter(event_type="buy").values_list("customer_id", flat=True)
    order_ids = CustomerOrder.objects.exclude(status__in=["failed", "cancelled"]).values_list("customer_id", flat=True)
    replied_ids = CustomerConversionEvent.objects.filter(event_type="replied").values_list("customer_id", flat=True)
    event_ids = CustomerCommunicationEvent.objects.values_list("customer_id", flat=True)
    if stage == "buyer":
        return queryset.filter(customer_id__in=buyer_ids)
    if stage == "order_submitted":
        return queryset.filter(customer_id__in=order_ids).exclude(customer_id__in=buyer_ids)
    if stage == "engaged":
        return queryset.filter(customer_id__in=replied_ids).exclude(customer_id__in=order_ids)
    if stage == "lead_only":
        return queryset.filter(customer_id__in=event_ids).exclude(customer_id__in=replied_ids).exclude(customer_id__in=order_ids)
    if stage == "no_activity":
        return queryset.exclude(customer_id__in=event_ids)
    return queryset


def _profiles_for_segment(segment):
    queryset = CustomerProfile.objects.all()
    if segment.country:
        if segment.country == "unknown":
            queryset = queryset.exclude(_known_country_q())
        else:
            queryset = queryset.filter(_country_q(segment.country))
    if segment.channel:
        ids = CustomerChannelIdentity.objects.filter(channel=segment.channel).values_list("customer_id", flat=True)
        queryset = queryset.filter(customer_id__in=ids)
    if segment.product_id:
        products = _product_filter_values([segment.product_id])
        order_ids = CustomerOrder.objects.filter(product_id__in=products).values_list("customer_id", flat=True)
        conversion_ids = CustomerConversionEvent.objects.filter(product_id__in=products).values_list("customer_id", flat=True)
        queryset = queryset.filter(
            Q(last_product_detected__in=products)
            | Q(customer_id__in=order_ids)
            | Q(customer_id__in=conversion_ids)
        )
    if segment.crm_stage:
        queryset = _apply_customer_stage(queryset, segment.crm_stage)
    if segment.profile_status:
        queryset = queryset.filter(status=segment.profile_status)

    rules = segment.rules if isinstance(segment.rules, dict) else {}
    if rules.get("has_email") is True:
        queryset = queryset.exclude(email__isnull=True).exclude(email="")
    if rules.get("has_email") is False:
        queryset = queryset.filter(Q(email__isnull=True) | Q(email=""))
    if rules.get("has_phone") is True:
        queryset = queryset.exclude(phone__isnull=True).exclude(phone="")
    if rules.get("has_phone") is False:
        queryset = queryset.filter(Q(phone__isnull=True) | Q(phone=""))
    if rules.get("min_total_messages"):
        queryset = queryset.filter(total_messages__gte=int(rules["min_total_messages"]))
    if rules.get("min_total_conversations"):
        queryset = queryset.filter(total_conversations__gte=int(rules["min_total_conversations"]))
    if rules.get("last_seen_days"):
        queryset = queryset.filter(last_seen_at__gte=timezone.now() - timedelta(days=int(rules["last_seen_days"])))
    return queryset.distinct()


def _refresh_segment_count(segment):
    count = CustomerSegmentMembership.objects.filter(segment=segment, status="active").count()
    segment.profile_count = count
    segment.updated_at = timezone.now()
    segment.save(update_fields=["profile_count", "updated_at"])
    return count


def _rebuild_dynamic_segment(segment, actor):
    profiles = list(_profiles_for_segment(segment).values_list("customer_id", flat=True))
    CustomerSegmentMembership.objects.filter(segment=segment).delete()
    CustomerSegmentMembership.objects.bulk_create(
        [
            CustomerSegmentMembership(
                segment=segment,
                customer_id=customer_id,
                source="dynamic_rule",
                status="active",
                added_by=actor,
            )
            for customer_id in profiles
        ],
        ignore_conflicts=True,
        batch_size=1000,
    )
    segment.profile_count = len(profiles)
    segment.last_built_at = timezone.now()
    segment.updated_at = timezone.now()
    segment.save(update_fields=["profile_count", "last_built_at", "updated_at"])
    return len(profiles)


def _create_segment_from_profiles(request, queryset, audience_type):
    from django.urls import reverse
    from django.utils.text import slugify

    now = timezone.now()
    label = "Marketing" if audience_type == "marketing" else "Transactional"
    base_name = f"{label} list {now:%Y-%m-%d %H:%M}"
    slug = slugify(f"{label}-{now:%Y%m%d-%H%M%S}")
    segment = CustomerSegment.objects.create(
        name=base_name,
        slug=slug,
        audience_type=audience_type,
        status="draft",
        is_dynamic=False,
        created_by=str(request.user),
        rules={
            "source": "admin_selected_profiles",
            "selected_count": queryset.count(),
        },
    )
    memberships = [
        CustomerSegmentMembership(
            segment=segment,
            customer_id=customer_id,
            source="filtered_selection",
            status="active",
            added_by=str(request.user),
            metadata={"created_from": "customer_profile_admin_action"},
        )
        for customer_id in queryset.values_list("customer_id", flat=True)
    ]
    CustomerSegmentMembership.objects.bulk_create(memberships, ignore_conflicts=True, batch_size=1000)
    count = _refresh_segment_count(segment)
    url = reverse("admin:superchatsync_customersegment_change", args=[segment.segment_id])
    messages.success(
        request,
        format_html(
            "{} segment creat cu {} profile. Deschide segmentul: <a href='{}'>{}</a>",
            label,
            count,
            url,
            segment.name,
        ),
    )


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    change_list_template = "admin/superchatsync/customerprofile/change_list.html"
    paginator = FastAdminPaginator
    show_full_result_count = False
    list_per_page = 25
    list_display = (
        "display_name",
        "phone",
        "country",
        "email",
        "channels",
        "segments",
        "last_product",
        "crm_stage",
        "orders_count",
        "revenue_total",
        "total_conversations",
        "total_messages",
        "last_seen_at",
        "status",
    )
    search_fields = (
        "profile_key",
        "display_name",
        "phone",
        "email",
        "last_product_detected",
        "last_conversation_id",
    )
    list_filter = (
        CustomerCountryFilter,
        CustomerProductFilter,
        CustomerChannelFilter,
        CustomerSegmentFilter,
        CustomerStageFilter,
        CustomerActivityFilter,
        CustomerRecencyFilter,
        CustomerStatusFilter,
    )
    readonly_fields = (
        "crm_overview",
        "customer_id",
        "profile_key",
        "display_name",
        "phone",
        "email",
        "first_seen_at",
        "last_seen_at",
        "total_conversations",
        "total_messages",
        "last_product_detected",
        "last_conversation_id",
        "status",
        "metadata",
        "created_at",
        "updated_at",
    )
    actions = (
        "create_marketing_segment",
        "create_transactional_segment",
        "refresh_customer_profiles",
    )
    ordering = ("-last_seen_at",)
    fieldsets = (
        ("CRM 360", {"fields": ("crm_overview",)}),
        (
            "Identity",
            {
                "fields": (
                    "customer_id",
                    "profile_key",
                    "display_name",
                    "phone",
                    "email",
                    "status",
                )
            },
        ),
        (
            "Activity",
            {
                "fields": (
                    "first_seen_at",
                    "last_seen_at",
                    "total_conversations",
                    "total_messages",
                    "last_product_detected",
                    "last_conversation_id",
                )
            },
        ),
        ("Metadata", {"classes": ("collapse",), "fields": ("metadata", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        order_counts = (
            CustomerOrder.objects.filter(customer_id=OuterRef("customer_id"))
            .values("customer_id")
            .annotate(total=Count("order_id"))
            .values("total")[:1]
        )
        revenue_totals = (
            CustomerOrder.objects.filter(customer_id=OuterRef("customer_id"))
            .exclude(status__in=["failed", "cancelled", "returned"])
            .values("customer_id")
            .annotate(total=Sum("cost_eur_estimate"))
            .values("total")[:1]
        )
        active_orders = CustomerOrder.objects.filter(customer_id=OuterRef("customer_id")).exclude(
            status__in=["failed", "cancelled"]
        )
        return queryset.annotate(
            _orders_count=Subquery(order_counts),
            _revenue_total=Subquery(revenue_totals),
            _has_buy=Exists(
                CustomerConversionEvent.objects.filter(
                    customer_id=OuterRef("customer_id"),
                    event_type="buy",
                )
            ),
            _has_active_order=Exists(active_orders),
            _has_reply=Exists(
                CustomerConversionEvent.objects.filter(
                    customer_id=OuterRef("customer_id"),
                    event_type="replied",
                )
            ),
            _has_event=Exists(
                CustomerCommunicationEvent.objects.filter(customer_id=OuterRef("customer_id"))
            ),
        )

    def changelist_view(self, request, extra_context=None):
        segment_values = list(
            CustomerSegment.objects.exclude(status="archived")
            .order_by("audience_type", "name")[:160]
        )
        status_values = _profile_status_options()

        filter_values = {
            "q": request.GET.get("q", ""),
            "country": _filter_values(request, "country"),
            "crm_product": _filter_values(request, "crm_product"),
            "crm_channel": _filter_values(request, "crm_channel"),
            "crm_segment": _filter_values(request, "crm_segment"),
            "crm_stage": _filter_values(request, "crm_stage"),
            "crm_activity": _filter_values(request, "crm_activity"),
            "crm_recency": _filter_values(request, "crm_recency"),
            "crm_status": _filter_values(request, "crm_status"),
            "last_seen_from": request.GET.get("last_seen_at__date__gte", ""),
            "last_seen_to": request.GET.get("last_seen_at__date__lte", ""),
            "first_seen_from": request.GET.get("first_seen_at__date__gte", ""),
            "first_seen_to": request.GET.get("first_seen_at__date__lte", ""),
        }
        countries = _country_options()
        products = _product_options()
        channels = CustomerChannelIdentity.CHANNEL_CHOICES
        segments = [(str(segment.segment_id), segment.name) for segment in segment_values]
        stages = [
            ("buyer", "Buyer"),
            ("order_submitted", "Order submitted"),
            ("engaged", "Engaged / replied"),
            ("lead_only", "Lead only"),
            ("no_activity", "No CRM activity"),
        ]
        activities = [
            ("has_order", "Has order"),
            ("no_order", "No order"),
            ("has_reply", "Has reply"),
            ("no_reply", "No reply"),
            ("has_email", "Has email"),
            ("has_phone", "Has phone"),
        ]
        recencies = [
            ("7d", "Last 7 days"),
            ("30d", "Last 30 days"),
            ("90d", "Last 90 days"),
            ("stale90", "Older than 90 days"),
            ("missing", "Missing last seen"),
        ]
        statuses = list(status_values)

        def build_url(params=None):
            params = params or {}
            query = urlencode(params, doseq=True)
            return f"{request.path}?{query}" if query else request.path

        known_country = _known_country_q()
        profile_total = CustomerProfile.objects.count()
        order_total_metric = CustomerOrder.objects.count()
        active_order_profile_count = _bounded_queryset_count(
            CustomerOrder.objects.exclude(status__in=["failed", "cancelled"])
            .exclude(customer_id__isnull=True)
            .values("customer_id")
            .distinct(),
            timeout_ms=700,
            fallback=None,
        )
        buyer_count = _bounded_queryset_count(
            CustomerConversionEvent.objects.filter(event_type="buy")
            .values("customer_id")
            .distinct(),
            timeout_ms=700,
            fallback=None,
        )
        replied_count = _bounded_queryset_count(
            CustomerConversionEvent.objects.filter(event_type="replied")
            .values("customer_id")
            .distinct(),
            timeout_ms=700,
            fallback=None,
        )
        with_phone_count = _bounded_queryset_count(
            CustomerProfile.objects.exclude(phone__isnull=True).exclude(phone=""),
            timeout_ms=700,
            fallback=None,
        )
        unknown_country_count = _bounded_queryset_count(
            CustomerProfile.objects.exclude(known_country),
            timeout_ms=700,
            fallback=None,
        )

        metrics = [
            {
                "label": "Profiles",
                "value": _format_count(profile_total),
                "href": build_url(),
            },
            {
                "label": "Orders",
                "value": _format_count(order_total_metric),
                "href": "/admin/superchatsync/customerorder/",
            },
            {
                "label": "Profiles with orders",
                "value": _format_count(active_order_profile_count),
                "href": build_url({"crm_stage": "order_submitted"}),
            },
            {
                "label": "Buyers",
                "value": _format_count(buyer_count),
                "href": build_url({"crm_stage": "buyer"}),
            },
            {
                "label": "Engaged",
                "value": _format_count(replied_count),
                "href": build_url({"crm_stage": "engaged"}),
            },
            {
                "label": "With phone",
                "value": _format_count(with_phone_count),
                "href": build_url({"crm_activity": "has_phone"}),
            },
            {
                "label": "Unknown country",
                "value": _format_count(unknown_country_count),
                "href": build_url({"country": "unknown"}),
            },
        ]

        quick_filters = [
            {
                "label": "All profiles",
                "href": build_url(),
                "active": not any(
                    value for key, value in filter_values.items()
                    if key != "q"
                ) and not filter_values["q"],
            },
            {
                "label": "Buyers",
                "href": build_url({"crm_stage": "buyer"}),
                "active": "buyer" in filter_values["crm_stage"],
            },
            {
                "label": "Order submitted",
                "href": build_url({"crm_stage": "order_submitted"}),
                "active": "order_submitted" in filter_values["crm_stage"],
            },
            {
                "label": "Engaged",
                "href": build_url({"crm_stage": "engaged"}),
                "active": "engaged" in filter_values["crm_stage"],
            },
            {
                "label": "No order",
                "href": build_url({"crm_activity": "no_order"}),
                "active": "no_order" in filter_values["crm_activity"],
            },
            {
                "label": "Unknown country",
                "href": build_url({"country": "unknown"}),
                "active": "unknown" in filter_values["country"],
            },
        ]

        option_maps = {
            "country": dict(countries),
            "crm_product": dict(products),
            "crm_channel": dict(channels),
            "crm_segment": dict(segments),
            "crm_stage": dict(stages),
            "crm_activity": dict(activities),
            "crm_recency": dict(recencies),
            "crm_status": dict(statuses),
        }
        active_filters = []
        if filter_values["q"]:
            active_filters.append(f"Search: {filter_values['q']}")
        for key, labels in option_maps.items():
            for value in filter_values[key]:
                active_filters.append(labels.get(value, value))
        if filter_values["last_seen_from"]:
            active_filters.append(f"Last seen from {filter_values['last_seen_from']}")
        if filter_values["last_seen_to"]:
            active_filters.append(f"Last seen to {filter_values['last_seen_to']}")
        if filter_values["first_seen_from"]:
            active_filters.append(f"First seen from {filter_values['first_seen_from']}")
        if filter_values["first_seen_to"]:
            active_filters.append(f"First seen to {filter_values['first_seen_to']}")

        extra_context = extra_context or {}
        extra_context["crm_filter_options"] = {
            "countries": countries,
            "products": products,
            "channels": channels,
            "segments": segments,
            "stages": stages,
            "activities": activities,
            "recencies": recencies,
            "statuses": statuses,
        }
        extra_context["crm_filter_values"] = filter_values
        extra_context["crm_filter_groups"] = [
            {
                "title": "Country",
                "name": "country",
                "options": countries,
                "selected": filter_values["country"],
                "searchable": False,
            },
            {
                "title": "Product",
                "name": "crm_product",
                "options": products,
                "selected": filter_values["crm_product"],
                "searchable": True,
            },
            {
                "title": "Channel",
                "name": "crm_channel",
                "options": channels,
                "selected": filter_values["crm_channel"],
                "searchable": False,
            },
            {
                "title": "Segment",
                "name": "crm_segment",
                "options": segments,
                "selected": filter_values["crm_segment"],
                "searchable": True,
            },
            {
                "title": "Stage",
                "name": "crm_stage",
                "options": stages,
                "selected": filter_values["crm_stage"],
                "searchable": False,
            },
            {
                "title": "Activity",
                "name": "crm_activity",
                "options": activities,
                "selected": filter_values["crm_activity"],
                "searchable": False,
            },
            {
                "title": "Profile status",
                "name": "crm_status",
                "options": statuses,
                "selected": filter_values["crm_status"],
                "searchable": False,
            },
        ]
        extra_context["crm_clear_filters_url"] = request.path
        extra_context["crm_metrics"] = metrics
        extra_context["crm_quick_filters"] = quick_filters
        extra_context["crm_active_filters"] = active_filters
        extra_context["crm_segment_admin_url"] = "/admin/superchatsync/customersegment/"
        extra_context["crm_orders_admin_url"] = "/admin/superchatsync/customerorder/"
        extra_context["crm_events_admin_url"] = "/admin/superchatsync/customercommunicationevent/"
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description="Country")
    def country(self, obj):
        code, label = _profile_country(obj)
        return f"{label} ({code})" if code != "unknown" else label

    @admin.display(description="Product")
    def last_product(self, obj):
        value = obj.last_product_detected
        if not value:
            return "-"
        label = _product_label_map().get(str(value))
        return label or str(value)

    @admin.display(description="Channels")
    def channels(self, obj):
        values = list(
            CustomerChannelIdentity.objects.filter(customer_id=obj.customer_id)
            .order_by("channel")
            .values_list("channel", flat=True)
            .distinct()
        )
        return ", ".join(values) or "-"

    @admin.display(description="Segments")
    def segments(self, obj):
        values = list(
            CustomerSegment.objects.filter(
                memberships__customer_id=obj.customer_id,
                memberships__status="active",
            )
            .order_by("audience_type", "name")
            .values_list("name", flat=True)
            .distinct()[:3]
        )
        suffix = ""
        total = CustomerSegmentMembership.objects.filter(customer_id=obj.customer_id, status="active").count()
        if total > len(values):
            suffix = f" +{total - len(values)}"
        return ", ".join(values) + suffix if values else "-"

    @admin.display(description="Orders")
    def orders_count(self, obj):
        if hasattr(obj, "_orders_count"):
            return obj._orders_count or 0
        return CustomerOrder.objects.filter(customer_id=obj.customer_id).count()

    @admin.display(description="Revenue")
    def revenue_total(self, obj):
        if hasattr(obj, "_revenue_total"):
            return f"{obj._revenue_total or 0} EUR"
        total = (
            CustomerOrder.objects.filter(customer_id=obj.customer_id)
            .exclude(status__in=["failed", "cancelled", "returned"])
            .aggregate(total=Sum("cost_eur_estimate"))
            .get("total")
        )
        return f"{total or 0} EUR"

    @admin.display(description="Stage")
    def crm_stage(self, obj):
        if hasattr(obj, "_has_buy"):
            if obj._has_buy:
                return "Buyer"
            if obj._has_active_order:
                return "Order submitted"
            if obj._has_reply:
                return "Engaged"
            if obj._has_event:
                return "Lead"
            return "No activity"
        if CustomerConversionEvent.objects.filter(customer_id=obj.customer_id, event_type="buy").exists():
            return "Buyer"
        if CustomerOrder.objects.filter(customer_id=obj.customer_id).exclude(status__in=["failed", "cancelled"]).exists():
            return "Order submitted"
        if CustomerConversionEvent.objects.filter(customer_id=obj.customer_id, event_type="replied").exists():
            return "Engaged"
        if CustomerCommunicationEvent.objects.filter(customer_id=obj.customer_id).exists():
            return "Lead"
        return "No activity"

    def crm_overview(self, obj):
        from collections import Counter
        from django.utils.safestring import mark_safe
        from django.utils.html import escape

        channel_labels = dict(CustomerChannelIdentity.CHANNEL_CHOICES)
        profile_meta = obj.metadata if isinstance(obj.metadata, dict) else {}
        identities = list(
            CustomerChannelIdentity.objects.filter(customer_id=obj.customer_id)
            .order_by("channel", "-is_primary", "-last_seen_at")[:24]
        )
        events = list(
            CustomerCommunicationEvent.objects.filter(customer_id=obj.customer_id)
            .order_by("-occurred_at", "-created_at")[:30]
        )
        orders = list(
            CustomerOrder.objects.filter(customer_id=obj.customer_id)
            .order_by("-submitted_at")[:14]
        )
        conversions = list(
            CustomerConversionEvent.objects.filter(customer_id=obj.customer_id)
            .order_by("-occurred_at")[:30]
        )
        attribution_conversions = list(
            CustomerConversionEvent.objects.filter(customer_id=obj.customer_id)
            .exclude(event_type__in=["sent", "delivered", "opened", "read"])
            .order_by("-occurred_at")[:80]
        )

        order_total = (
            CustomerOrder.objects.filter(customer_id=obj.customer_id)
            .exclude(status__in=["failed", "cancelled", "returned"])
            .aggregate(total=Sum("cost_eur_estimate"))
            .get("total")
            or 0
        )
        active_orders_count = CustomerOrder.objects.filter(customer_id=obj.customer_id).exclude(
            status__in=["failed", "cancelled", "returned"]
        ).count()
        event_count = CustomerCommunicationEvent.objects.filter(customer_id=obj.customer_id).count()
        conversion_count = CustomerConversionEvent.objects.filter(customer_id=obj.customer_id).count()
        country_code, country_label = _profile_country(obj)
        country_value = f"{country_label} ({country_code})" if country_code != "unknown" else country_label
        primary_channels = ", ".join(
            channel_labels.get(identity.channel, identity.channel)
            for identity in identities
            if identity.is_primary
        )
        if not primary_channels:
            primary_channels = ", ".join(
                channel_labels.get(channel, channel)
                for channel in sorted({identity.channel for identity in identities})
            ) or "-"

        product_ids = {
            item
            for item in (
                [order.product_id for order in orders]
                + [conversion.product_id for conversion in conversions]
                + [obj.last_product_detected]
            )
            if item
        }
        product_names = {
            mapping.product_id: mapping.product_name
            for mapping in FitexpressProductMapping.objects.filter(product_id__in=product_ids)
        }
        product_names.update(
            {
                mapping.fitexpress_product_id: mapping.product_name
                for mapping in FitexpressProductMapping.objects.filter(fitexpress_product_id__in=product_ids)
            }
        )

        def fmt_dt(value):
            if not value:
                return "-"
            return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")

        def channel_label(value):
            return channel_labels.get(value, value or "-")

        def product_label(product_id):
            if not product_id:
                return "-"
            name = product_names.get(product_id)
            return f"{product_id} | {name}" if name and name != product_id else str(product_id)

        def preview(value, limit=160):
            text = " ".join(str(value or "").split())
            if len(text) <= limit:
                return text or "-"
            return text[: limit - 1] + "..."

        def admin_link(label, href):
            return f"<a href='{escape(href)}'>{escape(label)}</a>" if href else escape(label)

        def conversation_link(conversation_id):
            if not conversation_id:
                return "-"
            short = conversation_id[:18] + "..." if len(conversation_id) > 18 else conversation_id
            return admin_link(short, f"/admin/superchatsync/conversation/{conversation_id}/change/")

        def filter_link(label, path, params):
            query = urlencode(params, doseq=True)
            return admin_link(label, f"{path}?{query}")

        def source_from_payload(payload):
            if not isinstance(payload, dict):
                return "-"
            for key in ("referral", "lead_source", "source", "utm_source", "campaign", "campaign_name"):
                value = payload.get(key)
                if value:
                    return str(value)
            return "-"

        def order_qualified_via(order):
            order_time = order.submitted_at
            for conversion in attribution_conversions:
                if order_time and conversion.occurred_at and conversion.occurred_at > order_time:
                    continue
                if order.product_id and conversion.product_id and conversion.product_id != order.product_id:
                    continue
                campaign = conversion.campaign_id or "-"
                return (
                    f"{channel_label(conversion.channel)} / {conversion.event_type}"
                    + (f" / campaign {campaign}" if campaign != "-" else "")
                )
            if order.source_channel:
                return f"{channel_label(order.source_channel)} / order intent"
            return "-"

        def order_source(order):
            payload_source = source_from_payload(order.order_payload)
            parts = []
            if payload_source != "-":
                parts.append(payload_source)
            if order.source_channel:
                parts.append(channel_label(order.source_channel))
            if order.webhook_url:
                parts.append("webhook")
            return " / ".join(parts) if parts else "-"

        def order_identifier(order):
            return order.external_order_id or str(order.order_id)

        def order_db_status(order):
            return order.external_status or "-"

        channel_counts = Counter(event.channel for event in events if event.channel)
        order_channel_counts = Counter(order.source_channel for order in orders if order.source_channel)
        conversion_counts = Counter(conversion.event_type for conversion in conversions if conversion.event_type)
        segment_values = list(
            CustomerSegment.objects.filter(
                memberships__customer_id=obj.customer_id,
                memberships__status="active",
            )
            .order_by("audience_type", "name")
            .values_list("name", "audience_type")[:12]
        )
        segment_html = "".join(
            f"<span class='crm360-pill'>{escape(name)} <em>{escape(audience)}</em></span>"
            for name, audience in segment_values
        ) or "<span class='crm360-muted'>No segments</span>"

        cards = [
            ("Country", country_value),
            ("Stage", self.crm_stage(obj)),
            ("Channels", str(len({identity.channel for identity in identities}))),
            ("Events", str(event_count)),
            ("Active orders", str(active_orders_count)),
            ("Revenue", f"{order_total} EUR"),
        ]
        card_html = "".join(
            f"<div class='crm360-card'>"
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            f"</div>"
            for label, value in cards
        )

        identity_rows = "".join(
            f"<tr><td><span class='crm360-channel'>{escape(channel_label(identity.channel))}</span></td>"
            f"<td>{escape(identity.identifier)}</td>"
            f"<td>{escape(identity.provider or '-')}</td>"
            f"<td>{escape(identity.status or '-')}</td>"
            f"<td>{'Primary' if identity.is_primary else '-'}</td>"
            f"<td>{escape(fmt_dt(identity.last_seen_at))}</td></tr>"
            for identity in identities
        ) or "<tr><td colspan='6' class='crm360-empty'>No channel identities yet</td></tr>"

        event_rows = "".join(
            f"<tr><td>{escape(fmt_dt(event.occurred_at))}</td>"
            f"<td><span class='crm360-channel'>{escape(channel_label(event.channel))}</span></td>"
            f"<td>{escape(event.direction or '-')}</td>"
            f"<td>{escape(event.event_type or '-')}</td>"
            f"<td>{escape(event.status or '-')}</td>"
            f"<td>{escape(event.campaign_id or event.workflow_id or '-')}</td>"
            f"<td>{conversation_link(event.conversation_id)}</td>"
            f"<td>{escape(preview(event.body_preview, 180))}</td></tr>"
            for event in events
        ) or "<tr><td colspan='8' class='crm360-empty'>No communication events yet</td></tr>"

        order_rows = "".join(
            f"<tr><td>{escape(fmt_dt(order.submitted_at))}</td>"
            f"<td>{escape(order_identifier(order))}</td>"
            f"<td>{escape(product_label(order.product_id))}</td>"
            f"<td>{order.quantity}</td>"
            f"<td>{order.cost} {escape(order.currency)}</td>"
            f"<td>{escape(str(order.cost_eur_estimate or '-'))} EUR</td>"
            f"<td>{escape(order.status or '-')}</td>"
            f"<td>{escape(order_db_status(order))}</td>"
            f"<td>{escape(order_source(order))}</td>"
            f"<td>{escape(order_qualified_via(order))}</td>"
            f"<td>{conversation_link(order.source_conversation_id)}</td>"
            f"<td>{escape(str(order.webhook_http_status or '-'))}</td></tr>"
            for order in orders
        ) or "<tr><td colspan='12' class='crm360-empty'>No orders yet</td></tr>"

        conversion_rows = "".join(
            f"<tr><td>{escape(fmt_dt(conversion.occurred_at))}</td>"
            f"<td><span class='crm360-channel'>{escape(channel_label(conversion.channel))}</span></td>"
            f"<td>{escape(conversion.event_type)}</td>"
            f"<td>{escape(product_label(conversion.product_id))}</td>"
            f"<td>{escape(conversion.campaign_id or '-')}</td>"
            f"<td>{conversation_link(conversion.conversation_id)}</td>"
            f"<td>{escape(str(conversion.value or '-'))} {escape(conversion.currency or '')}</td></tr>"
            for conversion in conversions
        ) or "<tr><td colspan='7' class='crm360-empty'>No conversion events yet</td></tr>"

        communication_mix = "".join(
            f"<span class='crm360-pill'>{escape(channel_label(channel))} <em>{count}</em></span>"
            for channel, count in channel_counts.most_common()
        ) or "<span class='crm360-muted'>No communication events</span>"
        order_mix = "".join(
            f"<span class='crm360-pill'>{escape(channel_label(channel))} <em>{count}</em></span>"
            for channel, count in order_channel_counts.most_common()
        ) or "<span class='crm360-muted'>No orders</span>"
        conversion_mix = "".join(
            f"<span class='crm360-pill'>{escape(event_type)} <em>{count}</em></span>"
            for event_type, count in conversion_counts.most_common(8)
        ) or "<span class='crm360-muted'>No conversions</span>"

        last_conversation_link = ""
        if obj.last_conversation_id:
            last_conversation_link = (
                f"<div><b>Last conversation</b>{conversation_link(obj.last_conversation_id)}</div>"
            )

        html = f"""
        <div class="crm360">
          <div class="crm360-header">
            <div>
              <h2>{escape(obj.display_name or "Customer profile")}</h2>
              <p>{escape(obj.phone or "-")} · {escape(obj.email or "-")} · {escape(country_value)}</p>
            </div>
            <div class="crm360-header__meta">
              <div><b>Status</b>{escape(obj.status or "-")}</div>
              <div><b>Primary channels</b>{escape(primary_channels)}</div>
              {last_conversation_link}
            </div>
          </div>

          <div class="crm360-cards">{card_html}</div>

          <div class="crm360-grid">
            <section class="crm360-panel">
              <h3>Customer identity</h3>
              <dl class="crm360-definition">
                <div><dt>Profile key</dt><dd>{escape(obj.profile_key)}</dd></div>
                <div><dt>First seen</dt><dd>{escape(fmt_dt(obj.first_seen_at))}</dd></div>
                <div><dt>Last seen</dt><dd>{escape(fmt_dt(obj.last_seen_at))}</dd></div>
                <div><dt>Last product</dt><dd>{escape(product_label(obj.last_product_detected))}</dd></div>
                <div><dt>Country source</dt><dd>{escape(profile_meta.get("country_source") or "phone/profile metadata")}</dd></div>
              </dl>
            </section>
            <section class="crm360-panel">
              <h3>Audience and attribution</h3>
              <div class="crm360-stack">
                <div><b>Segments</b><div>{segment_html}</div></div>
                <div><b>Communication mix</b><div>{communication_mix}</div></div>
                <div><b>Order channels</b><div>{order_mix}</div></div>
                <div><b>Conversion events</b><div>{conversion_mix}</div></div>
              </div>
            </section>
          </div>

          <section class="crm360-section">
            <div class="crm360-section__title">
              <h3>Channel identities</h3>
              {filter_link("Open identities", "/admin/superchatsync/customerchannelidentity/", {"q": str(obj.customer_id)})}
            </div>
            <div class="crm360-tablewrap">
              <table><tr><th>Channel</th><th>Identifier</th><th>Provider</th><th>Status</th><th>Primary</th><th>Last seen</th></tr>{identity_rows}</table>
            </div>
          </section>

          <section class="crm360-section">
            <div class="crm360-section__title">
              <h3>Communication timeline</h3>
              {filter_link("Open all events", "/admin/superchatsync/customercommunicationevent/", {"q": str(obj.customer_id)})}
            </div>
            <div class="crm360-tablewrap">
              <table><tr><th>Time</th><th>Channel</th><th>Direction</th><th>Type</th><th>Status</th><th>Campaign/workflow</th><th>Conversation</th><th>Preview</th></tr>{event_rows}</table>
            </div>
          </section>

          <section class="crm360-section">
            <div class="crm360-section__title">
              <h3>Order history and lead attribution</h3>
              {filter_link("Open all orders", "/admin/superchatsync/customerorder/", {"q": str(obj.customer_id)})}
            </div>
            <div class="crm360-tablewrap">
              <table><tr><th>Submitted</th><th>Order ID</th><th>Product</th><th>Qty</th><th>Amount</th><th>EUR</th><th>CRM status</th><th>DB status</th><th>Order source</th><th>Lead qualified via</th><th>Conversation</th><th>Webhook</th></tr>{order_rows}</table>
            </div>
          </section>

          <section class="crm360-section">
            <div class="crm360-section__title">
              <h3>Conversion timeline</h3>
              {filter_link("Open conversions", "/admin/superchatsync/customerconversionevent/", {"q": str(obj.customer_id)})}
            </div>
            <div class="crm360-tablewrap">
              <table><tr><th>Time</th><th>Channel</th><th>Event</th><th>Product</th><th>Campaign</th><th>Conversation</th><th>Value</th></tr>{conversion_rows}</table>
            </div>
          </section>
        </div>
        <style>
          .crm360 {{
            max-width: 1440px;
            color: var(--body-fg);
          }}
          .crm360 * {{
            box-sizing: border-box;
          }}
          .crm360-header {{
            display: grid;
            grid-template-columns: minmax(260px, 1fr) minmax(240px, 420px);
            gap: 16px;
            align-items: start;
            margin-bottom: 14px;
            padding: 14px;
            border: 1px solid var(--hairline-color);
            border-radius: 8px;
            background: var(--body-bg);
          }}
          .crm360-header h2 {{
            margin: 0;
            font-size: 20px;
            line-height: 1.2;
          }}
          .crm360-header p {{
            margin: 6px 0 0;
            color: var(--body-quiet-color);
          }}
          .crm360-header__meta {{
            display: grid;
            gap: 8px;
            font-size: 12px;
          }}
          .crm360-header__meta div {{
            display: grid;
            gap: 2px;
          }}
          .crm360-header__meta b,
          .crm360-definition dt,
          .crm360-stack b {{
            color: var(--body-quiet-color);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0;
          }}
          .crm360-cards {{
            display: grid;
            grid-template-columns: repeat(6, minmax(120px, 1fr));
            gap: 10px;
            margin-bottom: 14px;
          }}
          .crm360-card {{
            min-height: 74px;
            padding: 12px;
            border: 1px solid var(--hairline-color);
            border-radius: 8px;
            background: var(--body-bg);
          }}
          .crm360-card span {{
            display: block;
            color: var(--body-quiet-color);
            font-size: 12px;
          }}
          .crm360-card strong {{
            display: block;
            margin-top: 7px;
            font-size: 20px;
            line-height: 1.2;
          }}
          .crm360-grid {{
            display: grid;
            grid-template-columns: minmax(280px, 0.9fr) minmax(320px, 1.1fr);
            gap: 12px;
            margin-bottom: 14px;
          }}
          .crm360-panel,
          .crm360-section {{
            border: 1px solid var(--hairline-color);
            border-radius: 8px;
            background: var(--body-bg);
          }}
          .crm360-panel {{
            padding: 12px;
          }}
          .crm360 h3 {{
            margin: 0;
            font-size: 15px;
            line-height: 1.25;
          }}
          .crm360-definition {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px 14px;
            margin: 12px 0 0;
          }}
          .crm360-definition div {{
            min-width: 0;
          }}
          .crm360-definition dt,
          .crm360-definition dd {{
            margin: 0;
          }}
          .crm360-definition dd {{
            margin-top: 4px;
            overflow-wrap: anywhere;
          }}
          .crm360-stack {{
            display: grid;
            gap: 10px;
            margin-top: 12px;
          }}
          .crm360-pill,
          .crm360-channel {{
            display: inline-flex;
            min-height: 24px;
            align-items: center;
            margin: 4px 5px 0 0;
            padding: 2px 8px;
            border: 1px solid var(--hairline-color);
            border-radius: 999px;
            background: var(--darkened-bg);
            white-space: nowrap;
          }}
          .crm360-pill em {{
            margin-left: 5px;
            color: var(--body-quiet-color);
            font-style: normal;
          }}
          .crm360-channel {{
            margin: 0;
            font-weight: 700;
          }}
          .crm360-muted,
          .crm360-empty {{
            color: var(--body-quiet-color);
          }}
          .crm360-section {{
            margin-top: 12px;
            overflow: hidden;
          }}
          .crm360-section__title {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: center;
            padding: 12px;
            border-bottom: 1px solid var(--hairline-color);
          }}
          .crm360-section__title a {{
            white-space: nowrap;
          }}
          .crm360-tablewrap {{
            overflow-x: auto;
          }}
          .crm360 table {{
            width: 100%;
            min-width: 900px;
            border-collapse: collapse;
          }}
          .crm360 th,
          .crm360 td {{
            padding: 8px 10px;
            border-bottom: 1px solid var(--hairline-color);
            text-align: left;
            vertical-align: top;
          }}
          .crm360 th {{
            background: var(--darkened-bg);
            color: var(--body-quiet-color);
            font-size: 12px;
            font-weight: 700;
          }}
          .crm360 td {{
            overflow-wrap: anywhere;
          }}
          @media (max-width: 1100px) {{
            .crm360-header,
            .crm360-grid {{
              grid-template-columns: 1fr;
            }}
            .crm360-cards {{
              grid-template-columns: repeat(3, minmax(0, 1fr));
            }}
          }}
          @media (max-width: 720px) {{
            .crm360-cards,
            .crm360-definition {{
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }}
            .crm360-card strong {{
              font-size: 17px;
            }}
          }}
        </style>
        """
        return mark_safe(html)
    crm_overview.short_description = "CRM 360"

    @admin.action(description="Refresh customer profiles from conversations")
    def refresh_customer_profiles(self, request, queryset):
        import os as _customer_os
        import subprocess as _customer_subprocess
        import sys as _customer_sys

        log_dir = "/opt/superchat-ai-agent/logs"
        _customer_os.makedirs(log_dir, exist_ok=True)
        log_path = _customer_os.path.join(log_dir, "sync_customer_profiles.log")

        cmd = [
            _customer_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "sync_customer_profiles",
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            _customer_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_customer_subprocess.STDOUT,
                start_new_session=True,
            )

        self.message_user(
            request,
            f"Customer profile refresh started. Log: {log_path}",
            messages.SUCCESS,
        )

    @admin.action(description="Create marketing segment/list from selected profiles")
    def create_marketing_segment(self, request, queryset):
        _create_segment_from_profiles(request, queryset, "marketing")

    @admin.action(description="Create transactional segment/list from selected profiles")
    def create_transactional_segment(self, request, queryset):
        _create_segment_from_profiles(request, queryset, "transactional")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = (
        "sent_at",
        "conversation_id_short",
        "sender_type",
        "is_client_reply",
        "message_preview",
    )
    search_fields = (
        "conversation__conversation_id",
        "message_id",
        "message_text",
        "sender_type",
        "sender_name",
    )
    list_filter = (
        "sender_type",
        "is_client_reply",
        "message_type",
    )
    readonly_fields = (
        "message_pk",
        "message_id",
        "conversation",
        "sent_at",
        "sender_type",
        "sender_name",
        "message_text",
        "message_type",
        "button_clicked",
        "is_client_reply",
        "raw_line_hash",
        "raw_payload",
        "created_at",
    )
    ordering = ("-sent_at",)

    def conversation_id_short(self, obj):
        cid = obj.conversation_id
        return cid[:18] + "..." if cid and len(cid) > 18 else cid
    conversation_id_short.short_description = "conversation_id"

    def message_preview(self, obj):
        text = obj.message_text or ""
        return text[:160] + "..." if len(text) > 160 else text
    message_preview.short_description = "message"



# --- Superchat post-processing pipeline admin actions ---
import os
import sys
import subprocess
from django.contrib import messages
from .models import SuperchatSyncRun

PIPELINE_WEB_DIR = "/opt/superchat-ai-agent/web"
PIPELINE_LOG_DIR = "/opt/superchat-ai-agent/logs"


def _start_pipeline_process(modeladmin, request, queryset, force=False):
    os.makedirs(PIPELINE_LOG_DIR, exist_ok=True)

    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Selectează exact un singur sync run.",
            messages.ERROR,
        )
        return

    run = queryset.first()

    active_exists = (
        SuperchatSyncRun.objects
        .filter(status__in=["discovering", "extracting", "postprocessing", "stopping"])
        .exclude(run_id=run.run_id)
        .exists()
    )

    if active_exists:
        modeladmin.message_user(
            request,
            "Există deja un proces activ. Așteaptă să se termine înainte să pornești altul.",
            messages.ERROR,
        )
        return

    log_path = os.path.join(
        PIPELINE_LOG_DIR,
        f"pipeline_{run.run_id}{'_force' if force else ''}.log",
    )

    cmd = [
        sys.executable,
        os.path.join(PIPELINE_WEB_DIR, "manage.py"),
        "run_superchat_pipeline",
        "--run-id",
        str(run.run_id),
    ]

    if force:
        cmd.append("--force")

    with open(log_path, "a", encoding="utf-8") as log_file:
        subprocess.Popen(
            cmd,
            cwd=PIPELINE_WEB_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    run.status = "postprocessing"
    run.notes = (run.notes or "") + f"\nPipeline started from Admin. Log: {log_path}"
    run.save(update_fields=["status", "notes", "updated_at"])

    modeladmin.message_user(
        request,
        f"Pipeline pornit în background. Log: {log_path}",
        messages.SUCCESS,
    )


def start_full_postprocess_pipeline(modeladmin, request, queryset):
    return _start_pipeline_process(modeladmin, request, queryset, force=False)

start_full_postprocess_pipeline.short_description = "Run full post-processing pipeline"


def rerun_full_postprocess_pipeline_force(modeladmin, request, queryset):
    return _start_pipeline_process(modeladmin, request, queryset, force=True)

rerun_full_postprocess_pipeline_force.short_description = "Re-run full post-processing pipeline FORCE"


try:
    run_admin = admin.site._registry.get(SuperchatSyncRun)
    if run_admin:
        actions = list(run_admin.actions or [])

        existing_names = []
        for action in actions:
            if isinstance(action, str):
                existing_names.append(action)
            else:
                existing_names.append(getattr(action, "__name__", ""))

        if "start_full_postprocess_pipeline" not in existing_names:
            actions.append(start_full_postprocess_pipeline)

        if "rerun_full_postprocess_pipeline_force" not in existing_names:
            actions.append(rerun_full_postprocess_pipeline_force)

        run_admin.actions = actions

except Exception:
    pass
# --- End Superchat post-processing pipeline admin actions ---



from .models import ConversationAnalysis, ProductFeedSuggestion
from django.utils import timezone


@admin.register(ConversationAnalysis)
class ConversationAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "conversation",
        "product_id",
        "lead_score",
        "client_intent",
        "lead_stage",
        "main_objection",
        "sale_outcome",
        "analysis_status",
        "analyzed_at",
    )
    search_fields = (
        "conversation__conversation_id",
        "product_id",
        "summary",
        "missed_opportunity",
        "recommended_action",
        "recommended_message",
    )
    list_filter = (
        "analysis_status",
        "product_id",
        "client_intent",
        "lead_stage",
        "main_objection",
        "sale_outcome",
        "model",
        "prompt_version",
    )
    readonly_fields = (
        "analysis_id",
        "conversation",
        "product_id",
        "model",
        "prompt_version",
        "analysis_status",
        "lead_score",
        "client_intent",
        "lead_stage",
        "main_objection",
        "sale_outcome",
        "summary",
        "missed_opportunity",
        "operator_issue",
        "workflow_issue",
        "recommended_action",
        "recommended_message",
        "raw_result",
        "error",
        "analyzed_at",
        "created_at",
        "updated_at",
    )


@admin.register(ProductFeedSuggestion)
class ProductFeedSuggestionAdmin(admin.ModelAdmin):
    list_display = (
        "product_id",
        "suggestion_type",
        "title",
        "confidence_score",
        "status",
        "conversation",
        "created_at",
    )
    search_fields = (
        "product_id",
        "suggestion_type",
        "title",
        "suggested_question",
        "suggested_answer",
        "suggested_rule",
        "suggested_keyword",
        "reason",
        "evidence",
        "conversation__conversation_id",
    )
    list_filter = (
        "status",
        "suggestion_type",
        "product_id",
        "confidence_score",
    )
    readonly_fields = (
        "suggestion_id",
        "conversation",
        "analysis",
        "product_id",
        "suggestion_type",
        "title",
        "suggested_question",
        "suggested_answer",
        "suggested_rule",
        "suggested_keyword",
        "reason",
        "evidence",
        "confidence_score",
        "created_by",
        "raw_payload",
        "created_at",
        "updated_at",
        "reviewed_by",
        "reviewed_at",
        "applied_at",
    )
    actions = (
        "mark_approved",
        "mark_rejected",
    )

    @admin.action(description="Approve selected suggestions")
    def mark_approved(self, request, queryset):
        queryset.update(
            status="approved",
            reviewed_by=str(request.user),
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        self.message_user(request, "Sugestiile selectate au fost aprobate.")

    @admin.action(description="Reject selected suggestions")
    def mark_rejected(self, request, queryset):
        queryset.update(
            status="rejected",
            reviewed_by=str(request.user),
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        self.message_user(request, "Sugestiile selectate au fost respinse.")



# --- Apply approved product feed suggestions action ---
import os as _os
import sys as _sys
import subprocess as _subprocess
from django.contrib import messages as _messages

APPLY_SUGGESTIONS_WEB_DIR = "/opt/superchat-ai-agent/web"
APPLY_SUGGESTIONS_LOG_DIR = "/opt/superchat-ai-agent/logs"


def apply_approved_product_feed_suggestions(modeladmin, request, queryset):
    _os.makedirs(APPLY_SUGGESTIONS_LOG_DIR, exist_ok=True)

    log_path = _os.path.join(
        APPLY_SUGGESTIONS_LOG_DIR,
        "apply_product_feed_suggestions.log",
    )

    cmd = [
        _sys.executable,
        _os.path.join(APPLY_SUGGESTIONS_WEB_DIR, "manage.py"),
        "apply_product_feed_suggestions",
    ]

    with open(log_path, "a", encoding="utf-8") as log_file:
        _subprocess.Popen(
            cmd,
            cwd=APPLY_SUGGESTIONS_WEB_DIR,
            stdout=log_file,
            stderr=_subprocess.STDOUT,
            start_new_session=True,
        )

    modeladmin.message_user(
        request,
        f"Aplicarea sugestiilor aprobate a pornit în background. Log: {log_path}",
        _messages.SUCCESS,
    )


apply_approved_product_feed_suggestions.short_description = "Apply all approved suggestions to product feed"


try:
    suggestion_admin = admin.site._registry.get(ProductFeedSuggestion)

    if suggestion_admin:
        actions = list(suggestion_admin.actions or [])
        names = [
            action if isinstance(action, str) else getattr(action, "__name__", "")
            for action in actions
        ]

        if "apply_approved_product_feed_suggestions" not in names:
            actions.append(apply_approved_product_feed_suggestions)

        suggestion_admin.actions = actions

except Exception:
    pass
# --- End apply approved product feed suggestions action ---



# --- Front-end actions for AI Analysis and Product Feed Suggestions ---
import os as _frontend_os
import sys as _frontend_sys
import subprocess as _frontend_subprocess
from django.contrib import messages as _frontend_messages
from django.utils import timezone as _frontend_timezone

from .models import Conversation, ProductFeedSuggestion

FRONTEND_WEB_DIR = "/opt/superchat-ai-agent/web"
FRONTEND_LOG_DIR = "/opt/superchat-ai-agent/logs"


def _append_admin_action(model_cls, action_func):
    try:
        model_admin = admin.site._registry.get(model_cls)

        if not model_admin:
            return

        actions = list(model_admin.actions or [])

        names = [
            action if isinstance(action, str) else getattr(action, "__name__", "")
            for action in actions
        ]

        if action_func.__name__ not in names:
            actions.append(action_func)

        model_admin.actions = actions

    except Exception:
        pass


def _start_ai_analysis_for_conversations(modeladmin, request, queryset, force=False):
    _frontend_os.makedirs(FRONTEND_LOG_DIR, exist_ok=True)

    conversation_ids = list(
        queryset.values_list("conversation_id", flat=True)
    )

    if not conversation_ids:
        modeladmin.message_user(
            request,
            "Nu ai selectat nicio conversație.",
            _frontend_messages.ERROR,
        )
        return

    if len(conversation_ids) > 100:
        modeladmin.message_user(
            request,
            "Pentru siguranță, selectează maximum 100 conversații odată.",
            _frontend_messages.ERROR,
        )
        return

    timestamp = _frontend_timezone.now().strftime("%Y%m%d_%H%M%S")
    log_path = _frontend_os.path.join(
        FRONTEND_LOG_DIR,
        f"ai_analysis_selected_{timestamp}.log",
    )

    cmd = [
        _frontend_sys.executable,
        _frontend_os.path.join(FRONTEND_WEB_DIR, "manage.py"),
        "analyze_conversations_ai",
    ]

    for cid in conversation_ids:
        cmd.extend(["--conversation-id", str(cid)])

    if force:
        cmd.append("--force")

    with open(log_path, "a", encoding="utf-8") as log_file:
        _frontend_subprocess.Popen(
            cmd,
            cwd=FRONTEND_WEB_DIR,
            stdout=log_file,
            stderr=_frontend_subprocess.STDOUT,
            start_new_session=True,
        )

    modeladmin.message_user(
        request,
        f"AI Analysis pornit pentru {len(conversation_ids)} conversații. Log: {log_path}",
        _frontend_messages.SUCCESS,
    )


def analyze_selected_conversations_ai(modeladmin, request, queryset):
    return _start_ai_analysis_for_conversations(
        modeladmin,
        request,
        queryset,
        force=False,
    )

analyze_selected_conversations_ai.short_description = "Analyze selected conversations with AI"


def reanalyze_selected_conversations_ai_force(modeladmin, request, queryset):
    return _start_ai_analysis_for_conversations(
        modeladmin,
        request,
        queryset,
        force=True,
    )

reanalyze_selected_conversations_ai_force.short_description = "Re-analyze selected conversations with AI FORCE"


def apply_selected_approved_product_feed_suggestions(modeladmin, request, queryset):
    _frontend_os.makedirs(FRONTEND_LOG_DIR, exist_ok=True)

    approved = queryset.filter(status="approved")
    suggestion_ids = list(
        approved.values_list("suggestion_id", flat=True)
    )

    if not suggestion_ids:
        modeladmin.message_user(
            request,
            "Nu ai selectat sugestii cu status approved.",
            _frontend_messages.ERROR,
        )
        return

    timestamp = _frontend_timezone.now().strftime("%Y%m%d_%H%M%S")
    log_path = _frontend_os.path.join(
        FRONTEND_LOG_DIR,
        f"apply_selected_suggestions_{timestamp}.log",
    )

    cmd = [
        _frontend_sys.executable,
        _frontend_os.path.join(FRONTEND_WEB_DIR, "manage.py"),
        "apply_product_feed_suggestions",
    ]

    for sid in suggestion_ids:
        cmd.extend(["--suggestion-id", str(sid)])

    with open(log_path, "a", encoding="utf-8") as log_file:
        _frontend_subprocess.Popen(
            cmd,
            cwd=FRONTEND_WEB_DIR,
            stdout=log_file,
            stderr=_frontend_subprocess.STDOUT,
            start_new_session=True,
        )

    modeladmin.message_user(
        request,
        f"Aplicarea sugestiilor aprobate a pornit pentru {len(suggestion_ids)} sugestii. Log: {log_path}",
        _frontend_messages.SUCCESS,
    )

apply_selected_approved_product_feed_suggestions.short_description = "Apply selected approved suggestions to product feed"


_append_admin_action(Conversation, analyze_selected_conversations_ai)
_append_admin_action(Conversation, reanalyze_selected_conversations_ai_force)
_append_admin_action(ProductFeedSuggestion, apply_selected_approved_product_feed_suggestions)
# --- End front-end actions ---


# --- Safe AI decision drafts ---
def generate_safe_ai_decisions_for_selected(modeladmin, request, queryset):
    _frontend_os.makedirs(FRONTEND_LOG_DIR, exist_ok=True)

    conversation_ids = list(queryset.values_list("conversation_id", flat=True))

    if not conversation_ids:
        modeladmin.message_user(
            request,
            "Nu ai selectat nicio conversație.",
            _frontend_messages.ERROR,
        )
        return

    if len(conversation_ids) > 50:
        modeladmin.message_user(
            request,
            "Pentru siguranță, selectează maximum 50 conversații odată.",
            _frontend_messages.ERROR,
        )
        return

    timestamp = _frontend_timezone.now().strftime("%Y%m%d_%H%M%S")
    log_path = _frontend_os.path.join(
        FRONTEND_LOG_DIR,
        f"generate_safe_ai_decisions_{timestamp}.log",
    )

    cmd = [
        _frontend_sys.executable,
        _frontend_os.path.join(FRONTEND_WEB_DIR, "manage.py"),
        "generate_safe_ai_decision",
    ]

    for cid in conversation_ids:
        cmd.extend(["--conversation-id", str(cid)])

    with open(log_path, "a", encoding="utf-8") as log_file:
        _frontend_subprocess.Popen(
            cmd,
            cwd=FRONTEND_WEB_DIR,
            stdout=log_file,
            stderr=_frontend_subprocess.STDOUT,
            start_new_session=True,
        )

    modeladmin.message_user(
        request,
        f"Safe AI draft generation started for {len(conversation_ids)} conversations. Log: {log_path}",
        _frontend_messages.SUCCESS,
    )


generate_safe_ai_decisions_for_selected.short_description = "Generate safe AI draft decisions for review"
_append_admin_action(Conversation, generate_safe_ai_decisions_for_selected)
# --- End safe AI decision drafts ---



# --- AI analysis for full Superchat Sync Run ---
import os as _run_ai_os
import sys as _run_ai_sys
import subprocess as _run_ai_subprocess
from django.contrib import messages as _run_ai_messages
from django.utils import timezone as _run_ai_timezone

from .models import SuperchatSyncRun

RUN_AI_WEB_DIR = "/opt/superchat-ai-agent/web"
RUN_AI_LOG_DIR = "/opt/superchat-ai-agent/logs"


def _start_ai_analysis_for_sync_run(modeladmin, request, queryset, force=False):
    _run_ai_os.makedirs(RUN_AI_LOG_DIR, exist_ok=True)

    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Selectează exact un singur sync run.",
            _run_ai_messages.ERROR,
        )
        return

    run = queryset.first()

    timestamp = _run_ai_timezone.now().strftime("%Y%m%d_%H%M%S")
    log_path = _run_ai_os.path.join(
        RUN_AI_LOG_DIR,
        f"ai_analysis_run_{run.run_id}_{timestamp}{'_force' if force else ''}.log",
    )

    cmd = [
        _run_ai_sys.executable,
        _run_ai_os.path.join(RUN_AI_WEB_DIR, "manage.py"),
        "analyze_conversations_ai",
        "--run-id",
        str(run.run_id),
    ]

    if force:
        cmd.append("--force")

    with open(log_path, "a", encoding="utf-8") as log_file:
        _run_ai_subprocess.Popen(
            cmd,
            cwd=RUN_AI_WEB_DIR,
            stdout=log_file,
            stderr=_run_ai_subprocess.STDOUT,
            start_new_session=True,
        )

    run.notes = (run.notes or "") + f"\nAI Analysis started from Admin. Force={force}. Log: {log_path}"
    run.save(update_fields=["notes", "updated_at"])

    modeladmin.message_user(
        request,
        f"AI Analysis pornit pentru run-ul selectat. Log: {log_path}",
        _run_ai_messages.SUCCESS,
    )


def analyze_sync_run_with_ai(modeladmin, request, queryset):
    return _start_ai_analysis_for_sync_run(modeladmin, request, queryset, force=False)

analyze_sync_run_with_ai.short_description = "Analyze all conversations from this run with AI"


def reanalyze_sync_run_with_ai_force(modeladmin, request, queryset):
    return _start_ai_analysis_for_sync_run(modeladmin, request, queryset, force=True)

reanalyze_sync_run_with_ai_force.short_description = "Re-analyze all conversations from this run with AI FORCE"


try:
    run_admin = admin.site._registry.get(SuperchatSyncRun)

    if run_admin:
        actions = list(run_admin.actions or [])
        names = [
            action if isinstance(action, str) else getattr(action, "__name__", "")
            for action in actions
        ]

        if "analyze_sync_run_with_ai" not in names:
            actions.append(analyze_sync_run_with_ai)

        if "reanalyze_sync_run_with_ai_force" not in names:
            actions.append(reanalyze_sync_run_with_ai_force)

        run_admin.actions = actions

except Exception:
    pass
# --- End AI analysis for full Superchat Sync Run ---



# --- AI Sales Dashboard Admin Link ---
from django.urls import path as _admin_path
from django.shortcuts import redirect as _admin_redirect
from .views_dashboard import ai_dashboard as _ai_dashboard_view
from .models import AiSalesDashboardLink


# Add protected admin URL: /admin/ai-dashboard/
try:
    _old_admin_get_urls = admin.site.get_urls

    def _custom_admin_get_urls():
        custom_urls = [
            _admin_path(
                "ai-dashboard/",
                admin.site.admin_view(_ai_dashboard_view),
                name="ai_sales_dashboard",
            ),
        ]
        return custom_urls + _old_admin_get_urls()

    if not getattr(admin.site, "_ai_dashboard_url_added", False):
        admin.site.get_urls = _custom_admin_get_urls
        admin.site._ai_dashboard_url_added = True

except Exception:
    pass


@admin.register(AiSalesDashboardLink)
class AiSalesDashboardLinkAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_model_perms(self, request):
        if request.user.is_active and request.user.is_staff:
            return {"view": True}
        return {}

    def changelist_view(self, request, extra_context=None):
        return _admin_redirect("/admin/ai-dashboard/")
# --- End AI Sales Dashboard Admin Link ---



# --- AI Conversation Detail Admin URL ---
from django.urls import path as _detail_admin_path
from .views_dashboard import ai_conversation_detail as _ai_conversation_detail_view

try:
    _old_admin_get_urls_conversation_detail = admin.site.get_urls

    def _custom_admin_get_urls_conversation_detail():
        custom_urls = [
            _detail_admin_path(
                "ai-conversation/<str:conversation_id>/",
                admin.site.admin_view(_ai_conversation_detail_view),
                name="ai_conversation_detail",
            ),
        ]
        return custom_urls + _old_admin_get_urls_conversation_detail()

    if not getattr(admin.site, "_ai_conversation_detail_url_added", False):
        admin.site.get_urls = _custom_admin_get_urls_conversation_detail
        admin.site._ai_conversation_detail_url_added = True

except Exception:
    pass
# --- End AI Conversation Detail Admin URL ---


# --- CRM admin ---
from .models import (
    CustomerChannelIdentity,
    CustomerCommunicationEvent,
    CustomerOrder,
    CustomerOrderPhoneLink,
    CustomerConversionEvent,
    CustomerSegment,
    CustomerSegmentMembership,
)


class CustomerSegmentMembershipInline(admin.TabularInline):
    model = CustomerSegmentMembership
    extra = 0
    can_delete = True
    fields = (
        "customer_id",
        "customer_link",
        "source",
        "status",
        "added_by",
        "added_at",
    )
    readonly_fields = (
        "customer_id",
        "customer_link",
        "source",
        "added_by",
        "added_at",
    )
    ordering = ("-added_at",)

    def customer_link(self, obj):
        if not obj or not obj.customer_id:
            return "-"
        try:
            profile = CustomerProfile.objects.get(customer_id=obj.customer_id)
        except CustomerProfile.DoesNotExist:
            return str(obj.customer_id)
        return format_html(
            "<a href='/admin/superchatsync/customerprofile/{}/change/'>{}</a>",
            profile.customer_id,
            profile.display_name or profile.phone or profile.email or profile.profile_key,
        )

    customer_link.short_description = "Profile"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CustomerSegment)
class CustomerSegmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "audience_type",
        "status",
        "is_dynamic",
        "country",
        "channel",
        "product_id",
        "crm_stage",
        "profile_count",
        "live_members",
        "last_built_at",
        "updated_at",
    )
    search_fields = (
        "name",
        "slug",
        "description",
        "product_id",
        "created_by",
    )
    list_filter = (
        "audience_type",
        "status",
        "is_dynamic",
        "country",
        "channel",
        "product_id",
        "crm_stage",
        "profile_status",
    )
    readonly_fields = (
        "segment_id",
        "profile_count",
        "live_members",
        "last_built_at",
        "created_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Segment",
            {
                "fields": (
                    "segment_id",
                    "name",
                    "slug",
                    "description",
                    "audience_type",
                    "status",
                    "is_dynamic",
                )
            },
        ),
        (
            "Dynamic criteria",
            {
                "fields": (
                    "country",
                    "channel",
                    "product_id",
                    "crm_stage",
                    "profile_status",
                    "rules",
                ),
                "description": (
                    "For dynamic segments, use these fields then run the rebuild action. "
                    "rules supports has_email, has_phone, min_total_messages, "
                    "min_total_conversations, last_seen_days."
                ),
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "profile_count",
                    "live_members",
                    "last_built_at",
                    "created_by",
                    "metadata",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    inlines = [CustomerSegmentMembershipInline]
    actions = ("rebuild_dynamic_segments", "recount_members", "mark_active", "mark_archived")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("audience_type", "name")

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = str(request.user)
        super().save_model(request, obj, form, change)

    @admin.display(description="Live members")
    def live_members(self, obj):
        return CustomerSegmentMembership.objects.filter(segment=obj, status="active").count()

    @admin.action(description="Rebuild selected dynamic segments")
    def rebuild_dynamic_segments(self, request, queryset):
        rebuilt = 0
        skipped = 0
        total_members = 0
        for segment in queryset:
            if not segment.is_dynamic:
                skipped += 1
                continue
            total_members += _rebuild_dynamic_segment(segment, str(request.user))
            rebuilt += 1
        self.message_user(
            request,
            f"Rebuilt {rebuilt} dynamic segment(s), {total_members} active memberships. Skipped {skipped} manual segment(s).",
            messages.SUCCESS if rebuilt else messages.WARNING,
        )

    @admin.action(description="Recount selected segment members")
    def recount_members(self, request, queryset):
        for segment in queryset:
            _refresh_segment_count(segment)
        self.message_user(request, f"Recounted {queryset.count()} segment(s).", messages.SUCCESS)

    @admin.action(description="Mark selected segments active")
    def mark_active(self, request, queryset):
        updated = queryset.update(status="active", updated_at=timezone.now())
        self.message_user(request, f"{updated} segment(s) marked active.", messages.SUCCESS)

    @admin.action(description="Archive selected segments")
    def mark_archived(self, request, queryset):
        updated = queryset.update(status="archived", updated_at=timezone.now())
        self.message_user(request, f"{updated} segment(s) archived.", messages.WARNING)


@admin.register(CustomerSegmentMembership)
class CustomerSegmentMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "segment",
        "customer_id",
        "customer_profile",
        "source",
        "status",
        "added_by",
        "added_at",
    )
    search_fields = (
        "segment__name",
        "=customer_id",
        "added_by",
    )
    list_filter = (
        "segment",
        "source",
        "status",
        "added_at",
    )
    readonly_fields = (
        "membership_id",
        "customer_profile",
        "added_at",
        "updated_at",
    )
    ordering = ("-added_at",)

    def customer_profile(self, obj):
        try:
            profile = CustomerProfile.objects.get(customer_id=obj.customer_id)
        except CustomerProfile.DoesNotExist:
            return "-"
        return format_html(
            "<a href='/admin/superchatsync/customerprofile/{}/change/'>{}</a>",
            profile.customer_id,
            profile.display_name or profile.phone or profile.email or profile.profile_key,
        )

    customer_profile.short_description = "Profile"


@admin.register(CustomerChannelIdentity)
class CustomerChannelIdentityAdmin(admin.ModelAdmin):
    list_display = (
        "customer_id",
        "channel",
        "identifier",
        "provider",
        "is_primary",
        "status",
        "last_seen_at",
    )
    search_fields = (
        "=customer_id",
        "identifier",
        "normalized_identifier",
        "provider",
        "provider_contact_id",
    )
    list_filter = ("channel", "provider", "is_primary", "status")
    readonly_fields = (
        "identity_id",
        "created_at",
        "updated_at",
    )
    ordering = ("channel", "normalized_identifier")


@admin.register(CustomerCommunicationEvent)
class CustomerCommunicationEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "customer_id",
        "channel",
        "direction",
        "event_type",
        "status",
        "conversation_id",
        "body_short",
    )
    search_fields = (
        "=customer_id",
        "conversation_id",
        "message_id",
        "provider_message_id",
        "campaign_id",
        "workflow_id",
        "body_preview",
    )
    list_filter = (
        "channel",
        "direction",
        "event_type",
        "status",
        "provider",
    )
    readonly_fields = (
        "event_id",
        "created_at",
        "updated_at",
    )
    ordering = ("-occurred_at",)

    def body_short(self, obj):
        text = obj.body_preview or ""
        return text[:120] + "..." if len(text) > 120 else text
    body_short.short_description = "Preview"


class CustomerOrderPhoneLinkInline(admin.TabularInline):
    model = CustomerOrderPhoneLink
    extra = 0
    can_delete = False
    fields = (
        "normalized_phone",
        "raw_phone",
        "customer_id",
        "is_primary",
        "source",
        "country_id",
        "created_at",
    )
    readonly_fields = fields
    ordering = ("-is_primary", "normalized_phone")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CustomerOrder)
class CustomerOrderAdmin(admin.ModelAdmin):
    list_display = (
        "submitted_at",
        "external_order_id",
        "customer_id",
        "product_id",
        "quantity",
        "cost",
        "currency",
        "cost_eur",
        "status",
        "external_status",
        "source_channel",
        "webhook_http_status",
    )
    search_fields = (
        "=customer_id",
        "product_id",
        "sku",
        "source_conversation_id",
        "source_message_id",
        "external_order_id",
        "idempotency_key",
        "customer_comment",
    )
    list_filter = (
        "status",
        "source_channel",
        "product_id",
        "currency",
        "external_status",
        "exchange_rate_source",
    )
    readonly_fields = (
        "order_id",
        "idempotency_key",
        "cost_eur_estimate",
        "eur_exchange_rate",
        "exchange_rate_month",
        "exchange_rate_source",
        "submitted_at",
        "updated_at",
    )
    ordering = ("-submitted_at",)
    inlines = [CustomerOrderPhoneLinkInline]

    @admin.display(description="EUR")
    def cost_eur(self, obj):
        return obj.cost_eur_estimate if obj.cost_eur_estimate is not None else "-"


@admin.register(CurrencyMonthlyRate)
class CurrencyMonthlyRateAdmin(admin.ModelAdmin):
    list_display = (
        "month",
        "currency",
        "units_per_eur",
        "rate_to_eur",
        "source",
        "updated_at",
    )
    search_fields = ("currency", "source")
    list_filter = ("currency", "source", "month")
    readonly_fields = ("rate_id", "created_at", "updated_at")
    ordering = ("-month", "currency")


@admin.register(CustomerOrderPhoneLink)
class CustomerOrderPhoneLinkAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "normalized_phone",
        "customer_id",
        "is_primary",
        "source",
        "country_id",
        "created_at",
    )
    search_fields = (
        "normalized_phone",
        "raw_phone",
        "order__external_order_id",
        "order__idempotency_key",
        "=customer_id",
    )
    list_filter = (
        "is_primary",
        "source",
        "country_id",
        "created_at",
    )
    readonly_fields = (
        "link_id",
        "order",
        "customer_id",
        "normalized_phone",
        "raw_phone",
        "is_primary",
        "source",
        "country_id",
        "metadata",
        "created_at",
        "updated_at",
    )
    ordering = ("order_id", "-is_primary", "normalized_phone")


@admin.register(CustomerConversionEvent)
class CustomerConversionEventAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "customer_id",
        "channel",
        "event_type",
        "product_id",
        "conversation_id",
        "value",
        "currency",
    )
    search_fields = (
        "=customer_id",
        "conversation_id",
        "product_id",
        "campaign_id",
    )
    list_filter = (
        "channel",
        "event_type",
        "product_id",
        "currency",
    )
    readonly_fields = (
        "conversion_id",
        "created_at",
    )
    ordering = ("-occurred_at",)


def _crm_dashboard(request):
    from collections import Counter
    from django.http import HttpResponse
    from django.utils.html import escape

    profiles = list(CustomerProfile.objects.all())
    country_counts = Counter(_phone_country(profile.phone)[1] for profile in profiles)
    channel_counts = Counter(
        CustomerChannelIdentity.objects.values_list("channel", flat=True)
    )
    profile_ids = {profile.customer_id for profile in profiles}
    buyer_ids = set(CustomerConversionEvent.objects.filter(event_type="buy").values_list("customer_id", flat=True))
    order_ids = set(CustomerOrder.objects.exclude(status__in=["failed", "cancelled"]).values_list("customer_id", flat=True))
    replied_ids = set(CustomerConversionEvent.objects.filter(event_type="replied").values_list("customer_id", flat=True))
    event_ids = set(CustomerCommunicationEvent.objects.values_list("customer_id", flat=True))
    stage_counts = Counter(
        {
            "Buyer": len(profile_ids & buyer_ids),
            "Order submitted": len((profile_ids & order_ids) - buyer_ids),
            "Engaged": len((profile_ids & replied_ids) - order_ids - buyer_ids),
            "Lead": len((profile_ids & event_ids) - replied_ids - order_ids - buyer_ids),
            "No activity": len(profile_ids - event_ids),
        }
    )

    product_orders = list(
        CustomerOrder.objects.values("product_id")
        .annotate(total=Sum("cost"))
        .order_by("-total")[:12]
    )
    conversion_counts = Counter(
        CustomerConversionEvent.objects.values_list("event_type", flat=True)
    )

    def table_rows(items):
        return "".join(
            f"<tr><td>{escape(str(label))}</td><td>{escape(str(value))}</td></tr>"
            for label, value in items
        ) or "<tr><td colspan='2'>No data</td></tr>"

    product_rows = "".join(
        f"<tr><td>{escape(str(row.get('product_id') or '-'))}</td><td>{escape(str(row.get('total') or 0))} RON</td></tr>"
        for row in product_orders
    ) or "<tr><td colspan='2'>No orders yet</td></tr>"

    html = f"""
    <!doctype html>
    <html lang="ro">
    <head>
      <meta charset="utf-8">
      <title>CRM Dashboard</title>
      <style>
        body {{ font-family: Arial, sans-serif; margin: 24px; color: #111; }}
        .top {{ display:flex; justify-content:space-between; align-items:baseline; gap:16px; flex-wrap:wrap; }}
        .cards {{ display:grid; grid-template-columns:repeat(4,minmax(140px,1fr)); gap:10px; margin:16px 0; }}
        .card {{ border:1px solid #ddd; border-radius:8px; padding:12px; background:white; }}
        .label {{ color:#666; font-size:12px; }}
        .value {{ font-size:24px; font-weight:700; margin-top:4px; }}
        .grid {{ display:grid; grid-template-columns:repeat(2,minmax(280px,1fr)); gap:18px; }}
        table {{ width:100%; border-collapse:collapse; background:white; }}
        th, td {{ border-bottom:1px solid #eee; padding:8px; text-align:left; }}
        th {{ background:#f6f6f6; }}
        a {{ color:#0b57d0; }}
        @media (max-width: 900px) {{ .cards, .grid {{ grid-template-columns:1fr; }} }}
      </style>
    </head>
    <body>
      <div class="top">
        <div>
          <h1>CRM Dashboard</h1>
          <p>Customer profiles, channels, orders and conversion events.</p>
        </div>
        <p><a href="/admin/superchatsync/customerprofile/">Open customer profiles</a></p>
      </div>
      <div class="cards">
        <div class="card"><div class="label">Customers</div><div class="value">{CustomerProfile.objects.count()}</div></div>
        <div class="card"><div class="label">Channel identities</div><div class="value">{CustomerChannelIdentity.objects.count()}</div></div>
        <div class="card"><div class="label">Communication events</div><div class="value">{CustomerCommunicationEvent.objects.count()}</div></div>
        <div class="card"><div class="label">Orders</div><div class="value">{CustomerOrder.objects.count()}</div></div>
      </div>
      <div class="grid">
        <section><h2>By Country</h2><table><tr><th>Country</th><th>Customers</th></tr>{table_rows(country_counts.most_common())}</table></section>
        <section><h2>By Channel</h2><table><tr><th>Channel</th><th>Identities</th></tr>{table_rows(channel_counts.most_common())}</table></section>
        <section><h2>CRM Stage</h2><table><tr><th>Stage</th><th>Customers</th></tr>{table_rows(stage_counts.most_common())}</table></section>
        <section><h2>Conversion Events</h2><table><tr><th>Event</th><th>Count</th></tr>{table_rows(conversion_counts.most_common())}</table></section>
        <section><h2>Revenue by Product</h2><table><tr><th>Product</th><th>Revenue</th></tr>{product_rows}</table></section>
      </div>
    </body>
    </html>
    """
    return HttpResponse(html)


try:
    _old_admin_get_urls_crm_dashboard = admin.site.get_urls

    def _custom_admin_get_urls_crm_dashboard():
        from django.urls import path as _crm_dashboard_path

        custom_urls = [
            _crm_dashboard_path(
                "crm-dashboard/",
                admin.site.admin_view(_crm_dashboard),
                name="crm_dashboard",
            ),
        ]
        return custom_urls + _old_admin_get_urls_crm_dashboard()

    if not getattr(admin.site, "_crm_dashboard_url_added", False):
        admin.site.get_urls = _custom_admin_get_urls_crm_dashboard
        admin.site._crm_dashboard_url_added = True
except Exception:
    pass
# --- End CRM admin ---



# --- AI dashboard action URLs ---
from django.urls import path as _ai_action_path
from .views_dashboard import (
    ai_conversation_reanalyze as _ai_conversation_reanalyze_view,
    ai_suggestion_approve as _ai_suggestion_approve_view,
    ai_suggestion_reject as _ai_suggestion_reject_view,
    ai_suggestion_apply as _ai_suggestion_apply_view,
)

try:
    _old_admin_get_urls_ai_actions = admin.site.get_urls

    def _custom_admin_get_urls_ai_actions():
        custom_urls = [
            _ai_action_path(
                "ai-conversation/<str:conversation_id>/reanalyze/",
                admin.site.admin_view(_ai_conversation_reanalyze_view),
                name="ai_conversation_reanalyze",
            ),
            _ai_action_path(
                "ai-suggestion/<uuid:suggestion_id>/approve/",
                admin.site.admin_view(_ai_suggestion_approve_view),
                name="ai_suggestion_approve",
            ),
            _ai_action_path(
                "ai-suggestion/<uuid:suggestion_id>/reject/",
                admin.site.admin_view(_ai_suggestion_reject_view),
                name="ai_suggestion_reject",
            ),
            _ai_action_path(
                "ai-suggestion/<uuid:suggestion_id>/apply/",
                admin.site.admin_view(_ai_suggestion_apply_view),
                name="ai_suggestion_apply",
            ),
        ]
        return custom_urls + _old_admin_get_urls_ai_actions()

    if not getattr(admin.site, "_ai_dashboard_action_urls_added", False):
        admin.site.get_urls = _custom_admin_get_urls_ai_actions
        admin.site._ai_dashboard_action_urls_added = True

except Exception:
    pass
# --- End AI dashboard action URLs ---



# --- AI Quality Report Admin URL ---
from django.urls import path as _quality_admin_path
from .views_dashboard import ai_quality_report as _ai_quality_report_view

try:
    _old_admin_get_urls_quality_report = admin.site.get_urls

    def _custom_admin_get_urls_quality_report():
        custom_urls = [
            _quality_admin_path(
                "ai-quality-report/",
                admin.site.admin_view(_ai_quality_report_view),
                name="ai_quality_report",
            ),
        ]
        return custom_urls + _old_admin_get_urls_quality_report()

    if not getattr(admin.site, "_ai_quality_report_url_added", False):
        admin.site.get_urls = _custom_admin_get_urls_quality_report
        admin.site._ai_quality_report_url_added = True

except Exception:
    pass
# --- End AI Quality Report Admin URL ---



# --- Generate Quality Improvement Suggestions URL ---
from django.urls import path as _quality_generate_path
from .views_dashboard import ai_quality_generate_suggestions as _ai_quality_generate_suggestions_view

try:
    _old_admin_get_urls_quality_generate = admin.site.get_urls

    def _custom_admin_get_urls_quality_generate():
        custom_urls = [
            _quality_generate_path(
                "ai-quality-report/generate-suggestions/",
                admin.site.admin_view(_ai_quality_generate_suggestions_view),
                name="ai_quality_generate_suggestions",
            ),
        ]
        return custom_urls + _old_admin_get_urls_quality_generate()

    if not getattr(admin.site, "_ai_quality_generate_url_added", False):
        admin.site.get_urls = _custom_admin_get_urls_quality_generate
        admin.site._ai_quality_generate_url_added = True

except Exception:
    pass
# --- End Generate Quality Improvement Suggestions URL ---


# --- Product Knowledge Imports Admin ---
from .models import ProductKnowledgeImport
import os as _pki_os
import sys as _pki_sys
import subprocess as _pki_subprocess
from django.contrib import messages as _pki_messages


@admin.register(ProductKnowledgeImport)
class ProductKnowledgeImportAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "product",
        "status",
        "suggestions_created_count",
        "extracted_char_count",
        "created_at",
        "processed_at",
    )
    search_fields = (
        "title",
        "product__product_id",
        "product__product_name",
        "notes",
        "error",
    )
    list_filter = (
        "status",
        "product",
    )
    readonly_fields = (
        "import_id",
        "status",
        "original_filename",
        "extracted_text",
        "extracted_char_count",
        "suggestions_created_count",
        "error",
        "created_by",
        "processed_at",
        "created_at",
        "updated_at",
    )
    fields = (
        "import_id",
        "product",
        "title",
        "source_file",
        "status",
        "notes",
        "original_filename",
        "extracted_char_count",
        "suggestions_created_count",
        "error",
        "created_by",
        "processed_at",
        "created_at",
        "updated_at",
    )
    actions = (
        "parse_selected_with_ai",
        "reparse_selected_with_ai_force",
    )

    def save_model(self, request, obj, form, change):
        from django.utils import timezone as _pki_timezone

        now = _pki_timezone.now()

        if not obj.created_by:
            obj.created_by = str(request.user)

        if obj.source_file and not obj.original_filename:
            obj.original_filename = _pki_os.path.basename(str(obj.source_file.name))

        if not obj.created_at:
            obj.created_at = now

        obj.updated_at = now

        if not obj.status:
            obj.status = "uploaded"

        super().save_model(request, obj, form, change)

    @admin.action(description="Parse selected documents with AI")
    def parse_selected_with_ai(self, request, queryset):
        if queryset.count() > 10:
            self.message_user(
                request,
                "Selectează maximum 10 documente odată.",
                _pki_messages.ERROR,
            )
            return

        log_dir = "/opt/superchat-ai-agent/logs"
        _pki_os.makedirs(log_dir, exist_ok=True)

        started = 0

        for item in queryset:
            log_path = _pki_os.path.join(
                log_dir,
                f"product_knowledge_import_{item.import_id}.log",
            )

            cmd = [
                _pki_sys.executable,
                "/opt/superchat-ai-agent/web/manage.py",
                "parse_product_knowledge_import",
                "--import-id",
                str(item.import_id),
            ]

            with open(log_path, "a", encoding="utf-8") as log_file:
                _pki_subprocess.Popen(
                    cmd,
                    cwd="/opt/superchat-ai-agent/web",
                    stdout=log_file,
                    stderr=_pki_subprocess.STDOUT,
                    start_new_session=True,
                )

            item.status = "queued"
            item.save(update_fields=["status", "updated_at"])

            started += 1

        self.message_user(
            request,
            f"Parsing AI pornit pentru {started} documente.",
            _pki_messages.SUCCESS,
        )

    @admin.action(description="Re-parse selected documents with AI FORCE")
    def reparse_selected_with_ai_force(self, request, queryset):
        if queryset.count() > 10:
            self.message_user(
                request,
                "Selectează maximum 10 documente odată.",
                _pki_messages.ERROR,
            )
            return

        log_dir = "/opt/superchat-ai-agent/logs"
        _pki_os.makedirs(log_dir, exist_ok=True)

        started = 0

        for item in queryset:
            log_path = _pki_os.path.join(
                log_dir,
                f"product_knowledge_import_force_{item.import_id}.log",
            )

            cmd = [
                _pki_sys.executable,
                "/opt/superchat-ai-agent/web/manage.py",
                "parse_product_knowledge_import",
                "--import-id",
                str(item.import_id),
                "--force",
            ]

            with open(log_path, "a", encoding="utf-8") as log_file:
                _pki_subprocess.Popen(
                    cmd,
                    cwd="/opt/superchat-ai-agent/web",
                    stdout=log_file,
                    stderr=_pki_subprocess.STDOUT,
                    start_new_session=True,
                )

            item.status = "queued"
            item.save(update_fields=["status", "updated_at"])

            started += 1

        self.message_user(
            request,
            f"Re-parsing FORCE pornit pentru {started} documente.",
            _pki_messages.SUCCESS,
        )

# --- End Product Knowledge Imports Admin ---


# --- Product Knowledge Package Admin Extension ---
from django.urls import path as _pkp_path
from django.utils.html import format_html as _pkp_format_html
import os as _pkp_os
import sys as _pkp_sys
import subprocess as _pkp_subprocess

from .views_knowledge_package import (
    product_knowledge_package_preview as _pkp_preview_view,
    product_knowledge_package_convert as _pkp_convert_view,
)


def _pki_package_preview_link(self, obj):
    if not obj or not getattr(obj, "import_id", None):
        return "-"

    return _pkp_format_html(
        '<a href="/admin/product-knowledge-import/{}/package/">Preview package</a>',
        obj.import_id,
    )


def _pki_create_ai_knowledge_package(self, request, queryset):
    if queryset.count() > 5:
        self.message_user(request, "Selectează maximum 5 documente odată.", level="error")
        return

    log_dir = "/opt/superchat-ai-agent/logs"
    _pkp_os.makedirs(log_dir, exist_ok=True)

    started = 0

    for item in queryset:
        log_path = _pkp_os.path.join(
            log_dir,
            f"create_knowledge_package_{item.import_id}.log",
        )

        cmd = [
            _pkp_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "create_product_knowledge_package",
            "--import-id",
            str(item.import_id),
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pkp_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pkp_subprocess.STDOUT,
                start_new_session=True,
            )

        item.knowledge_package_status = "queued"
        item.save(update_fields=["knowledge_package_status", "updated_at"])

        started += 1

    self.message_user(
        request,
        f"Crearea knowledge package a pornit pentru {started} documente.",
        level="success",
    )


def _pki_convert_package_to_suggestions(self, request, queryset):
    if queryset.count() > 10:
        self.message_user(request, "Selectează maximum 10 documente odată.", level="error")
        return

    log_dir = "/opt/superchat-ai-agent/logs"
    _pkp_os.makedirs(log_dir, exist_ok=True)

    started = 0

    for item in queryset:
        log_path = _pkp_os.path.join(
            log_dir,
            f"convert_knowledge_package_{item.import_id}.log",
        )

        cmd = [
            _pkp_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "convert_knowledge_package_to_suggestions",
            "--import-id",
            str(item.import_id),
            "--force",
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pkp_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pkp_subprocess.STDOUT,
                start_new_session=True,
            )

        started += 1

    self.message_user(
        request,
        f"Conversia package → Product Feed Suggestions a pornit pentru {started} documente.",
        level="success",
    )


_pki_create_ai_knowledge_package.short_description = "Create AI knowledge package"
_pki_convert_package_to_suggestions.short_description = "Convert knowledge package to Product Feed Suggestions"
_pki_package_preview_link.short_description = "Knowledge Package"


try:
    ProductKnowledgeImportAdmin.package_preview = _pki_package_preview_link
    ProductKnowledgeImportAdmin.create_ai_knowledge_package = _pki_create_ai_knowledge_package
    ProductKnowledgeImportAdmin.convert_package_to_suggestions = _pki_convert_package_to_suggestions

    current_actions = list(getattr(ProductKnowledgeImportAdmin, "actions", []) or [])

    for action_name in [
        "create_ai_knowledge_package",
        "convert_package_to_suggestions",
    ]:
        if action_name not in current_actions:
            current_actions.append(action_name)

    ProductKnowledgeImportAdmin.actions = tuple(current_actions)

    current_list_display = list(getattr(ProductKnowledgeImportAdmin, "list_display", []) or [])

    if "package_preview" not in current_list_display:
        current_list_display.append("package_preview")

    ProductKnowledgeImportAdmin.list_display = tuple(current_list_display)

except Exception:
    pass


try:
    _old_admin_get_urls_pkp = admin.site.get_urls

    def _custom_admin_get_urls_pkp():
        custom_urls = [
            _pkp_path(
                "product-knowledge-import/<uuid:import_id>/package/",
                admin.site.admin_view(_pkp_preview_view),
                name="product_knowledge_package_preview",
            ),
            _pkp_path(
                "product-knowledge-import/<uuid:import_id>/convert-package/",
                admin.site.admin_view(_pkp_convert_view),
                name="product_knowledge_package_convert",
            ),
        ]
        return custom_urls + _old_admin_get_urls_pkp()

    if not getattr(admin.site, "_product_knowledge_package_urls_added", False):
        admin.site.get_urls = _custom_admin_get_urls_pkp
        admin.site._product_knowledge_package_urls_added = True

except Exception:
    pass
# --- End Product Knowledge Package Admin Extension ---


# --- Product Knowledge Items final workflow ---
from django.contrib import messages as _pki2_messages
from django.urls import path as _pki2_path
from django.utils.html import format_html as _pki2_format_html
import os as _pki2_os
import sys as _pki2_sys
import subprocess as _pki2_subprocess

from .models import ProductKnowledgeItem
from .views_knowledge_items import (
    product_knowledge_items_page as _pki2_items_page,
    approve_item as _pki2_approve_item,
    reject_item as _pki2_reject_item,
    apply_item as _pki2_apply_item,
    approve_high_confidence as _pki2_approve_high_confidence,
    apply_approved as _pki2_apply_approved,
)


class ProductKnowledgeItemAdmin(admin.ModelAdmin):
    list_display = (
        "category",
        "title",
        "product",
        "status",
        "confidence_score",
        "applied_target_table",
        "created_at",
    )
    list_filter = (
        "status",
        "category",
        "product",
    )
    search_fields = (
        "title",
        "question",
        "answer",
        "rule",
        "keyword",
        "description",
        "evidence",
        "apply_error",
    )
    readonly_fields = (
        "item_id",
        "knowledge_import",
        "product",
        "category",
        "title",
        "question",
        "answer",
        "rule",
        "keyword",
        "description",
        "price",
        "target_product_name",
        "target_product_id",
        "evidence",
        "confidence_score",
        "priority",
        "status",
        "applied_target_table",
        "applied_target_id",
        "apply_error",
        "raw_payload",
        "reviewed_by",
        "reviewed_at",
        "applied_at",
        "created_at",
        "updated_at",
    )
    actions = (
        "approve_selected_items",
        "reject_selected_items",
        "apply_selected_items",
    )

    @admin.action(description="Approve selected knowledge items")
    def approve_selected_items(self, request, queryset):
        count = queryset.update(
            status="approved",
            reviewed_by=str(request.user),
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )

        self.message_user(
            request,
            f"{count} itemuri aprobate.",
            _pki2_messages.SUCCESS,
        )

    @admin.action(description="Reject selected knowledge items")
    def reject_selected_items(self, request, queryset):
        count = queryset.update(
            status="rejected",
            reviewed_by=str(request.user),
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )

        self.message_user(
            request,
            f"{count} itemuri respinse.",
            _pki2_messages.SUCCESS,
        )

    @admin.action(description="Apply selected knowledge items")
    def apply_selected_items(self, request, queryset):
        ids = [str(x.item_id) for x in queryset]

        if not ids:
            return

        log_dir = "/opt/superchat-ai-agent/logs"
        _pki2_os.makedirs(log_dir, exist_ok=True)

        log_path = _pki2_os.path.join(log_dir, "apply_selected_knowledge_items.log")

        cmd = [
            _pki2_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "apply_product_knowledge_items",
        ]

        for item_id in ids:
            cmd.extend(["--item-id", item_id])

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pki2_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pki2_subprocess.STDOUT,
                start_new_session=True,
            )

        self.message_user(
            request,
            f"Apply pornit pentru {len(ids)} itemuri. Log: {log_path}",
            _pki2_messages.SUCCESS,
        )


try:
    admin.site.register(ProductKnowledgeItem, ProductKnowledgeItemAdmin)
except admin.sites.AlreadyRegistered:
    pass


def _pki2_open_items_link(self, obj):
    if not obj or not getattr(obj, "import_id", None):
        return "-"

    return _pki2_format_html(
        '<a href="/admin/product-knowledge-import/{}/items/">Open extracted knowledge</a>',
        obj.import_id,
    )


def _pki2_items_count(self, obj):
    if not obj or not getattr(obj, "import_id", None):
        return 0

    with connection.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*)
            FROM product_knowledge_items
            WHERE import_id = %s
        """, [str(obj.import_id)])
        return cur.fetchone()[0]


def _pki2_create_ai_knowledge_items_from_docx(self, request, queryset):
    if queryset.count() > 5:
        self.message_user(
            request,
            "Selectează maximum 5 documente odată.",
            _pki2_messages.ERROR,
        )
        return

    log_dir = "/opt/superchat-ai-agent/logs"
    _pki2_os.makedirs(log_dir, exist_ok=True)

    started = 0

    for item in queryset:
        log_path = _pki2_os.path.join(
            log_dir,
            f"create_ai_knowledge_items_{item.import_id}.log",
        )

        bash_cmd = (
            f'"{_pki2_sys.executable}" "/opt/superchat-ai-agent/web/manage.py" '
            f'create_product_knowledge_package --import-id "{item.import_id}" '
            f'&& "{_pki2_sys.executable}" "/opt/superchat-ai-agent/web/manage.py" '
            f'convert_knowledge_package_to_items --import-id "{item.import_id}" --force'
        )

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pki2_subprocess.Popen(
                ["/bin/bash", "-lc", bash_cmd],
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pki2_subprocess.STDOUT,
                start_new_session=True,
            )

        item.knowledge_package_status = "queued"
        item.save(update_fields=["knowledge_package_status", "updated_at"])

        started += 1

    self.message_user(
        request,
        f"AI Knowledge Items generation pornit pentru {started} documente.",
        _pki2_messages.SUCCESS,
    )


def _pki2_convert_package_to_items(self, request, queryset):
    if queryset.count() > 10:
        self.message_user(
            request,
            "Selectează maximum 10 documente odată.",
            _pki2_messages.ERROR,
        )
        return

    log_dir = "/opt/superchat-ai-agent/logs"
    _pki2_os.makedirs(log_dir, exist_ok=True)

    started = 0

    for item in queryset:
        log_path = _pki2_os.path.join(
            log_dir,
            f"convert_package_to_items_{item.import_id}.log",
        )

        cmd = [
            _pki2_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "convert_knowledge_package_to_items",
            "--import-id",
            str(item.import_id),
            "--force",
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pki2_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pki2_subprocess.STDOUT,
                start_new_session=True,
            )

        started += 1

    self.message_user(
        request,
        f"Conversia package → Product Knowledge Items a pornit pentru {started} documente.",
        _pki2_messages.SUCCESS,
    )


def _pki2_apply_approved_items_from_import(self, request, queryset):
    log_dir = "/opt/superchat-ai-agent/logs"
    _pki2_os.makedirs(log_dir, exist_ok=True)

    started = 0

    for item in queryset:
        log_path = _pki2_os.path.join(
            log_dir,
            f"apply_approved_knowledge_items_{item.import_id}.log",
        )

        cmd = [
            _pki2_sys.executable,
            "/opt/superchat-ai-agent/web/manage.py",
            "apply_product_knowledge_items",
            "--import-id",
            str(item.import_id),
            "--approved-only",
        ]

        with open(log_path, "a", encoding="utf-8") as log_file:
            _pki2_subprocess.Popen(
                cmd,
                cwd="/opt/superchat-ai-agent/web",
                stdout=log_file,
                stderr=_pki2_subprocess.STDOUT,
                start_new_session=True,
            )

        started += 1

    self.message_user(
        request,
        f"Apply approved pornit pentru {started} importuri.",
        _pki2_messages.SUCCESS,
    )


_pki2_open_items_link.short_description = "Extracted Knowledge"
_pki2_items_count.short_description = "Knowledge Items"
_pki2_create_ai_knowledge_items_from_docx.short_description = "Create AI Knowledge Items from DOCX"
_pki2_convert_package_to_items.short_description = "Convert package to Product Knowledge Items"
_pki2_apply_approved_items_from_import.short_description = "Apply approved Product Knowledge Items"


try:
    ProductKnowledgeImportAdmin.open_extracted_knowledge = _pki2_open_items_link
    ProductKnowledgeImportAdmin.knowledge_items_count = _pki2_items_count
    ProductKnowledgeImportAdmin.create_ai_knowledge_items_from_docx = _pki2_create_ai_knowledge_items_from_docx
    ProductKnowledgeImportAdmin.convert_package_to_items = _pki2_convert_package_to_items
    ProductKnowledgeImportAdmin.apply_approved_items_from_import = _pki2_apply_approved_items_from_import

    current_display = list(getattr(ProductKnowledgeImportAdmin, "list_display", []) or [])

    for field in [
        "knowledge_items_count",
        "open_extracted_knowledge",
    ]:
        if field not in current_display:
            current_display.append(field)

    ProductKnowledgeImportAdmin.list_display = tuple(current_display)

    current_actions = list(getattr(ProductKnowledgeImportAdmin, "actions", []) or [])

    for action in [
        "create_ai_knowledge_items_from_docx",
        "convert_package_to_items",
        "apply_approved_items_from_import",
    ]:
        if action not in current_actions:
            current_actions.append(action)

    ProductKnowledgeImportAdmin.actions = tuple(current_actions)

except Exception:
    pass


try:
    _old_admin_get_urls_knowledge_items = admin.site.get_urls

    def _custom_admin_get_urls_knowledge_items():
        custom_urls = [
            _pki2_path(
                "product-knowledge-import/<uuid:import_id>/items/",
                admin.site.admin_view(_pki2_items_page),
                name="product_knowledge_items_page",
            ),
            _pki2_path(
                "product-knowledge-import/<uuid:import_id>/approve-high-confidence/",
                admin.site.admin_view(_pki2_approve_high_confidence),
                name="product_knowledge_approve_high_confidence",
            ),
            _pki2_path(
                "product-knowledge-import/<uuid:import_id>/apply-approved/",
                admin.site.admin_view(_pki2_apply_approved),
                name="product_knowledge_apply_approved",
            ),
            _pki2_path(
                "product-knowledge-item/<uuid:item_id>/approve/",
                admin.site.admin_view(_pki2_approve_item),
                name="product_knowledge_item_approve",
            ),
            _pki2_path(
                "product-knowledge-item/<uuid:item_id>/reject/",
                admin.site.admin_view(_pki2_reject_item),
                name="product_knowledge_item_reject",
            ),
            _pki2_path(
                "product-knowledge-item/<uuid:item_id>/apply/",
                admin.site.admin_view(_pki2_apply_item),
                name="product_knowledge_item_apply",
            ),
        ]

        return custom_urls + _old_admin_get_urls_knowledge_items()

    if not getattr(admin.site, "_product_knowledge_items_urls_added", False):
        admin.site.get_urls = _custom_admin_get_urls_knowledge_items
        admin.site._product_knowledge_items_urls_added = True

except Exception:
    pass
# --- End Product Knowledge Items final workflow ---

# Product Creative Library Admin

from django.contrib import admin
from django.conf import settings
from .models import ProductCreativeAsset
from django import forms
from django.db import connection




class ProductCreativeAssetAdminForm(forms.ModelForm):
    product_id = forms.ChoiceField(
        label="Product",
        required=True,
        help_text="Selectează produsul existent pentru care se folosește acest creative."
    )

    class Meta:
        model = ProductCreativeAsset
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        choices = self.get_product_choices()

        current_product_id = None
        if self.instance and self.instance.product_id:
            current_product_id = str(self.instance.product_id)

        existing_values = {str(value) for value, label in choices}

        if current_product_id and current_product_id not in existing_values:
            choices.append((current_product_id, f"{current_product_id} — produs existent în asset"))

        self.fields["product_id"].choices = choices

    def get_product_choices(self):
        try:
            with connection.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'products'
                """)
                columns = [row[0] for row in cur.fetchall()]

            if "product_id" not in columns:
                return [("", "Products table nu are coloana product_id")]

            label_candidates = [
                "name",
                "title",
                "product_name",
                "display_name",
                "sku",
                "slug",
            ]

            label_column = None
            for col in label_candidates:
                if col in columns:
                    label_column = col
                    break

            if label_column:
                query = f"""
                    SELECT product_id, {label_column}
                    FROM products
                    ORDER BY product_id::text ASC
                    LIMIT 1000
                """
            else:
                query = """
                    SELECT product_id, NULL
                    FROM products
                    ORDER BY product_id::text ASC
                    LIMIT 1000
                """

            with connection.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()

            choices = [("", "Alege produsul...")]

            for product_id, label in rows:
                product_id = str(product_id)
                label = str(label).strip() if label else ""

                if label:
                    choices.append((product_id, f"{product_id} — {label}"))
                else:
                    choices.append((product_id, product_id))

            return choices

        except Exception as exc:
            return [("", f"Eroare la citirea produselor: {exc}")]


@admin.register(ProductCreativeAsset)
class ProductCreativeAssetAdmin(admin.ModelAdmin):
    form = ProductCreativeAssetAdminForm
    list_display = (
        "product_id",
        "asset_type",
        "title",
        "sales_stage",
        "intent",
        "next_best_action",
        "priority",
        "is_active",
        "created_at",
    )

    list_filter = (
        "product_id",
        "asset_type",
        "sales_stage",
        "intent",
        "next_best_action",
        "is_active",
    )

    search_fields = (
        "product_id",
        "title",
        "description",
        "usage_context",
        "sales_stage",
        "intent",
        "next_best_action",
    )

    readonly_fields = (
        "asset_id",
        "public_url",
        "original_filename",
        "mime_type",
        "file_size_bytes",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        ("Product", {
            "fields": (
                "product_id",
                "asset_type",
                "title",
                "description",
                "file",
                "use_superchat_file",
                "superchat_file_id",
                "is_active",
                "priority",
            )
        }),
        ("Usage logic", {
            "fields": (
                "usage_context",
                "sales_stage",
                "intent",
                "next_best_action",
                "tags",
            )
        }),
        ("Generated file info", {
            "fields": (
                "public_url",
                "original_filename",
                "mime_type",
                "file_size_bytes",
            )
        }),
        ("System", {
            "fields": (
                "asset_id",
                "metadata",
                "created_at",
                "updated_at",
            )
        }),
    )

    def save_model(self, request, obj, form, change):
        uploaded = form.cleaned_data.get("file")

        if uploaded:
            obj.original_filename = uploaded.name
            obj.file_size_bytes = uploaded.size

            content_type = getattr(uploaded.file, "content_type", None)
            if not content_type:
                content_type = getattr(uploaded, "content_type", None)

            obj.mime_type = content_type or ""

            if not obj.asset_type:
                if obj.mime_type.startswith("image/"):
                    obj.asset_type = "image"
                elif obj.mime_type.startswith("video/"):
                    obj.asset_type = "video"
                else:
                    obj.asset_type = "document"

        super().save_model(request, obj, form, change)

        if obj.file and not obj.public_url:
            base_url = (
                getattr(settings, "PUBLIC_BASE_URL", None)
                or getattr(settings, "SITE_URL", None)
                or "https://storwyz.com"
            ).rstrip("/")

            obj.public_url = base_url + obj.file.url

            ProductCreativeAsset.objects.filter(asset_id=obj.asset_id).update(
                public_url=obj.public_url
            )


# ===== AI PROCESS / LLM LOGS ADMIN =====

import json
from django.contrib import admin
from django.utils.html import format_html

from .models import AiLlmCallLog, AiResponseProcessRun, AiResponseProcessStep


def pretty_json(value):
    if value is None:
        return ""

    try:
        formatted = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        formatted = str(value)

    return format_html(
        '<pre style="white-space: pre-wrap; max-width: 1200px; font-size: 12px;">{}</pre>',
        formatted
    )


class AiResponseProcessStepInline(admin.TabularInline):
    model = AiResponseProcessStep
    extra = 0
    can_delete = False
    readonly_fields = (
        "created_at",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "fail_reasons",
        "blocking_issues",
        "feedback_for_repair",
    )

    fields = (
        "created_at",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "fail_reasons",
        "blocking_issues",
        "feedback_for_repair",
    )

    ordering = ("created_at",)


@admin.register(AiResponseProcessRun)
class AiResponseProcessRunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "product_id",
        "status",
        "final_action",
        "final_score",
        "attempts_count",
        "short_final_body",
    )
    list_filter = ("status", "final_action", "product_id", "created_at")
    search_fields = ("conversation_id", "product_id", "client_message", "final_body", "error")
    readonly_fields = (
        "run_id",
        "conversation_id",
        "product_id",
        "client_message",
        "status",
        "final_action",
        "final_score",
        "final_body",
        "pretty_final_buttons",
        "attempts_count",
        "error",
        "created_at",
        "finished_at",
    )
    fields = (
        "run_id",
        "created_at",
        "finished_at",
        "conversation_id",
        "product_id",
        "client_message",
        "status",
        "final_action",
        "final_score",
        "attempts_count",
        "final_body",
        "pretty_final_buttons",
        "error",
    )
    inlines = [AiResponseProcessStepInline]
    ordering = ("-created_at",)

    def short_final_body(self, obj):
        text = obj.final_body or ""
        return text[:120] + ("..." if len(text) > 120 else "")

    short_final_body.short_description = "Final body"

    def pretty_final_buttons(self, obj):
        return pretty_json(obj.final_buttons)

    pretty_final_buttons.short_description = "Final buttons"


@admin.register(AiResponseProcessStep)
class AiResponseProcessStepAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "short_feedback",
    )
    list_filter = ("step_name", "approved", "severity", "action", "created_at")
    search_fields = (
        "conversation_id",
        "product_id",
        "step_name",
        "feedback_for_repair",
    )
    readonly_fields = (
        "step_id",
        "run",
        "conversation_id",
        "product_id",
        "step_name",
        "attempt",
        "pretty_input_json",
        "pretty_output_json",
        "approved",
        "score",
        "severity",
        "action",
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "created_at",
    )
    fields = (
        "step_id",
        "run",
        "created_at",
        "conversation_id",
        "product_id",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "pretty_input_json",
        "pretty_output_json",
    )
    ordering = ("-created_at",)

    def short_feedback(self, obj):
        text = obj.feedback_for_repair or ""
        return text[:120] + ("..." if len(text) > 120 else "")

    short_feedback.short_description = "Feedback"

    def pretty_input_json(self, obj):
        return pretty_json(obj.input_json)

    def pretty_output_json(self, obj):
        return pretty_json(obj.output_json)

    def pretty_fail_reasons(self, obj):
        return pretty_json(obj.fail_reasons)

    def pretty_blocking_issues(self, obj):
        return pretty_json(obj.blocking_issues)

    pretty_input_json.short_description = "Input JSON"
    pretty_output_json.short_description = "Output JSON"
    pretty_fail_reasons.short_description = "Fail reasons"
    pretty_blocking_issues.short_description = "Blocking issues"


@admin.register(AiLlmCallLog)
class AiLlmCallLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "product_id",
        "purpose",
        "model",
        "response_status",
        "duration_ms",
        "has_error",
    )
    list_filter = ("purpose", "model", "response_status", "product_id", "created_at")
    search_fields = (
        "conversation_id",
        "product_id",
        "purpose",
        "prompt_text",
        "output_text",
        "error",
    )
    readonly_fields = (
        "call_id",
        "created_at",
        "conversation_id",
        "product_id",
        "provider",
        "model",
        "purpose",
        "response_status",
        "duration_ms",
        "error",
        "prompt_text",
        "output_text",
        "pretty_request_json",
        "pretty_raw_response",
    )
    fields = (
        "call_id",
        "created_at",
        "conversation_id",
        "product_id",
        "provider",
        "model",
        "purpose",
        "response_status",
        "duration_ms",
        "error",
        "prompt_text",
        "output_text",
        "pretty_request_json",
        "pretty_raw_response",
    )
    ordering = ("-created_at",)

    def has_error(self, obj):
        return bool(obj.error)

    has_error.boolean = True
    has_error.short_description = "Error"

    def pretty_request_json(self, obj):
        return pretty_json(obj.request_json)

    def pretty_raw_response(self, obj):
        return pretty_json(obj.raw_response)

    pretty_request_json.short_description = "Request JSON"
    pretty_raw_response.short_description = "Raw response"

# ===== ENHANCED AI DEBUG ADMIN VIEW =====

from django.urls import reverse
from django.utils.safestring import mark_safe


def ai_debug_pretty_json(value):
    if value is None:
        return ""

    try:
        import json
        formatted = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        formatted = str(value)

    return format_html(
        '<pre style="white-space: pre-wrap; word-break: break-word; '
        'max-width: 1400px; max-height: 700px; overflow:auto; '
        'font-size: 12px; background:#111; color:#eee; padding:12px; '
        'border-radius:6px;">{}</pre>',
        formatted
    )


def ai_debug_pretty_text(value):
    if not value:
        return ""

    return format_html(
        '<pre style="white-space: pre-wrap; word-break: break-word; '
        'max-width: 1400px; max-height: 800px; overflow:auto; '
        'font-size: 12px; background:#111; color:#eee; padding:12px; '
        'border-radius:6px;">{}</pre>',
        value
    )


def ai_debug_short(value, limit=160):
    text = str(value or "")
    return text[:limit] + ("..." if len(text) > limit else "")


class EnhancedAiResponseProcessStepInline(admin.StackedInline):
    model = AiResponseProcessStep
    extra = 0
    can_delete = False
    show_change_link = True

    readonly_fields = (
        "created_at",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "pretty_input_json",
        "pretty_output_json",
    )

    fields = (
        ("created_at", "step_name", "attempt"),
        ("approved", "score", "severity", "action"),
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "pretty_input_json",
        "pretty_output_json",
    )

    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def pretty_input_json(self, obj):
        return ai_debug_pretty_json(obj.input_json)

    def pretty_output_json(self, obj):
        return ai_debug_pretty_json(obj.output_json)

    def pretty_fail_reasons(self, obj):
        return ai_debug_pretty_json(obj.fail_reasons)

    def pretty_blocking_issues(self, obj):
        return ai_debug_pretty_json(obj.blocking_issues)

    pretty_input_json.short_description = "Input JSON"
    pretty_output_json.short_description = "Output JSON"
    pretty_fail_reasons.short_description = "Fail reasons"
    pretty_blocking_issues.short_description = "Blocking issues"


class EnhancedAiResponseProcessRunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "product_id",
        "status",
        "final_action",
        "final_score",
        "attempts_count",
        "short_client_message",
        "short_final_body",
    )
    list_filter = ("status", "final_action", "product_id", "created_at")
    search_fields = (
        "conversation_id",
        "product_id",
        "client_message",
        "final_body",
        "error",
    )

    readonly_fields = (
        "run_id",
        "conversation_id",
        "product_id",
        "client_message",
        "status",
        "final_action",
        "final_score",
        "pretty_final_body",
        "pretty_final_buttons",
        "attempts_count",
        "error",
        "created_at",
        "finished_at",
    )

    fields = (
        ("run_id", "created_at", "finished_at"),
        ("conversation_id", "product_id"),
        "client_message",
        ("status", "final_action", "final_score", "attempts_count"),
        "pretty_final_body",
        "pretty_final_buttons",
        "error",
    )

    inlines = [EnhancedAiResponseProcessStepInline]
    ordering = ("-created_at",)

    def short_client_message(self, obj):
        return ai_debug_short(obj.client_message)

    def short_final_body(self, obj):
        return ai_debug_short(obj.final_body)

    def pretty_final_body(self, obj):
        return ai_debug_pretty_text(obj.final_body)

    def pretty_final_buttons(self, obj):
        return ai_debug_pretty_json(obj.final_buttons)

    short_client_message.short_description = "Client message"
    short_final_body.short_description = "Final body"
    pretty_final_body.short_description = "Final body"
    pretty_final_buttons.short_description = "Final buttons"


class EnhancedAiResponseProcessStepAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "short_feedback",
    )
    list_filter = ("step_name", "approved", "severity", "action", "created_at")
    search_fields = (
        "conversation_id",
        "product_id",
        "step_name",
        "feedback_for_repair",
    )

    readonly_fields = (
        "step_id",
        "run",
        "conversation_id",
        "product_id",
        "step_name",
        "attempt",
        "approved",
        "score",
        "severity",
        "action",
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "pretty_input_json",
        "pretty_output_json",
        "created_at",
    )

    fields = (
        ("step_id", "run"),
        ("created_at", "conversation_id", "product_id"),
        ("step_name", "attempt", "approved", "score"),
        ("severity", "action"),
        "pretty_fail_reasons",
        "pretty_blocking_issues",
        "feedback_for_repair",
        "pretty_input_json",
        "pretty_output_json",
    )

    ordering = ("-created_at",)

    def short_feedback(self, obj):
        return ai_debug_short(obj.feedback_for_repair)

    def pretty_input_json(self, obj):
        return ai_debug_pretty_json(obj.input_json)

    def pretty_output_json(self, obj):
        return ai_debug_pretty_json(obj.output_json)

    def pretty_fail_reasons(self, obj):
        return ai_debug_pretty_json(obj.fail_reasons)

    def pretty_blocking_issues(self, obj):
        return ai_debug_pretty_json(obj.blocking_issues)

    short_feedback.short_description = "Feedback"
    pretty_input_json.short_description = "Input JSON"
    pretty_output_json.short_description = "Output JSON"
    pretty_fail_reasons.short_description = "Fail reasons"
    pretty_blocking_issues.short_description = "Blocking issues"


class EnhancedAiLlmCallLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "product_id",
        "purpose",
        "model",
        "response_status",
        "duration_ms",
        "has_error",
        "short_output",
    )
    list_filter = ("purpose", "model", "response_status", "product_id", "created_at")
    search_fields = (
        "conversation_id",
        "product_id",
        "purpose",
        "prompt_text",
        "output_text",
        "error",
    )

    readonly_fields = (
        "call_id",
        "created_at",
        "conversation_id",
        "product_id",
        "provider",
        "model",
        "purpose",
        "response_status",
        "duration_ms",
        "error",
        "pretty_prompt_text",
        "pretty_output_text",
        "pretty_request_json",
        "pretty_raw_response",
    )

    fields = (
        ("call_id", "created_at"),
        ("conversation_id", "product_id"),
        ("provider", "model", "purpose"),
        ("response_status", "duration_ms"),
        "error",
        "pretty_prompt_text",
        "pretty_output_text",
        "pretty_request_json",
        "pretty_raw_response",
    )

    ordering = ("-created_at",)

    def has_error(self, obj):
        return bool(obj.error)

    def short_output(self, obj):
        return ai_debug_short(obj.output_text)

    def pretty_prompt_text(self, obj):
        return ai_debug_pretty_text(obj.prompt_text)

    def pretty_output_text(self, obj):
        return ai_debug_pretty_text(obj.output_text)

    def pretty_request_json(self, obj):
        return ai_debug_pretty_json(obj.request_json)

    def pretty_raw_response(self, obj):
        return ai_debug_pretty_json(obj.raw_response)

    has_error.boolean = True
    has_error.short_description = "Error"
    short_output.short_description = "Output preview"
    pretty_prompt_text.short_description = "Prompt sent to LLM"
    pretty_output_text.short_description = "LLM output text"
    pretty_request_json.short_description = "Request JSON"
    pretty_raw_response.short_description = "Raw response"


# Re-register admin views with enhanced content display.
for model in [AiResponseProcessRun, AiResponseProcessStep, AiLlmCallLog]:
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass

admin.site.register(AiResponseProcessRun, EnhancedAiResponseProcessRunAdmin)
admin.site.register(AiResponseProcessStep, EnhancedAiResponseProcessStepAdmin)
admin.site.register(AiLlmCallLog, EnhancedAiLlmCallLogAdmin)


# ===== SINGLE AI DECISION ROADMAP ADMIN =====

import json
from html import escape

from django.contrib import admin
from django.db import connection
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import (
    AiDecisionRoadmap,
    AiResponseProcessRun,
    AiResponseProcessStep,
    AiLlmCallLog,
)


def roadmap_dictfetchall(cursor):
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def roadmap_load_json(value):
    if value is None:
        return None

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except Exception:
            return value

    return value


def roadmap_is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def roadmap_compact(value):
    value = roadmap_load_json(value)

    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            compacted = roadmap_compact(v)
            if not roadmap_is_empty(compacted):
                result[k] = compacted
        return result

    if isinstance(value, list):
        result = []
        for item in value:
            compacted = roadmap_compact(item)
            if not roadmap_is_empty(compacted):
                result.append(compacted)
        return result

    return value


def roadmap_json(value):
    value = roadmap_compact(value)
    if roadmap_is_empty(value):
        return ""

    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(value)


def roadmap_pre_text(value, max_height=520):
    if roadmap_is_empty(value):
        return ""

    return (
        f'<pre style="white-space:pre-wrap;word-break:break-word;'
        f'max-height:{max_height}px;overflow:auto;'
        f'background:#111;color:#eee;padding:12px;border-radius:8px;'
        f'font-size:12px;line-height:1.45;">'
        f'{escape(str(value))}'
        f'</pre>'
    )


def roadmap_pre_json(value, max_height=520):
    text = roadmap_json(value)
    if not text:
        return ""

    return roadmap_pre_text(text, max_height=max_height)


def roadmap_card(title, body, border="#ddd"):
    return (
        f'<div style="border:1px solid {border};border-radius:10px;'
        f'padding:14px;margin:12px 0;background:#fff;">'
        f'<h3 style="margin:0 0 10px 0;">{escape(str(title))}</h3>'
        f'{body}'
        f'</div>'
    )


def roadmap_chip(text, bg="#eef", color="#222"):
    if roadmap_is_empty(text):
        return ""
    return (
        f'<span style="display:inline-block;padding:3px 8px;'
        f'border-radius:999px;background:{bg};color:{color};'
        f'font-size:12px;margin:2px;">{escape(str(text))}</span>'
    )


def roadmap_details(title, content, open_default=False):
    if roadmap_is_empty(content):
        return ""

    open_attr = " open" if open_default else ""

    return (
        f'<details{open_attr} style="margin:8px 0;">'
        f'<summary style="cursor:pointer;font-weight:600;">{escape(str(title))}</summary>'
        f'{content}'
        f'</details>'
    )


def roadmap_short(value, limit=130):
    text = str(value or "")
    return text[:limit] + ("..." if len(text) > limit else "")


class AiDecisionRoadmapAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "conversation_id",
        "product_id",
        "status",
        "final_action",
        "final_score",
        "attempts_count",
        "short_client_message",
        "short_final_body",
    )

    list_filter = (
        "status",
        "final_action",
        "product_id",
        "created_at",
    )

    search_fields = (
        "conversation_id",
        "product_id",
        "client_message",
        "final_body",
        "error",
    )

    readonly_fields = (
        "run_id",
        "created_at",
        "finished_at",
        "conversation_id",
        "product_id",
        "status",
        "final_action",
        "final_score",
        "attempts_count",
        "client_message",
        "roadmap_view",
    )

    fields = (
        ("run_id", "created_at", "finished_at"),
        ("conversation_id", "product_id"),
        ("status", "final_action", "final_score", "attempts_count"),
        "client_message",
        "roadmap_view",
    )

    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def short_client_message(self, obj):
        return roadmap_short(obj.client_message)

    def short_final_body(self, obj):
        return roadmap_short(obj.final_body)

    short_client_message.short_description = "Client message"
    short_final_body.short_description = "Final body"

    def roadmap_view(self, obj):
        if not obj:
            return ""

        conversation_id = obj.conversation_id
        run_id = str(obj.run_id)

        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    created_at,
                    role,
                    source,
                    message_text
                FROM ai_chat_transcript
                WHERE conversation_id = %s
                ORDER BY created_at ASC
            """, [conversation_id])
            messages = roadmap_dictfetchall(cur)

        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    run_id,
                    created_at,
                    finished_at,
                    client_message,
                    status,
                    final_action,
                    final_score,
                    attempts_count,
                    final_body,
                    final_buttons
                FROM ai_response_process_runs
                WHERE conversation_id = %s
                ORDER BY created_at ASC
            """, [conversation_id])
            all_runs = roadmap_dictfetchall(cur)

        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    created_at,
                    step_name,
                    attempt,
                    approved,
                    score,
                    severity,
                    action,
                    fail_reasons,
                    blocking_issues,
                    feedback_for_repair,
                    input_json,
                    output_json
                FROM ai_response_process_steps
                WHERE run_id = %s::uuid
                ORDER BY created_at ASC
            """, [run_id])
            steps = roadmap_dictfetchall(cur)

        with connection.cursor() as cur:
            cur.execute("""
                SELECT
                    created_at,
                    purpose,
                    model,
                    response_status,
                    duration_ms,
                    error,
                    prompt_text,
                    output_text,
                    request_json,
                    raw_response
                FROM ai_llm_call_logs
                WHERE conversation_id = %s
                  AND created_at >= (%s::timestamptz - interval '20 seconds')
                  AND created_at <= (
                        COALESCE(%s::timestamptz, %s::timestamptz + interval '10 minutes')
                        + interval '20 seconds'
                  )
                ORDER BY created_at ASC
            """, [conversation_id, obj.created_at, obj.finished_at, obj.created_at])
            llm_calls = roadmap_dictfetchall(cur)

        html = []

        html.append(
            '<div style="font-family:Arial,sans-serif;max-width:1500px;">'
        )

        # Header
        header = ""
        header += roadmap_chip(f"Status: {obj.status}", "#e8f0ff")
        header += roadmap_chip(f"Action: {obj.final_action}", "#eaf7ea")
        header += roadmap_chip(f"Score: {obj.final_score}", "#fff4d6")
        header += roadmap_chip(f"Attempts: {obj.attempts_count}", "#f2eaff")
        header += f"<p><b>Conversation:</b> {escape(str(conversation_id))}</p>"
        header += f"<p><b>Client message:</b></p>{roadmap_pre_text(obj.client_message, 180)}"
        header += f"<p><b>Final response sent:</b></p>{roadmap_pre_text(obj.final_body, 260)}"
        header += roadmap_details(
            "Final buttons",
            roadmap_pre_json(obj.final_buttons, 220),
            open_default=False,
        )

        html.append(roadmap_card("1. Mesajul procesat și decizia finală", header, "#b7d4ff"))

        # Full conversation history
        msg_rows = []
        for m in messages:
            role = m.get("role")
            bg = "#f7f7f7"
            if role == "client":
                bg = "#eaf7ff"
            elif role == "ai":
                bg = "#eafbea"
            elif role == "system":
                bg = "#fff4d6"

            msg_rows.append(
                f'<div style="background:{bg};border-radius:8px;padding:10px;margin:8px 0;">'
                f'<div>'
                f'{roadmap_chip(role, "#fff")}'
                f'{roadmap_chip(m.get("source"), "#fff")}'
                f'<span style="color:#666;font-size:12px;">{escape(str(m.get("created_at")))}</span>'
                f'</div>'
                f'<div style="margin-top:6px;white-space:pre-wrap;">{escape(str(m.get("message_text") or ""))}</div>'
                f'</div>'
            )

        html.append(
            roadmap_card(
                "2. Istoria tuturor mesajelor din conversație",
                "".join(msg_rows) if msg_rows else "<p>Nu există transcript.</p>",
                "#cce7cc",
            )
        )

        # All runs in this conversation
        runs_rows = []
        for r in all_runs:
            current = str(r.get("run_id")) == run_id
            try:
                url = reverse("admin:superchatsync_aidecisionroadmap_change", args=[r.get("run_id")])
                link = f'<a href="{escape(url)}">deschide roadmap</a>'
            except Exception:
                link = ""

            runs_rows.append(
                f'<tr style="background:{"#fff7d6" if current else "#fff"};">'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{escape(str(r.get("created_at")))}</td>'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{escape(str(r.get("status") or ""))}</td>'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{escape(str(r.get("final_score") or ""))}</td>'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{escape(roadmap_short(r.get("client_message"), 90))}</td>'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{escape(roadmap_short(r.get("final_body"), 90))}</td>'
                f'<td style="padding:6px;border-bottom:1px solid #eee;">{link}</td>'
                f'</tr>'
            )

        runs_table = (
            '<table style="border-collapse:collapse;width:100%;font-size:13px;">'
            '<thead><tr>'
            '<th style="text-align:left;padding:6px;">Time</th>'
            '<th style="text-align:left;padding:6px;">Status</th>'
            '<th style="text-align:left;padding:6px;">Score</th>'
            '<th style="text-align:left;padding:6px;">Client</th>'
            '<th style="text-align:left;padding:6px;">AI final</th>'
            '<th style="text-align:left;padding:6px;">Link</th>'
            '</tr></thead>'
            '<tbody>'
            + "".join(runs_rows)
            + '</tbody></table>'
        )

        html.append(
            roadmap_card(
                "3. Istoria tuturor deciziilor AI pentru această conversație",
                runs_table if runs_rows else "<p>Nu există alte run-uri.</p>",
                "#d8c9ff",
            )
        )

        # Decision roadmap for current run
        step_cards = []
        for idx, s in enumerate(steps, start=1):
            approved = s.get("approved")
            border = "#ddd"
            if approved is True:
                border = "#7fd47f"
            elif approved is False:
                border = "#ff9f9f"

            body = ""
            body += roadmap_chip(f"#{idx}", "#eee")
            body += roadmap_chip(f"attempt {s.get('attempt')}", "#eee")
            body += roadmap_chip(f"approved: {approved}", "#e8f0ff")
            body += roadmap_chip(f"score: {s.get('score')}", "#fff4d6")
            body += roadmap_chip(f"severity: {s.get('severity')}", "#f2eaff")
            body += roadmap_chip(f"action: {s.get('action')}", "#eaf7ea")
            body += f'<p style="color:#666;font-size:12px;">{escape(str(s.get("created_at")))}</p>'

            if s.get("feedback_for_repair"):
                body += "<h4>Feedback pentru repair</h4>"
                body += roadmap_pre_text(s.get("feedback_for_repair"), 220)

            body += roadmap_details(
                "Fail reasons",
                roadmap_pre_json(s.get("fail_reasons"), 260),
                open_default=bool(s.get("fail_reasons")),
            )
            body += roadmap_details(
                "Blocking issues",
                roadmap_pre_json(s.get("blocking_issues"), 260),
                open_default=bool(s.get("blocking_issues")),
            )
            body += roadmap_details(
                "Input JSON",
                roadmap_pre_json(s.get("input_json"), 500),
                open_default=False,
            )
            body += roadmap_details(
                "Output JSON",
                roadmap_pre_json(s.get("output_json"), 700),
                open_default=s.get("step_name") in ["candidate_generated", "combined_validation", "repair_generated", "fallback_selected"],
            )

            step_cards.append(
                roadmap_card(
                    f"Pas {idx}: {s.get('step_name')}",
                    body,
                    border,
                )
            )

        html.append(
            roadmap_card(
                "4. Roadmap-ul decizional pentru mesajul selectat",
                "".join(step_cards) if step_cards else "<p>Nu există pași de proces.</p>",
                "#ffd6aa",
            )
        )

        # LLM calls for this run
        llm_cards = []
        for idx, call in enumerate(llm_calls, start=1):
            border = "#ddd"
            if call.get("error"):
                border = "#ff9f9f"
            elif call.get("response_status") and int(call.get("response_status")) >= 200 and int(call.get("response_status")) < 300:
                border = "#7fd47f"

            body = ""
            body += roadmap_chip(f"#{idx}", "#eee")
            body += roadmap_chip(call.get("purpose"), "#e8f0ff")
            body += roadmap_chip(call.get("model"), "#f2eaff")
            body += roadmap_chip(f"status {call.get('response_status')}", "#fff4d6")
            body += roadmap_chip(f"{call.get('duration_ms')} ms", "#eaf7ea")
            body += f'<p style="color:#666;font-size:12px;">{escape(str(call.get("created_at")))}</p>'

            if call.get("error"):
                body += "<h4>Error</h4>"
                body += roadmap_pre_text(call.get("error"), 220)

            body += roadmap_details(
                "Prompt trimis la LLM",
                roadmap_pre_text(call.get("prompt_text"), 900),
                open_default=False,
            )
            body += roadmap_details(
                "Răspuns text primit de la LLM",
                roadmap_pre_text(call.get("output_text"), 700),
                open_default=True,
            )
            body += roadmap_details(
                "Request JSON",
                roadmap_pre_json(call.get("request_json"), 500),
                open_default=False,
            )
            body += roadmap_details(
                "Raw response",
                roadmap_pre_json(call.get("raw_response"), 700),
                open_default=False,
            )

            llm_cards.append(
                roadmap_card(
                    f"LLM call {idx}: {call.get('purpose')}",
                    body,
                    border,
                )
            )

        html.append(
            roadmap_card(
                "5. Prompturi și răspunsuri LLM folosite în acest proces",
                "".join(llm_cards) if llm_cards else "<p>Nu există chemări LLM asociate acestui run.</p>",
                "#b7e4ff",
            )
        )

        html.append("</div>")

        return mark_safe("".join(html))

    roadmap_view.short_description = "Decision Roadmap"


# Hide old technical menus and keep only one roadmap menu.
for model in [AiResponseProcessRun, AiResponseProcessStep, AiLlmCallLog, AiDecisionRoadmap]:
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass

admin.site.register(AiDecisionRoadmap, AiDecisionRoadmapAdmin)



# ===== FINAL ADMIN: ONLY AI DECISION ROADMAP MENU =====

from django.http import HttpResponseRedirect

try:
    from .models import AiDecisionRoadmap, AiResponseProcessRun, AiResponseProcessStep, AiLlmCallLog

    for _m in [AiResponseProcessRun, AiResponseProcessStep, AiLlmCallLog, AiDecisionRoadmap]:
        try:
            admin.site.unregister(_m)
        except admin.sites.NotRegistered:
            pass

    class AiDecisionRoadmapRedirectAdmin(admin.ModelAdmin):
        def has_add_permission(self, request):
            return False

        def changelist_view(self, request, extra_context=None):
            return HttpResponseRedirect("/ai-debug/roadmap/")

    admin.site.register(AiDecisionRoadmap, AiDecisionRoadmapRedirectAdmin)

except Exception as _roadmap_admin_error:
    pass


# ===== BUSINESS CLIENT KNOWLEDGE ADMIN =====

from .models import (
    BusinessClient,
    BusinessCrawlPage,
    BusinessKnowledgeImportRun,
    BusinessKnowledgeItem,
    BusinessMediaAsset,
    BusinessProduct,
    BusinessProductRanking,
    KnowledgeCenterLink,
    ShortLink,
    ShortLinkClick,
    WhatsappAgentInboxRoute,
)
from django.conf import settings
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse


def _kc_count(queryset):
    try:
        return queryset.count()
    except Exception:
        return 0


def _kc_admin_url(name, *args):
    try:
        return reverse(f"admin:{name}", args=args)
    except Exception:
        return "#"


def _kc_status_counts(queryset, field="status"):
    try:
        return {
            str(row[field] or "empty"): row["count"]
            for row in queryset.values(field).annotate(count=Count("pk"))
        }
    except Exception:
        return {}


@admin.register(KnowledgeCenterLink)
class KnowledgeCenterAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "title": "Knowledge Center",
                **self._build_context(request),
            }
        )
        return TemplateResponse(
            request,
            "admin/superchatsync/knowledge_center.html",
            context,
        )

    def _build_context(self, request):
        product_imports = ProductKnowledgeImport.objects.all()
        product_items = ProductKnowledgeItem.objects.all()
        creative_assets = ProductCreativeAsset.objects.all()
        business_clients = BusinessClient.objects.all()
        business_products = BusinessProduct.objects.all()
        business_knowledge = BusinessKnowledgeItem.objects.all()
        business_media = BusinessMediaAsset.objects.all()
        business_runs = BusinessKnowledgeImportRun.objects.all()
        suggestions = ProductFeedSuggestion.objects.all()

        import_status = _kc_status_counts(product_imports)
        item_status = _kc_status_counts(product_items)
        asset_status = _kc_status_counts(creative_assets, "is_active")
        business_product_status = _kc_status_counts(business_products)
        business_knowledge_status = _kc_status_counts(business_knowledge)
        business_media_status = _kc_status_counts(business_media)
        suggestion_status = _kc_status_counts(suggestions)

        pending_product_items = item_status.get("pending_review", 0) + item_status.get("pending", 0)
        approved_unapplied_product_items = _kc_count(
            product_items.filter(status="approved", applied_at__isnull=True)
        )
        draft_business_knowledge = business_knowledge_status.get("draft", 0)
        draft_business_media = business_media_status.get("draft", 0)
        draft_business_products = business_product_status.get("draft", 0)
        pending_suggestions = suggestion_status.get("pending_review", 0) + suggestion_status.get("pending", 0)

        metrics = [
            {
                "label": "Product docs",
                "value": _kc_count(product_imports),
                "hint": f"{import_status.get('uploaded', 0)} uploaded · {import_status.get('queued', 0)} queued",
                "href": _kc_admin_url("superchatsync_productknowledgeimport_changelist"),
            },
            {
                "label": "Extracted items",
                "value": _kc_count(product_items),
                "hint": f"{pending_product_items} need review · {approved_unapplied_product_items} approved unapplied",
                "href": _kc_admin_url("superchatsync_productknowledgeitem_changelist"),
            },
            {
                "label": "Business clients",
                "value": _kc_count(business_clients),
                "hint": f"{_kc_count(business_clients.filter(status='active'))} active",
                "href": _kc_admin_url("superchatsync_businessclient_changelist"),
            },
            {
                "label": "Business knowledge",
                "value": _kc_count(business_knowledge),
                "hint": f"{draft_business_knowledge} draft · {business_knowledge_status.get('approved', 0)} approved",
                "href": _kc_admin_url("superchatsync_businessknowledgeitem_changelist"),
            },
            {
                "label": "Media assets",
                "value": _kc_count(business_media) + _kc_count(creative_assets),
                "hint": f"{draft_business_media} business draft · {asset_status.get('True', 0)} product active",
                "href": _kc_admin_url("superchatsync_businessmediaasset_changelist"),
            },
            {
                "label": "Knowledge gaps",
                "value": pending_suggestions,
                "hint": "suggestions from conversation analysis",
                "href": _kc_admin_url("superchatsync_productfeedsuggestion_changelist"),
            },
        ]

        next_actions = []
        if pending_product_items:
            next_actions.append(
                {
                    "title": "Review extracted product knowledge",
                    "body": f"{pending_product_items} Fitexpress/product knowledge items need approval.",
                    "href": _kc_admin_url("superchatsync_productknowledgeitem_changelist") + "?status__exact=pending_review",
                    "label": "Review items",
                    "priority": "high",
                }
            )
        if approved_unapplied_product_items:
            next_actions.append(
                {
                    "title": "Apply approved product knowledge",
                    "body": f"{approved_unapplied_product_items} approved items are not yet applied to the agent knowledge base.",
                    "href": _kc_admin_url("superchatsync_productknowledgeitem_changelist") + "?status__exact=approved",
                    "label": "Apply approved",
                    "priority": "high",
                }
            )
        if draft_business_knowledge:
            next_actions.append(
                {
                    "title": "Approve business knowledge",
                    "body": f"{draft_business_knowledge} business knowledge items are still draft.",
                    "href": _kc_admin_url("superchatsync_businessknowledgeitem_changelist") + "?status__exact=draft",
                    "label": "Open drafts",
                    "priority": "medium",
                }
            )
        if draft_business_media:
            next_actions.append(
                {
                    "title": "Approve business media",
                    "body": f"{draft_business_media} product images/media assets are waiting for review.",
                    "href": _kc_admin_url("superchatsync_businessmediaasset_changelist") + "?status__exact=draft",
                    "label": "Review media",
                    "priority": "medium",
                }
            )
        if draft_business_products:
            next_actions.append(
                {
                    "title": "Activate imported business products",
                    "body": f"{draft_business_products} imported products are still draft.",
                    "href": _kc_admin_url("superchatsync_businessproduct_changelist") + "?status__exact=draft",
                    "label": "Open products",
                    "priority": "medium",
                }
            )
        if pending_suggestions:
            next_actions.append(
                {
                    "title": "Review conversation-derived knowledge gaps",
                    "body": f"{pending_suggestions} suggestions came from conversation analysis.",
                    "href": _kc_admin_url("superchatsync_productfeedsuggestion_changelist") + "?status__exact=pending_review",
                    "label": "Open suggestions",
                    "priority": "low",
                }
            )
        if not next_actions:
            next_actions.append(
                {
                    "title": "Knowledge queues are clear",
                    "body": "No urgent review queue is visible right now.",
                    "href": _kc_admin_url("superchatsync_businessclient_changelist"),
                    "label": "Open clients",
                    "priority": "low",
                }
            )

        business_cards = []
        for business in business_clients.order_by("name")[:12]:
            business_cards.append(
                {
                    "business": business,
                    "review_url": _kc_admin_url("superchatsync_businessclient_knowledge_review", business.pk),
                    "products": _kc_count(BusinessProduct.objects.filter(business=business)),
                    "active_products": _kc_count(BusinessProduct.objects.filter(business=business, status="active")),
                    "knowledge": _kc_count(BusinessKnowledgeItem.objects.filter(business=business)),
                    "draft_knowledge": _kc_count(BusinessKnowledgeItem.objects.filter(business=business, status="draft")),
                    "media": _kc_count(BusinessMediaAsset.objects.filter(business=business)),
                    "draft_media": _kc_count(BusinessMediaAsset.objects.filter(business=business, status="draft")),
                    "runs": _kc_count(BusinessKnowledgeImportRun.objects.filter(business=business)),
                }
            )

        workflows = [
            {
                "title": "Product document knowledge",
                "body": "Upload product docs, extract structured knowledge, review uncertain items, then apply approved facts/rules to the agent.",
                "steps": ["Upload", "Extract", "Review", "Apply"],
                "primary": {
                    "label": "Upload document",
                    "href": _kc_admin_url("superchatsync_productknowledgeimport_add"),
                },
                "secondary": [
                    {"label": "Imports", "href": _kc_admin_url("superchatsync_productknowledgeimport_changelist")},
                    {"label": "Extracted items", "href": _kc_admin_url("superchatsync_productknowledgeitem_changelist")},
                    {"label": "Creative assets", "href": _kc_admin_url("superchatsync_productcreativeasset_changelist")},
                ],
            },
            {
                "title": "Business website knowledge",
                "body": "For clients like Peeko: crawl/import products, review product facts and images, activate usable catalog data.",
                "steps": ["Import", "Review", "Approve", "Use in agent"],
                "primary": {
                    "label": "Business clients",
                    "href": _kc_admin_url("superchatsync_businessclient_changelist"),
                },
                "secondary": [
                    {"label": "Products", "href": _kc_admin_url("superchatsync_businessproduct_changelist")},
                    {"label": "Knowledge", "href": _kc_admin_url("superchatsync_businessknowledgeitem_changelist")},
                    {"label": "Media", "href": _kc_admin_url("superchatsync_businessmediaasset_changelist")},
                    {"label": "Import runs", "href": _kc_admin_url("superchatsync_businessknowledgeimportrun_changelist")},
                ],
            },
            {
                "title": "Conversation learning loop",
                "body": "Use conversation analysis and AI roadmap to detect missing answers, convert gaps into knowledge, and improve the agent.",
                "steps": ["Analyze", "Suggest", "Approve", "Test"],
                "primary": {
                    "label": "AI roadmap",
                    "href": _kc_admin_url("superchatsync_aidecisionroadmap_changelist"),
                },
                "secondary": [
                    {"label": "Conversations", "href": _kc_admin_url("superchatsync_conversation_changelist")},
                    {"label": "Suggestions", "href": _kc_admin_url("superchatsync_productfeedsuggestion_changelist")},
                    {"label": "Agent routes", "href": _kc_admin_url("superchatsync_whatsappagentinboxroute_changelist")},
                ],
            },
            {
                "title": "Link and catalog assets",
                "body": "Manage shortlinks, click attribution, product catalog pages and creative links used in WhatsApp flows.",
                "steps": ["Create", "Send", "Track", "Optimize"],
                "primary": {
                    "label": "Shortlinks",
                    "href": "/shortlinks/",
                },
                "secondary": [
                    {"label": "ShortLink admin", "href": _kc_admin_url("superchatsync_shortlink_changelist")},
                    {"label": "Click logs", "href": _kc_admin_url("superchatsync_shortlinkclick_changelist")},
                    {"label": "Catalog admin", "href": "/catalog-admin/"},
                ],
            },
        ]

        recent_product_imports = product_imports.select_related("product").order_by("-created_at")[:8]
        recent_business_runs = business_runs.select_related("business").order_by("-started_at")[:8]

        technical_links = [
            {"label": "Product imports", "href": _kc_admin_url("superchatsync_productknowledgeimport_changelist")},
            {"label": "Product items", "href": _kc_admin_url("superchatsync_productknowledgeitem_changelist")},
            {"label": "Product creatives", "href": _kc_admin_url("superchatsync_productcreativeasset_changelist")},
            {"label": "Business clients", "href": _kc_admin_url("superchatsync_businessclient_changelist")},
            {"label": "Business products", "href": _kc_admin_url("superchatsync_businessproduct_changelist")},
            {"label": "Business knowledge", "href": _kc_admin_url("superchatsync_businessknowledgeitem_changelist")},
            {"label": "Business media", "href": _kc_admin_url("superchatsync_businessmediaasset_changelist")},
            {"label": "Crawl pages", "href": _kc_admin_url("superchatsync_businesscrawlpage_changelist")},
            {"label": "Import runs", "href": _kc_admin_url("superchatsync_businessknowledgeimportrun_changelist")},
            {"label": "Agent routes", "href": _kc_admin_url("superchatsync_whatsappagentinboxroute_changelist")},
        ]

        return {
            "metrics": metrics,
            "next_actions": next_actions,
            "business_cards": business_cards,
            "workflows": workflows,
            "recent_product_imports": recent_product_imports,
            "recent_business_runs": recent_business_runs,
            "technical_links": technical_links,
        }


@admin.register(BusinessClient)
class BusinessClientAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "domain", "default_language", "default_currency", "status", "review_workspace", "updated_at")
    list_filter = ("status", "default_language")
    search_fields = ("name", "slug", "domain")
    readonly_fields = ("business_id", "created_at", "updated_at")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/knowledge-review/",
                self.admin_site.admin_view(self.knowledge_review_view),
                name="superchatsync_businessclient_knowledge_review",
            ),
        ]
        return custom_urls + urls

    def review_workspace(self, obj):
        url = reverse("admin:superchatsync_businessclient_knowledge_review", args=[obj.pk])
        return format_html('<a class="button" href="{}">Review</a>', url)

    review_workspace.short_description = "Knowledge"

    def knowledge_review_view(self, request, object_id):
        business = self.get_object(request, object_id)
        if business is None:
            return HttpResponseRedirect(reverse("admin:superchatsync_businessclient_changelist"))
        if not self.has_view_or_change_permission(request, business):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied

        if request.method == "POST":
            self._handle_review_action(request, business)
            return HttpResponseRedirect(request.get_full_path())

        context = self._build_review_context(request, business)
        context.update(self.admin_site.each_context(request))
        context.update(
            {
                "opts": self.model._meta,
                "original": business,
                "title": f"{business.name} knowledge review",
            }
        )
        return TemplateResponse(request, "admin/superchatsync/businessclient/knowledge_review.html", context)

    def _handle_review_action(self, request, business):
        action = request.POST.get("action")
        now = timezone.now()
        reviewer = getattr(request.user, "get_username", lambda: "")() or str(request.user)

        if action in {"approve_knowledge", "reject_knowledge"}:
            ids = request.POST.getlist("knowledge_ids")
            if not ids:
                self.message_user(request, "Selectează cel puțin un knowledge item.", messages.WARNING)
                return
            status = "approved" if action == "approve_knowledge" else "rejected"
            updated = (
                BusinessKnowledgeItem.objects.filter(business=business, item_id__in=ids)
                .update(status=status, reviewed_by=reviewer, reviewed_at=now)
            )
            self.message_user(request, f"{updated} knowledge items au fost marcate {status}.", messages.SUCCESS)
            return

        if action in {"approve_media", "reject_media"}:
            ids = request.POST.getlist("media_ids")
            if not ids:
                self.message_user(request, "Selectează cel puțin un media asset.", messages.WARNING)
                return
            status = "approved" if action == "approve_media" else "rejected"
            updated = BusinessMediaAsset.objects.filter(business=business, asset_id__in=ids).update(status=status)
            self.message_user(request, f"{updated} media assets au fost marcate {status}.", messages.SUCCESS)
            return

        if action in {"activate_products", "archive_products"}:
            ids = request.POST.getlist("product_ids")
            if not ids:
                self.message_user(request, "Selectează cel puțin un produs.", messages.WARNING)
                return
            status = "active" if action == "activate_products" else "archived"
            updated = BusinessProduct.objects.filter(business=business, product_id__in=ids).update(status=status)
            self.message_user(request, f"{updated} produse au fost marcate {status}.", messages.SUCCESS)
            return

        self.message_user(request, "Acțiune necunoscută.", messages.ERROR)

    def _build_review_context(self, request, business):
        q = (request.GET.get("q") or "").strip()
        knowledge_status = request.GET.get("knowledge_status") or "draft"
        knowledge_type = request.GET.get("knowledge_type") or ""
        media_status = request.GET.get("media_status") or "draft"
        product_status = request.GET.get("product_status") or "draft"

        products = BusinessProduct.objects.filter(business=business).order_by("name")
        knowledge = BusinessKnowledgeItem.objects.filter(business=business).select_related("product", "page")
        media = BusinessMediaAsset.objects.filter(business=business).select_related("product")

        if q:
            product_filter = Q(name__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q) | Q(product_type__icontains=q)
            knowledge_filter = (
                Q(title__icontains=q)
                | Q(body__icontains=q)
                | Q(evidence__icontains=q)
                | Q(source_url__icontains=q)
                | Q(product__name__icontains=q)
            )
            media_filter = Q(title__icontains=q) | Q(alt_text__icontains=q) | Q(source_url__icontains=q) | Q(product__name__icontains=q)
            products = products.filter(product_filter)
            knowledge = knowledge.filter(knowledge_filter)
            media = media.filter(media_filter)

        if product_status:
            products = products.filter(status=product_status)
        if knowledge_status:
            knowledge = knowledge.filter(status=knowledge_status)
        if knowledge_type:
            knowledge = knowledge.filter(item_type=knowledge_type)
        if media_status:
            media = media.filter(status=media_status)

        general_knowledge = knowledge.filter(scope="general").order_by("item_type", "-confidence_score", "title")
        product_knowledge = knowledge.filter(scope="product").order_by("product__name", "item_type", "-confidence_score")
        media = media.order_by("product__name", "image_role", "title")
        media_page = self._paginate(request, media, "media_page", 48)

        return {
            "business": business,
            "filters": {
                "q": q,
                "knowledge_status": knowledge_status,
                "knowledge_type": knowledge_type,
                "media_status": media_status,
                "product_status": product_status,
            },
            "clear_url": reverse("admin:superchatsync_businessclient_knowledge_review", args=[business.pk]),
            "metrics": self._review_metrics(business),
            "status_options": ["draft", "approved", "rejected", "archived"],
            "product_status_options": ["draft", "active", "archived"],
            "knowledge_type_options": self._knowledge_type_options(business),
            "products_page": self._paginate(request, products, "products_page", 25),
            "general_knowledge_page": self._paginate(request, general_knowledge, "general_page", 40),
            "product_knowledge_page": self._paginate(request, product_knowledge, "product_knowledge_page", 40),
            "media_page": media_page,
            "media_cards": [self._media_card(asset) for asset in media_page.object_list],
            "admin_urls": self._review_admin_urls(),
        }

    def _review_metrics(self, business):
        knowledge_by_status = {
            row["status"]: row["c"]
            for row in BusinessKnowledgeItem.objects.filter(business=business).values("status").annotate(c=Count("item_id"))
        }
        media_by_status = {
            row["status"]: row["c"]
            for row in BusinessMediaAsset.objects.filter(business=business).values("status").annotate(c=Count("asset_id"))
        }
        product_by_status = {
            row["status"]: row["c"]
            for row in BusinessProduct.objects.filter(business=business).values("status").annotate(c=Count("product_id"))
        }
        return [
            {"label": "Products", "value": BusinessProduct.objects.filter(business=business).count()},
            {"label": "Draft products", "value": product_by_status.get("draft", 0)},
            {"label": "Knowledge", "value": BusinessKnowledgeItem.objects.filter(business=business).count()},
            {"label": "Draft knowledge", "value": knowledge_by_status.get("draft", 0)},
            {"label": "Media", "value": BusinessMediaAsset.objects.filter(business=business).count()},
            {"label": "Draft media", "value": media_by_status.get("draft", 0)},
        ]

    def _paginate(self, request, queryset, page_param, per_page):
        paginator = Paginator(queryset, per_page)
        return paginator.get_page(request.GET.get(page_param))

    def _knowledge_type_options(self, business):
        labels = dict(BusinessKnowledgeItem.TYPE_CHOICES)
        values = (
            BusinessKnowledgeItem.objects.filter(business=business)
            .values_list("item_type", flat=True)
            .distinct()
            .order_by("item_type")
        )
        return [(value, labels.get(value, value.replace("_", " ").title())) for value in values]

    def _media_card(self, asset):
        return {
            "asset": asset,
            "image_url": self._media_public_url(asset),
            "change_url": reverse("admin:superchatsync_businessmediaasset_change", args=[asset.pk]),
            "product_url": reverse("admin:superchatsync_businessproduct_change", args=[asset.product_id]) if asset.product_id else "",
        }

    def _media_public_url(self, asset):
        local_path = asset.local_path or ""
        media_root = str(getattr(settings, "MEDIA_ROOT", "") or "")
        media_url = str(getattr(settings, "MEDIA_URL", "/media/") or "/media/")
        if local_path and media_root and local_path.startswith(media_root):
            relative_path = os.path.relpath(local_path, media_root).replace(os.sep, "/")
            return f"{media_url.rstrip('/')}/{relative_path}"
        return asset.source_url

    def _review_admin_urls(self):
        return {
            "products": reverse("admin:superchatsync_businessproduct_changelist"),
            "knowledge": reverse("admin:superchatsync_businessknowledgeitem_changelist"),
            "media": reverse("admin:superchatsync_businessmediaasset_changelist"),
            "runs": reverse("admin:superchatsync_businessknowledgeimportrun_changelist"),
        }


@admin.register(WhatsappAgentInboxRoute)
class WhatsappAgentInboxRouteAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "channel_phone",
        "short_channel_id",
        "inbox_name",
        "agent_type",
        "business",
        "default_product_id",
        "require_handle_status",
        "active",
        "updated_at",
    )
    list_filter = ("active", "agent_type", "business", "inbox_name", "require_handle_status")
    search_fields = (
        "name",
        "channel_id",
        "channel_phone",
        "channel_phone_digits",
        "channel_name",
        "inbox_id",
        "inbox_name",
        "default_product_id",
        "business__slug",
        "business__name",
    )
    readonly_fields = ("route_id", "channel_phone_digits", "created_at", "updated_at")
    autocomplete_fields = ("business",)

    def short_channel_id(self, obj):
        return (obj.channel_id or "")[:12]

    short_channel_id.short_description = "Channel ID"


@admin.register(BusinessKnowledgeImportRun)
class BusinessKnowledgeImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "business",
        "source_type",
        "status",
        "pages_crawled",
        "products_imported",
        "knowledge_items_created",
        "media_assets_created",
        "error_count",
        "started_at",
        "finished_at",
    )
    list_filter = ("business", "status", "source_type")
    search_fields = ("business__slug", "source_url", "notes")
    readonly_fields = ("run_id", "created_at", "updated_at")


@admin.register(BusinessProduct)
class BusinessProductAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "slug", "product_type", "min_price", "max_price", "currency", "status", "updated_at")
    list_filter = ("business", "status", "currency", "product_type")
    search_fields = ("name", "slug", "external_id", "description")
    readonly_fields = ("product_id", "first_seen_at", "last_seen_at", "created_at", "updated_at")


@admin.register(BusinessProductRanking)
class BusinessProductRankingAdmin(admin.ModelAdmin):
    list_display = ("product", "business", "rank_type", "collection_slug", "rank", "score", "active", "updated_at")
    list_filter = ("business", "rank_type", "collection_slug", "active")
    search_fields = ("product__name", "product__slug", "collection_slug", "collection_title", "source_url")
    readonly_fields = ("ranking_id", "first_seen_at", "last_seen_at", "created_at", "updated_at")


@admin.register(ShortLink)
class ShortLinkAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "short_url_link",
        "business_slug",
        "conversation_id",
        "product_name",
        "click_count",
        "first_clicked_at",
        "last_clicked_at",
        "thank_you_state",
        "active",
        "created_at",
    )
    list_filter = ("business_slug", "source_channel", "active", "thank_you_enabled", "created_at", "first_clicked_at")
    search_fields = ("code", "target_url", "conversation_id", "phone", "product_id", "product_name", "campaign_id")
    readonly_fields = (
        "link_id",
        "short_url_link",
        "click_count",
        "first_clicked_at",
        "last_clicked_at",
        "thank_you_attempted_at",
        "thank_you_sent_at",
        "thank_you_message_id",
        "last_thank_you_error",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Redirect", {"fields": ("link_id", "code", "short_url_link", "target_url", "title", "active", "expires_at")}),
        ("Attribution", {"fields": ("business_slug", "source_channel", "source_template", "source_message_id", "intent", "campaign_id")}),
        ("Conversation", {"fields": ("conversation_id", "contact_id", "channel_id", "customer_id", "phone")}),
        ("Product", {"fields": ("product_id", "product_name")}),
        ("Thank-you", {"fields": ("thank_you_enabled", "thank_you_body", "thank_you_attempted_at", "thank_you_sent_at", "thank_you_message_id", "last_thank_you_error")}),
        ("Stats", {"fields": ("click_count", "first_clicked_at", "last_clicked_at")}),
        ("Metadata", {"fields": ("metadata", "created_by", "created_at", "updated_at")}),
    )

    def short_url_link(self, obj):
        if not obj or not obj.code:
            return "-"
        url = short_url_for_code(obj.code)
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    short_url_link.short_description = "Short URL"

    def thank_you_state(self, obj):
        if obj.thank_you_sent_at:
            return "sent"
        if obj.last_thank_you_error:
            return "error"
        if obj.thank_you_attempted_at:
            return "attempted"
        return "-"


@admin.register(ShortLinkClick)
class ShortLinkClickAdmin(admin.ModelAdmin):
    list_display = ("clicked_at", "link_code", "ip_address", "is_preview", "thank_you_queued", "request_method")
    list_filter = ("is_preview", "thank_you_queued", "request_method", "clicked_at")
    search_fields = ("link__code", "link__conversation_id", "ip_address", "user_agent", "referer")
    readonly_fields = (
        "click_id",
        "link",
        "clicked_at",
        "ip_address",
        "user_agent",
        "referer",
        "request_method",
        "query_params",
        "is_preview",
        "thank_you_queued",
        "thank_you_result",
        "metadata",
    )

    def has_add_permission(self, request):
        return False

    def link_code(self, obj):
        return obj.link.code if obj.link_id else "-"


@admin.register(BusinessCrawlPage)
class BusinessCrawlPageAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "page_type", "status", "http_status", "extracted_char_count", "crawled_at")
    list_filter = ("business", "page_type", "status", "language")
    search_fields = ("title", "url", "canonical_url", "extracted_text")
    readonly_fields = ("page_id", "url_hash", "created_at", "updated_at")


@admin.register(BusinessKnowledgeItem)
class BusinessKnowledgeItemAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "scope", "item_type", "product", "status", "confidence_score", "priority", "updated_at")
    list_filter = ("business", "scope", "item_type", "status", "language")
    search_fields = ("title", "question", "answer", "body", "evidence", "source_url", "product__name")
    readonly_fields = ("item_id", "content_hash", "source_url_hash", "created_at", "updated_at")
    actions = ("approve_items", "reject_items")

    @admin.action(description="Approve selected knowledge items")
    def approve_items(self, request, queryset):
        queryset.update(status="approved")

    @admin.action(description="Reject selected knowledge items")
    def reject_items(self, request, queryset):
        queryset.update(status="rejected")


@admin.register(BusinessMediaAsset)
class BusinessMediaAssetAdmin(admin.ModelAdmin):
    list_display = ("title", "business", "product", "asset_type", "image_role", "status", "mime_type", "file_size_bytes", "updated_at")
    list_filter = ("business", "asset_type", "image_role", "status", "language")
    search_fields = ("title", "alt_text", "source_url", "local_path", "product__name")
    readonly_fields = ("asset_id", "source_url_hash", "first_seen_at", "last_seen_at", "created_at", "updated_at")
    actions = ("approve_assets", "reject_assets")

    @admin.action(description="Approve selected media assets")
    def approve_assets(self, request, queryset):
        queryset.update(status="approved")

    @admin.action(description="Reject selected media assets")
    def reject_assets(self, request, queryset):
        queryset.update(status="rejected")
