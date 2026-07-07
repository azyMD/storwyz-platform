from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import AdminSite
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    BusinessClient,
    BusinessCrawlPage,
    BusinessKnowledgeImportRun,
    BusinessKnowledgeItem,
    BusinessMediaAsset,
    BusinessProduct,
    BusinessProductRanking,
    Conversation,
    Message,
    PeekoWorkspaceLink,
    ShortLink,
    ShortLinkClick,
    WhatsappAgentInboxRoute,
)
from .shortlinks import short_url_for_code


PEEKO_BUSINESS_SLUG = "peeko"
PEEKO_GROUP_NAMES = ("Peeko Team", "Peeko Admin")


def peeko_group_names():
    return tuple(getattr(settings, "PEEKO_ADMIN_GROUP_NAMES", PEEKO_GROUP_NAMES))


def user_can_access_peeko_admin(user):
    if not getattr(user, "is_active", False) or not getattr(user, "is_staff", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name__in=peeko_group_names()).exists()


def peeko_business():
    return BusinessClient.objects.filter(slug=PEEKO_BUSINESS_SLUG).first()


def peeko_routes():
    return WhatsappAgentInboxRoute.objects.filter(
        Q(business__slug=PEEKO_BUSINESS_SLUG)
        | Q(agent_type=WhatsappAgentInboxRoute.AGENT_PEEKO_BUSINESS)
    )


def peeko_conversation_q():
    route_ids = [str(route_id) for route_id in peeko_routes().values_list("route_id", flat=True)]
    channel_ids = [value for value in peeko_routes().exclude(channel_id__isnull=True).values_list("channel_id", flat=True)]
    inbox_ids = [value for value in peeko_routes().exclude(inbox_id__isnull=True).values_list("inbox_id", flat=True)]
    link_conversations = (
        ShortLink.objects.filter(business_slug=PEEKO_BUSINESS_SLUG)
        .exclude(conversation_id__isnull=True)
        .exclude(conversation_id="")
        .values_list("conversation_id", flat=True)
    )

    query = (
        Q(product_detected__startswith=f"business:{PEEKO_BUSINESS_SLUG}")
        | Q(metadata__business_slug=PEEKO_BUSINESS_SLUG)
        | Q(metadata__agent_type=WhatsappAgentInboxRoute.AGENT_PEEKO_BUSINESS)
        | Q(conversation_id__in=link_conversations)
    )
    if route_ids:
        query |= Q(metadata__agent_route_id__in=route_ids)
    if channel_ids:
        query |= Q(metadata__agent_channel_id__in=channel_ids)
    if inbox_ids:
        query |= Q(metadata__agent_inbox_id__in=inbox_ids)
    return query


def peeko_admin_url(name, *args):
    try:
        return reverse(f"{peeko_admin_site.name}:{name}", args=args)
    except Exception:
        return "#"


def safe_count(queryset):
    try:
        return queryset.count()
    except Exception:
        return 0


def status_counts(queryset, field="status"):
    try:
        return {
            str(row[field] or "empty"): row["count"]
            for row in queryset.values(field).annotate(count=Count("pk"))
        }
    except Exception:
        return {}


class PeekoAdminSite(AdminSite):
    site_header = "Peeko Workspace"
    site_title = "Peeko Admin"
    index_title = "Peeko operations"
    site_url = "/peeko-admin/"

    def has_permission(self, request):
        return user_can_access_peeko_admin(request.user)


peeko_admin_site = PeekoAdminSite(name="peeko_admin")


class PeekoAccessAdminMixin:
    show_full_result_count = False

    def has_module_permission(self, request):
        return user_can_access_peeko_admin(request.user)

    def has_view_permission(self, request, obj=None):
        return user_can_access_peeko_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return user_can_access_peeko_admin(request.user)

    def has_add_permission(self, request):
        return user_can_access_peeko_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return bool(getattr(request.user, "is_superuser", False))


