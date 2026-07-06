from uuid import uuid4

from django.db import models
from django.utils.text import slugify


def build_id(*parts):
    text = "_".join([slugify(str(p)) for p in parts if p])
    text = text.replace("-", "_").strip("_")
    if not text:
        text = uuid4().hex[:8]
    return text[:80]


def unique_id(model_class, base_id):
    candidate = base_id
    if not model_class.objects.filter(pk=candidate).exists():
        return candidate
    return f"{candidate[:70]}_{uuid4().hex[:8]}"


class Product(models.Model):
    product_id = models.TextField(primary_key=True)
    product_name = models.TextField()
    brand = models.TextField(blank=True, null=True)
    category = models.TextField(blank=True, null=True)
    short_description = models.TextField(blank=True, null=True)
    main_benefits = models.TextField(blank=True, null=True)
    material = models.TextField(blank=True, null=True)
    compatibility = models.TextField(blank=True, null=True)
    delivery_info = models.TextField(blank=True, null=True)
    payment_info = models.TextField(blank=True, null=True)
    warranty_info = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "products"
        managed = False
        verbose_name = "Product"
        verbose_name_plural = "Products"

    def __str__(self):
        return self.product_name or self.product_id


class Offer(models.Model):
    offer_id = models.TextField(primary_key=True, blank=True)
    product = models.ForeignKey(Product, db_column="product_id", on_delete=models.CASCADE)
    offer_name = models.TextField()
    variant = models.TextField(blank=True, null=True)
    quantity = models.IntegerField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    currency = models.TextField(default="RON")
    gift_1 = models.TextField(blank=True, null=True)
    gift_2 = models.TextField(blank=True, null=True)
    delivery_offer = models.TextField(blank=True, null=True)
    payment_method = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "offers"
        managed = False
        verbose_name = "Offer"
        verbose_name_plural = "Offers"

    def save(self, *args, **kwargs):
        if not self.offer_id:
            base = build_id(self.product_id, self.offer_name or self.variant or "offer")
            self.offer_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product} - {self.offer_name}"


class ObjectionRule(models.Model):
    rule_id = models.TextField(primary_key=True, blank=True)
    product = models.ForeignKey(Product, db_column="product_id", on_delete=models.CASCADE, blank=True, null=True)
    objection_type = models.TextField()
    client_phrase_examples = models.TextField(blank=True, null=True)
    recommended_answer = models.TextField()
    next_action = models.TextField(blank=True, null=True)
    priority = models.TextField(default="medium")
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "objection_rules"
        managed = False
        verbose_name = "Objection Rule"
        verbose_name_plural = "Objection Rules"

    def save(self, *args, **kwargs):
        if not self.rule_id:
            base = build_id(self.product_id, self.objection_type, "objection")
            self.rule_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.rule_id


class CrossSellRule(models.Model):
    rule_id = models.TextField(primary_key=True, blank=True)
    source_product = models.ForeignKey(
        Product,
        db_column="source_product_id",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="source_cross_sells",
    )
    target_product = models.ForeignKey(
        Product,
        db_column="target_product_id",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="target_cross_sells",
    )
    trigger = models.TextField(blank=True, null=True)
    message_angle = models.TextField(blank=True, null=True)
    priority = models.TextField(default="medium")
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "cross_sell_rules"
        managed = False
        verbose_name = "Cross-sell Rule"
        verbose_name_plural = "Cross-sell Rules"

    def save(self, *args, **kwargs):
        if not self.rule_id:
            base = build_id(self.source_product_id, self.target_product_id, "cross_sell")
            self.rule_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.rule_id


class IntentRule(models.Model):
    intent_id = models.TextField(primary_key=True, blank=True)
    intent = models.TextField()
    examples = models.TextField(blank=True, null=True)
    meaning = models.TextField(blank=True, null=True)
    correct_next_action = models.TextField(blank=True, null=True)
    priority = models.TextField(default="medium")
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "intent_rules"
        managed = False
        verbose_name = "Intent Rule"
        verbose_name_plural = "Intent Rules"

    def save(self, *args, **kwargs):
        if not self.intent_id:
            base = build_id(self.intent, "intent")
            self.intent_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.intent


class ProductFAQ(models.Model):
    faq_id = models.TextField(primary_key=True, blank=True)
    product = models.ForeignKey(Product, db_column="product_id", on_delete=models.CASCADE)
    question = models.TextField()
    answer = models.TextField()
    priority = models.TextField(default="medium")
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_faq"
        managed = False
        verbose_name = "Product FAQ"
        verbose_name_plural = "Product FAQs"

    def save(self, *args, **kwargs):
        if not self.faq_id:
            base = build_id(self.product_id, self.question, "faq")
            self.faq_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.question


class ProductSalesRule(models.Model):
    rule_id = models.TextField(primary_key=True, blank=True)
    product = models.ForeignKey(Product, db_column="product_id", on_delete=models.CASCADE)
    trigger = models.TextField()
    action = models.TextField()
    instruction = models.TextField()
    priority = models.TextField(default="medium")
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_sales_rules"
        managed = False
        verbose_name = "Product Sales Rule"
        verbose_name_plural = "Product Sales Rules"

    def save(self, *args, **kwargs):
        if not self.rule_id:
            base = build_id(self.product_id, self.trigger, self.action, "sales_rule")
            self.rule_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.rule_id


class ProductAsset(models.Model):
    asset_id = models.TextField(primary_key=True, blank=True)
    product = models.ForeignKey(Product, db_column="product_id", on_delete=models.CASCADE)
    asset_type = models.TextField()
    name = models.TextField(blank=True, null=True)
    file = models.FileField(db_column="url_or_path", upload_to="product_assets/", blank=True, null=True)
    usage = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "product_assets"
        managed = False
        verbose_name = "Product Asset"
        verbose_name_plural = "Product Assets"

    def save(self, *args, **kwargs):
        if not self.asset_id:
            base = build_id(self.product_id, self.asset_type, self.name or "asset")
            self.asset_id = unique_id(self.__class__, base)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or self.asset_id
