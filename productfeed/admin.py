from django.contrib import admin
from django.utils.html import format_html
from django.utils.text import slugify

from import_export import resources, fields
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from .models import (
    Product,
    Offer,
    ObjectionRule,
    CrossSellRule,
    IntentRule,
    ProductFAQ,
    ProductSalesRule,
    ProductAsset,
)


def make_id(*parts):
    text = "_".join([slugify(str(p)) for p in parts if p])
    return text.replace("-", "_").strip("_")[:80]


class ProductResource(resources.ModelResource):
    class Meta:
        model = Product
        import_id_fields = ("product_id",)
        fields = (
            "product_id",
            "product_name",
            "brand",
            "category",
            "short_description",
            "main_benefits",
            "material",
            "compatibility",
            "delivery_info",
            "payment_info",
            "warranty_info",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("product_id") and row.get("product_name"):
            row["product_id"] = make_id(row.get("product_name"))


class OfferResource(resources.ModelResource):
    product_id = fields.Field(
        column_name="product_id",
        attribute="product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = Offer
        import_id_fields = ("offer_id",)
        fields = (
            "offer_id",
            "product_id",
            "offer_name",
            "variant",
            "quantity",
            "price",
            "currency",
            "gift_1",
            "gift_2",
            "delivery_offer",
            "payment_method",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("offer_id"):
            row["offer_id"] = make_id(
                row.get("product_id"),
                row.get("offer_name") or row.get("variant") or "offer",
            )


class ProductFAQResource(resources.ModelResource):
    product_id = fields.Field(
        column_name="product_id",
        attribute="product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = ProductFAQ
        import_id_fields = ("faq_id",)
        fields = (
            "faq_id",
            "product_id",
            "question",
            "answer",
            "priority",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("faq_id"):
            row["faq_id"] = make_id(row.get("product_id"), row.get("question"), "faq")


class ObjectionRuleResource(resources.ModelResource):
    product_id = fields.Field(
        column_name="product_id",
        attribute="product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = ObjectionRule
        import_id_fields = ("rule_id",)
        fields = (
            "rule_id",
            "product_id",
            "objection_type",
            "client_phrase_examples",
            "recommended_answer",
            "next_action",
            "priority",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("rule_id"):
            row["rule_id"] = make_id(
                row.get("product_id"),
                row.get("objection_type"),
                "objection",
            )


class ProductSalesRuleResource(resources.ModelResource):
    product_id = fields.Field(
        column_name="product_id",
        attribute="product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = ProductSalesRule
        import_id_fields = ("rule_id",)
        fields = (
            "rule_id",
            "product_id",
            "trigger",
            "action",
            "instruction",
            "priority",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("rule_id"):
            row["rule_id"] = make_id(
                row.get("product_id"),
                row.get("trigger"),
                row.get("action"),
                "sales_rule",
            )


class CrossSellRuleResource(resources.ModelResource):
    source_product_id = fields.Field(
        column_name="source_product_id",
        attribute="source_product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )
    target_product_id = fields.Field(
        column_name="target_product_id",
        attribute="target_product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = CrossSellRule
        import_id_fields = ("rule_id",)
        fields = (
            "rule_id",
            "source_product_id",
            "target_product_id",
            "trigger",
            "message_angle",
            "priority",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("rule_id"):
            row["rule_id"] = make_id(
                row.get("source_product_id"),
                row.get("target_product_id"),
                "cross_sell",
            )


class IntentRuleResource(resources.ModelResource):
    class Meta:
        model = IntentRule
        import_id_fields = ("intent_id",)
        fields = (
            "intent_id",
            "intent",
            "examples",
            "meaning",
            "correct_next_action",
            "priority",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("intent_id"):
            row["intent_id"] = make_id(row.get("intent"), "intent")


class ProductAssetResource(resources.ModelResource):
    product_id = fields.Field(
        column_name="product_id",
        attribute="product",
        widget=ForeignKeyWidget(Product, "product_id"),
    )

    class Meta:
        model = ProductAsset
        import_id_fields = ("asset_id",)
        fields = (
            "asset_id",
            "product_id",
            "asset_type",
            "name",
            "file",
            "usage",
            "active",
        )
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        if not row.get("asset_id"):
            row["asset_id"] = make_id(
                row.get("product_id"),
                row.get("asset_type"),
                row.get("name") or "asset",
            )


class OfferInline(admin.TabularInline):
    model = Offer
    extra = 1
    fields = (
        "offer_name",
        "variant",
        "quantity",
        "price",
        "currency",
        "gift_1",
        "gift_2",
        "delivery_offer",
        "payment_method",
        "active",
    )


class ProductFAQInline(admin.StackedInline):
    model = ProductFAQ
    extra = 1
    fields = ("question", "answer", "priority", "active")


class ObjectionRuleInline(admin.StackedInline):
    model = ObjectionRule
    extra = 1
    fields = (
        "objection_type",
        "client_phrase_examples",
        "recommended_answer",
        "next_action",
        "priority",
        "active",
    )


class ProductSalesRuleInline(admin.StackedInline):
    model = ProductSalesRule
    extra = 1
    fields = ("trigger", "action", "instruction", "priority", "active")


class ProductAssetInline(admin.TabularInline):
    model = ProductAsset
    extra = 1
    fields = ("asset_type", "name", "file", "usage", "active")


class CrossSellFromInline(admin.StackedInline):
    model = CrossSellRule
    fk_name = "source_product"
    extra = 1
    fields = ("target_product", "trigger", "message_angle", "priority", "active")
    verbose_name = "Cross-sell from this product"
    verbose_name_plural = "Cross-sell from this product"


@admin.register(Product)
class ProductAdmin(ImportExportModelAdmin):
    resource_class = ProductResource
    list_display = ("product_id", "product_name", "brand", "category", "active")
    search_fields = ("product_id", "product_name", "brand", "category")
    list_filter = ("active", "category", "brand")

    fieldsets = (
        ("General", {
            "fields": (
                "product_id",
                "product_name",
                "brand",
                "category",
                "active",
            )
        }),
        ("Product details", {
            "fields": (
                "short_description",
                "main_benefits",
                "material",
                "compatibility",
            )
        }),
        ("Commercial info", {
            "fields": (
                "delivery_info",
                "payment_info",
                "warranty_info",
            )
        }),
    )

    inlines = [
        OfferInline,
        ProductFAQInline,
        ObjectionRuleInline,
        ProductSalesRuleInline,
        ProductAssetInline,
        CrossSellFromInline,
    ]


@admin.register(Offer)
class OfferAdmin(ImportExportModelAdmin):
    resource_class = OfferResource
    list_display = ("offer_id", "product", "offer_name", "variant", "price", "currency", "active")
    search_fields = ("offer_id", "offer_name", "variant", "product__product_name")
    list_filter = ("active", "currency", "product")


@admin.register(ObjectionRule)
class ObjectionRuleAdmin(ImportExportModelAdmin):
    resource_class = ObjectionRuleResource
    list_display = ("rule_id", "product", "objection_type", "priority", "active")
    search_fields = ("rule_id", "objection_type", "client_phrase_examples", "recommended_answer")
    list_filter = ("active", "priority", "product", "objection_type")


@admin.register(CrossSellRule)
class CrossSellRuleAdmin(ImportExportModelAdmin):
    resource_class = CrossSellRuleResource
    list_display = ("rule_id", "source_product", "target_product", "priority", "active")
    search_fields = ("rule_id", "trigger", "message_angle")
    list_filter = ("active", "priority")


@admin.register(IntentRule)
class IntentRuleAdmin(ImportExportModelAdmin):
    resource_class = IntentRuleResource
    list_display = ("intent_id", "intent", "correct_next_action", "priority", "active")
    search_fields = ("intent_id", "intent", "examples", "meaning")
    list_filter = ("active", "priority", "intent")


@admin.register(ProductFAQ)
class ProductFAQAdmin(ImportExportModelAdmin):
    resource_class = ProductFAQResource
    list_display = ("faq_id", "product", "question", "priority", "active")
    search_fields = ("faq_id", "question", "answer", "product__product_name")
    list_filter = ("active", "priority", "product")


@admin.register(ProductSalesRule)
class ProductSalesRuleAdmin(ImportExportModelAdmin):
    resource_class = ProductSalesRuleResource
    list_display = ("rule_id", "product", "trigger", "action", "priority", "active")
    search_fields = ("rule_id", "trigger", "action", "instruction", "product__product_name")
    list_filter = ("active", "priority", "product")


@admin.register(ProductAsset)
class ProductAssetAdmin(ImportExportModelAdmin):
    resource_class = ProductAssetResource
    list_display = ("asset_id", "product", "asset_type", "name", "file_link", "active")
    search_fields = ("asset_id", "name", "usage", "product__product_name")
    list_filter = ("active", "asset_type", "product")

    def file_link(self, obj):
        if obj.file:
            return format_html('<a href="{}" target="_blank">Open</a>', obj.file.url)
        return "-"

    file_link.short_description = "File"