class PeekoEditableAdmin(PeekoAccessAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return user_can_access_peeko_admin(request.user)


class PeekoReadOnlyAdmin(PeekoAccessAdminMixin, admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PeekoBusinessScopedAdmin(PeekoEditableAdmin):
    peeko_business_field = "business"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.filter(**{f"{self.peeko_business_field}__slug": PEEKO_BUSINESS_SLUG})

    def save_model(self, request, obj, form, change):
        business = peeko_business()
        if business and hasattr(obj, "business_id"):
            obj.business = business
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "business":
            kwargs["queryset"] = BusinessClient.objects.filter(slug=PEEKO_BUSINESS_SLUG)
        elif db_field.name == "product":
            kwargs["queryset"] = BusinessProduct.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        elif db_field.name == "import_run":
            kwargs["queryset"] = BusinessKnowledgeImportRun.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        elif db_field.name == "page":
            kwargs["queryset"] = BusinessCrawlPage.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class PeekoNoAddBusinessScopedAdmin(PeekoBusinessScopedAdmin):
    def has_add_permission(self, request):
        return False


class PeekoMessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    fields = ("sent_at", "sender_type", "sender_name", "message_text", "button_clicked", "is_client_reply")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return user_can_access_peeko_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PeekoWorkspaceLink, site=peeko_admin_site)
class PeekoWorkspaceAdmin(PeekoReadOnlyAdmin):
    def changelist_view(self, request, extra_context=None):
        context = self.admin_site.each_context(request)
        context.update(
            {
                "opts": self.model._meta,
                "title": "Peeko Workspace",
                **self._build_context(request),
            }
        )
        return TemplateResponse(request, "admin/superchatsync/peeko_workspace.html", context)

    def _build_context(self, request):
        business = peeko_business()
        products = BusinessProduct.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        knowledge = BusinessKnowledgeItem.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        media = BusinessMediaAsset.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        runs = BusinessKnowledgeImportRun.objects.filter(business__slug=PEEKO_BUSINESS_SLUG)
        routes = peeko_routes()
        shortlinks = ShortLink.objects.filter(business_slug=PEEKO_BUSINESS_SLUG)
        clicks = ShortLinkClick.objects.filter(link__business_slug=PEEKO_BUSINESS_SLUG)
        conversations = Conversation.objects.filter(peeko_conversation_q())

        product_status = status_counts(products)
        knowledge_status = status_counts(knowledge)
        media_status = status_counts(media)

        metrics = [
            {"label": "Products", "value": safe_count(products), "hint": f"{product_status.get('active', 0)} active"},
            {"label": "Knowledge", "value": safe_count(knowledge), "hint": f"{knowledge_status.get('draft', 0)} draft"},
            {"label": "Media", "value": safe_count(media), "hint": f"{media_status.get('draft', 0)} draft"},
            {"label": "Conversations", "value": safe_count(conversations), "hint": "Peeko inbox only"},
            {"label": "Shortlinks", "value": safe_count(shortlinks), "hint": f"{safe_count(clicks)} clicks"},
            {"label": "Agent routes", "value": safe_count(routes), "hint": f"{safe_count(routes.filter(active=True))} active"},
        ]

        next_actions = []
        draft_products = product_status.get("draft", 0)
        draft_knowledge = knowledge_status.get("draft", 0)
        draft_media = media_status.get("draft", 0)
        inactive_routes = safe_count(routes.filter(active=False))
        if draft_products:
            next_actions.append(
                {
                    "title": "Activate imported products",
                    "body": f"{draft_products} Peeko products are still draft.",
                    "href": peeko_admin_url("superchatsync_businessproduct_changelist") + "?status__exact=draft",
                    "label": "Open products",
                    "priority": "high",
                }
            )
        if draft_knowledge:
            next_actions.append(
                {
                    "title": "Review product knowledge",
                    "body": f"{draft_knowledge} Peeko knowledge items need review.",
                    "href": peeko_admin_url("superchatsync_businessknowledgeitem_changelist") + "?status__exact=draft",
                    "label": "Open knowledge",
                    "priority": "high",
                }
            )
        if draft_media:
            next_actions.append(
                {
                    "title": "Review product images",
                    "body": f"{draft_media} Peeko media assets are waiting for approval.",
                    "href": peeko_admin_url("superchatsync_businessmediaasset_changelist") + "?status__exact=draft",
                    "label": "Open media",
                    "priority": "medium",
                }
            )
        if inactive_routes:
            next_actions.append(
                {
                    "title": "Check inactive routes",
                    "body": f"{inactive_routes} Peeko inbox routes are inactive.",
                    "href": peeko_admin_url("superchatsync_whatsappagentinboxroute_changelist") + "?active__exact=0",
                    "label": "Open routes",
                    "priority": "medium",
                }
            )
        if not next_actions:
            next_actions.append(
                {
                    "title": "Peeko queues are clear",
                    "body": "No urgent product, knowledge, media or route queue is visible right now.",
                    "href": peeko_admin_url("superchatsync_businessproduct_changelist"),
                    "label": "Open catalog",
                    "priority": "low",
                }
            )

        return {
            "business": business,
            "metrics": metrics,
            "next_actions": next_actions,
            "recent_products": products.order_by("-updated_at")[:8],
            "recent_conversations": conversations.order_by("-last_message_at")[:8],
            "recent_shortlinks": shortlinks.order_by("-created_at")[:8],
            "recent_runs": runs.order_by("-started_at")[:8],
            "links": {
                "business": peeko_admin_url("superchatsync_businessclient_changelist"),
                "products": peeko_admin_url("superchatsync_businessproduct_changelist"),
                "rankings": peeko_admin_url("superchatsync_businessproductranking_changelist"),
                "knowledge": peeko_admin_url("superchatsync_businessknowledgeitem_changelist"),
                "media": peeko_admin_url("superchatsync_businessmediaasset_changelist"),
                "crawl_pages": peeko_admin_url("superchatsync_businesscrawlpage_changelist"),
                "runs": peeko_admin_url("superchatsync_businessknowledgeimportrun_changelist"),
                "routes": peeko_admin_url("superchatsync_whatsappagentinboxroute_changelist"),
                "conversations": peeko_admin_url("superchatsync_conversation_changelist"),
                "messages": peeko_admin_url("superchatsync_message_changelist"),
                "shortlinks": peeko_admin_url("superchatsync_shortlink_changelist"),
                "clicks": peeko_admin_url("superchatsync_shortlinkclick_changelist"),
            },
        }


@admin.register(BusinessClient, site=peeko_admin_site)
class PeekoBusinessClientAdmin(PeekoEditableAdmin):
    list_display = ("name", "slug", "domain", "default_language", "default_currency", "status", "updated_at")
    list_filter = ("status", "default_language")
    search_fields = ("name", "slug", "domain")
    readonly_fields = ("business_id", "slug", "created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(slug=PEEKO_BUSINESS_SLUG)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessKnowledgeImportRun, site=peeko_admin_site)
class PeekoBusinessKnowledgeImportRunAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = (
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
    list_filter = ("status", "source_type")
    search_fields = ("source_url", "notes", "error")
    readonly_fields = ("run_id", "created_at", "updated_at")


@admin.register(BusinessProduct, site=peeko_admin_site)
class PeekoBusinessProductAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = ("name", "slug", "product_type", "price_range", "currency", "status", "updated_at")
    list_filter = ("status", "currency", "product_type")
    search_fields = ("name", "slug", "external_id", "description")
    readonly_fields = ("product_id", "business", "first_seen_at", "last_seen_at", "created_at", "updated_at")
    actions = ("activate_products", "archive_products")

    def price_range(self, obj):
        if obj.min_price == obj.max_price:
            return obj.min_price or "-"
        return f"{obj.min_price or '-'} - {obj.max_price or '-'}"

    @admin.action(description="Activate selected products")
    def activate_products(self, request, queryset):
        updated = queryset.update(status="active")
        self.message_user(request, f"{updated} Peeko products activated.", messages.SUCCESS)

    @admin.action(description="Archive selected products")
    def archive_products(self, request, queryset):
        updated = queryset.update(status="archived")
        self.message_user(request, f"{updated} Peeko products archived.", messages.SUCCESS)


@admin.register(BusinessProductRanking, site=peeko_admin_site)
class PeekoBusinessProductRankingAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = ("product", "rank_type", "collection_slug", "rank", "score", "active", "updated_at")
    list_filter = ("rank_type", "collection_slug", "active")
    search_fields = ("product__name", "product__slug", "collection_slug", "collection_title", "source_url")
    readonly_fields = ("ranking_id", "business", "first_seen_at", "last_seen_at", "created_at", "updated_at")


@admin.register(BusinessCrawlPage, site=peeko_admin_site)
class PeekoBusinessCrawlPageAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = ("title", "page_type", "status", "http_status", "extracted_char_count", "crawled_at")
    list_filter = ("page_type", "status", "language")
    search_fields = ("title", "url", "canonical_url", "extracted_text", "product__name")
    readonly_fields = ("page_id", "business", "url_hash", "created_at", "updated_at")


@admin.register(BusinessKnowledgeItem, site=peeko_admin_site)
class PeekoBusinessKnowledgeItemAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = ("title", "scope", "item_type", "product", "status", "confidence_score", "priority", "updated_at")
    list_filter = ("scope", "item_type", "status", "language")
    search_fields = ("title", "question", "answer", "body", "evidence", "source_url", "product__name")
    readonly_fields = ("item_id", "business", "content_hash", "source_url_hash", "created_at", "updated_at")
    actions = ("approve_items", "reject_items")

    @admin.action(description="Approve selected Peeko knowledge items")
    def approve_items(self, request, queryset):
        updated = queryset.update(status="approved", reviewed_by=request.user.get_username(), reviewed_at=timezone.now())
        self.message_user(request, f"{updated} Peeko knowledge items approved.", messages.SUCCESS)

    @admin.action(description="Reject selected Peeko knowledge items")
    def reject_items(self, request, queryset):
        updated = queryset.update(status="rejected", reviewed_by=request.user.get_username(), reviewed_at=timezone.now())
        self.message_user(request, f"{updated} Peeko knowledge items rejected.", messages.SUCCESS)


@admin.register(BusinessMediaAsset, site=peeko_admin_site)
class PeekoBusinessMediaAssetAdmin(PeekoNoAddBusinessScopedAdmin):
    list_display = ("title", "product", "asset_type", "image_role", "status", "mime_type", "file_size_bytes", "updated_at")
    list_filter = ("asset_type", "image_role", "status", "language")
    search_fields = ("title", "alt_text", "source_url", "local_path", "product__name")
    readonly_fields = ("asset_id", "business", "source_url_hash", "first_seen_at", "last_seen_at", "created_at", "updated_at")
    actions = ("approve_assets", "reject_assets")

    @admin.action(description="Approve selected Peeko media assets")
    def approve_assets(self, request, queryset):
        updated = queryset.update(status="approved")
        self.message_user(request, f"{updated} Peeko media assets approved.", messages.SUCCESS)

    @admin.action(description="Reject selected Peeko media assets")
    def reject_assets(self, request, queryset):
        updated = queryset.update(status="rejected")
        self.message_user(request, f"{updated} Peeko media assets rejected.", messages.SUCCESS)


@admin.register(WhatsappAgentInboxRoute, site=peeko_admin_site)
class PeekoWhatsappAgentInboxRouteAdmin(PeekoEditableAdmin):
    list_display = (
        "name",
        "channel_phone",
        "short_channel_id",
        "inbox_name",
        "require_handle_status",
        "active",
        "updated_at",
    )
    list_filter = ("active", "inbox_name", "require_handle_status")
    search_fields = ("name", "channel_id", "channel_phone", "channel_phone_digits", "channel_name", "inbox_id", "inbox_name")
    readonly_fields = ("route_id", "agent_type", "business", "channel_phone_digits", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            Q(business__slug=PEEKO_BUSINESS_SLUG)
            | Q(agent_type=WhatsappAgentInboxRoute.AGENT_PEEKO_BUSINESS)
        )

    def save_model(self, request, obj, form, change):
        business = peeko_business()
        if business:
            obj.business = business
        obj.agent_type = WhatsappAgentInboxRoute.AGENT_PEEKO_BUSINESS
        super().save_model(request, obj, form, change)

    def short_channel_id(self, obj):
        return (obj.channel_id or "")[:12]

    short_channel_id.short_description = "Channel ID"


@admin.register(ShortLink, site=peeko_admin_site)
class PeekoShortLinkAdmin(PeekoEditableAdmin):
    list_display = (
        "code",
        "short_url_link",
        "conversation_id",
        "product_name",
        "click_count",
        "first_clicked_at",
        "last_clicked_at",
        "thank_you_state",
        "active",
        "created_at",
    )
    list_filter = ("source_channel", "active", "thank_you_enabled", "created_at", "first_clicked_at")
    search_fields = ("code", "target_url", "conversation_id", "phone", "product_id", "product_name", "campaign_id")
    readonly_fields = (
        "link_id",
        "business_slug",
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

    def get_queryset(self, request):
        return super().get_queryset(request).filter(business_slug=PEEKO_BUSINESS_SLUG)

    def save_model(self, request, obj, form, change):
        obj.business_slug = PEEKO_BUSINESS_SLUG
        super().save_model(request, obj, form, change)

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


@admin.register(ShortLinkClick, site=peeko_admin_site)
class PeekoShortLinkClickAdmin(PeekoReadOnlyAdmin):
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

    def get_queryset(self, request):
        return super().get_queryset(request).filter(link__business_slug=PEEKO_BUSINESS_SLUG)

    def link_code(self, obj):
        return obj.link.code if obj.link_id else "-"


@admin.register(Conversation, site=peeko_admin_site)
class PeekoConversationAdmin(PeekoReadOnlyAdmin):
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
    list_filter = ("channel", "has_client_reply", "product_detected", "source", "status")
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
    inlines = [PeekoMessageInline]
    ordering = ("-last_message_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(peeko_conversation_q()).distinct()

    def short_conversation_id(self, obj):
        cid = obj.conversation_id
        return cid[:18] + "..." if cid and len(cid) > 18 else cid

    short_conversation_id.short_description = "conversation_id"

    def message_count(self, obj):
        return obj.messages.count()

    message_count.short_description = "messages"


@admin.register(Message, site=peeko_admin_site)
class PeekoMessageAdmin(PeekoReadOnlyAdmin):
    list_display = ("sent_at", "conversation_id_short", "sender_type", "is_client_reply", "message_preview")
    search_fields = ("conversation__conversation_id", "message_id", "message_text", "sender_type", "sender_name")
    list_filter = ("sender_type", "is_client_reply", "message_type")
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

    def get_queryset(self, request):
        return super().get_queryset(request).filter(conversation__in=Conversation.objects.filter(peeko_conversation_q()))

    def conversation_id_short(self, obj):
        cid = obj.conversation_id
        return cid[:18] + "..." if cid and len(cid) > 18 else cid

    conversation_id_short.short_description = "conversation_id"

    def message_preview(self, obj):
        text = obj.message_text or ""
        return text[:160] + "..." if len(text) > 160 else text

    message_preview.short_description = "message"
