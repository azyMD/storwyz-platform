import uuid

from django.db import models
from django.utils import timezone


class SuperchatSyncRun(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("discovering", "Discovering"),
        ("waiting_approval", "Waiting approval"),
        ("extracting", "Extracting"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("stopping", "Stopping"),
        ("stopped", "Stopped"),
    ]

    RUN_TYPE_CHOICES = [
        ("discover", "Discover updates"),
        ("extract", "Extract approved"),
        ("full", "Full sync"),
    ]

    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run_type = models.CharField(max_length=30, choices=RUN_TYPE_CHOICES, default="discover")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="pending")

    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    stop_requested = models.BooleanField(default=False)

    total_checked = models.IntegerField(default=0)
    candidates_found = models.IntegerField(default=0)
    total_to_extract = models.IntegerField(default=0)

    processed_count = models.IntegerField(default=0)
    downloaded_count = models.IntegerField(default=0)
    parsed_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)

    current_conversation_id = models.CharField(max_length=120, blank=True, null=True)

    notes = models.TextField(blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "superchat_sync_runs"
        ordering = ["-started_at"]
        verbose_name = "Superchat Sync Run"
        verbose_name_plural = "Superchat Sync Runs"

    @property
    def progress_percent(self):
        if self.total_to_extract and self.status in ("extracting", "stopping", "stopped", "completed"):
            return round((self.processed_count / self.total_to_extract) * 100, 2)
        if self.total_checked and self.status in ("discovering", "waiting_approval"):
            return round((self.total_checked / max(self.total_checked, 1)) * 100, 2)
        return 0

    def __str__(self):
        return f"{self.run_type} | {self.status} | {self.started_at:%Y-%m-%d %H:%M}"


class SuperchatSyncCandidate(models.Model):
    DECISION_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("skipped", "Skipped"),
    ]

    EXTRACT_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("exporting", "Exporting"),
        ("export_pending", "Export pending"),
        ("downloading", "Downloading"),
        ("downloaded", "Downloaded"),
        ("parsed", "Parsed"),
        ("error", "Error"),
        ("skipped", "Skipped"),
    ]

    candidate_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    run = models.ForeignKey(
        SuperchatSyncRun,
        on_delete=models.CASCADE,
        related_name="candidates",
    )

    conversation_id = models.CharField(max_length=120)
    superchat_status = models.CharField(max_length=80, blank=True, null=True)

    channel_id = models.CharField(max_length=120, blank=True, null=True)
    channel_type = models.CharField(max_length=80, blank=True, null=True)

    inbox_id = models.CharField(max_length=120, blank=True, null=True)
    inbox_name = models.CharField(max_length=255, blank=True, null=True)

    superchat_url = models.TextField(blank=True, null=True)

    local_exists = models.BooleanField(default=False)
    local_last_imported_at = models.DateTimeField(blank=True, null=True)
    superchat_open_until = models.DateTimeField(blank=True, null=True)

    change_reason = models.CharField(max_length=255, blank=True, null=True)

    decision = models.CharField(max_length=30, choices=DECISION_CHOICES, default="pending")
    extract_status = models.CharField(max_length=30, choices=EXTRACT_STATUS_CHOICES, default="pending")

    export_id = models.CharField(max_length=120, blank=True, null=True)
    export_status = models.CharField(max_length=80, blank=True, null=True)
    export_link = models.TextField(blank=True, null=True)
    export_link_valid_until = models.DateTimeField(blank=True, null=True)

    zip_path = models.TextField(blank=True, null=True)
    pdf_path = models.TextField(blank=True, null=True)

    raw_zip_hash = models.TextField(blank=True, null=True)
    raw_zip_size = models.BigIntegerField(blank=True, null=True)
    extracted_dir = models.TextField(blank=True, null=True)
    archive_status = models.CharField(max_length=80, default="downloaded")
    archive_error = models.TextField(blank=True, null=True)
    archive_processed_at = models.DateTimeField(blank=True, null=True)

    text_status = models.CharField(max_length=80, default="pending")
    text_path = models.TextField(blank=True, null=True)
    text_hash = models.TextField(blank=True, null=True)
    text_extraction_version = models.TextField(blank=True, null=True)
    text_extracted_at = models.DateTimeField(blank=True, null=True)

    parse_status = models.CharField(max_length=80, default="pending")
    parse_error = models.TextField(blank=True, null=True)
    parsed_at = models.DateTimeField(blank=True, null=True)

    messages_found = models.IntegerField(default=0)
    attachments_found = models.IntegerField(default=0)

    error = models.TextField(blank=True, null=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "superchat_sync_candidates"
        ordering = ["created_at"]
        unique_together = [("run", "conversation_id")]
        verbose_name = "Superchat Sync Candidate"
        verbose_name_plural = "Superchat Sync Candidates"

    def __str__(self):
        return f"{self.conversation_id} | {self.decision} | {self.extract_status}"


class FitexpressCountry(models.Model):
    country_id = models.PositiveIntegerField(primary_key=True)
    country_name = models.TextField()
    iso2 = models.CharField(max_length=2, blank=True, null=True)
    phone_prefixes = models.JSONField(default=list, blank=True)
    default_language = models.CharField(max_length=12, blank=True, null=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fitexpress_countries"
        verbose_name = "Fitexpress Country"
        verbose_name_plural = "Fitexpress Countries"
        ordering = ["country_name"]
        indexes = [
            models.Index(fields=["country_name"]),
            models.Index(fields=["iso2"]),
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return f"{self.country_id} | {self.country_name}"


class FitexpressProductMapping(models.Model):
    mapping_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_id = models.TextField(unique=True, db_index=True)
    product_name = models.TextField()
    fitexpress_product_id = models.TextField(unique=True, db_index=True)
    aliases = models.JSONField(default=list, blank=True)
    landing_url = models.TextField(blank=True, null=True)
    match_status = models.CharField(max_length=40, default="exact")
    options = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fitexpress_product_mappings"
        verbose_name = "Fitexpress Product Mapping"
        verbose_name_plural = "Fitexpress Product Mappings"
        ordering = ["product_name"]
        indexes = [
            models.Index(fields=["product_name"]),
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return f"{self.product_id} -> {self.fitexpress_product_id} | {self.product_name}"


class FitexpressOrderSnapshot(models.Model):
    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    external_order_id = models.TextField(unique=True, db_index=True)

    status_id = models.PositiveIntegerField(blank=True, null=True, db_index=True)
    product_id = models.TextField(blank=True, null=True, db_index=True)
    country_id = models.PositiveIntegerField(blank=True, null=True, db_index=True)
    region_id = models.PositiveIntegerField(blank=True, null=True)
    product_sku = models.TextField(blank=True, null=True)
    quantity = models.TextField(blank=True, null=True)
    quantity_number = models.IntegerField(blank=True, null=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    shipping_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    currency_id = models.PositiveIntegerField(blank=True, null=True)
    payment_type = models.TextField(blank=True, null=True)
    customer_paid_online = models.BooleanField(blank=True, null=True)

    customer_name = models.TextField(blank=True, null=True)
    customer_location = models.TextField(blank=True, null=True)
    customer_address = models.TextField(blank=True, null=True)
    customer_phone = models.TextField(blank=True, null=True)
    normalized_phone = models.TextField(blank=True, null=True, db_index=True)
    customer_comment = models.TextField(blank=True, null=True)
    customer_zipcode = models.TextField(blank=True, null=True)
    customer_email = models.TextField(blank=True, null=True)
    customer_age = models.TextField(blank=True, null=True)
    customer_gender = models.TextField(blank=True, null=True)
    customer_streetnr = models.TextField(blank=True, null=True)
    customer_blocknr = models.TextField(blank=True, null=True)
    customer_appartmentnr = models.TextField(blank=True, null=True)

    deliver_date = models.TextField(blank=True, null=True)
    created_at_remote = models.DateTimeField(blank=True, null=True)
    updated_at_remote = models.DateTimeField(blank=True, null=True)
    referral = models.TextField(blank=True, null=True)
    source = models.TextField(blank=True, null=True)

    curier_id = models.TextField(blank=True, null=True)
    courier_note = models.TextField(blank=True, null=True)
    tracking_url = models.TextField(blank=True, null=True)
    tracking_pdf = models.TextField(blank=True, null=True)
    approve_method = models.TextField(blank=True, null=True)

    raw_payload = models.JSONField(default=dict, blank=True)
    fetch_params = models.JSONField(default=dict, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fitexpress_order_snapshots"
        verbose_name = "Fitexpress Order Snapshot"
        verbose_name_plural = "Fitexpress Order Snapshots"
        ordering = ["-created_at_remote", "-updated_at_remote", "external_order_id"]
        indexes = [
            models.Index(fields=["status_id", "country_id"], name="fitexp_snap_status_country_idx"),
            models.Index(fields=["product_id", "created_at_remote"], name="fitexp_snap_prod_cr_idx"),
            models.Index(fields=["created_at_remote"], name="fitexp_snap_created_idx"),
            models.Index(fields=["updated_at_remote"], name="fitexp_snap_updated_idx"),
            models.Index(fields=["normalized_phone"], name="fitexp_snap_phone_idx"),
        ]

    def __str__(self):
        return f"{self.external_order_id} | status={self.status_id}"



class Conversation(models.Model):
    conversation_id = models.TextField(primary_key=True)
    channel = models.TextField(blank=True, null=True)
    client_name = models.TextField(blank=True, null=True)
    client_phone = models.TextField(blank=True, null=True)
    client_email = models.TextField(blank=True, null=True)
    product_detected = models.TextField(blank=True, null=True)
    campaign_id = models.TextField(blank=True, null=True)
    campaign_name = models.TextField(blank=True, null=True)
    workflow_id = models.TextField(blank=True, null=True)
    workflow_name = models.TextField(blank=True, null=True)

    first_message_at = models.DateTimeField(blank=True, null=True)
    first_client_reply_at = models.DateTimeField(blank=True, null=True)
    last_message_at = models.DateTimeField(blank=True, null=True)
    has_client_reply = models.BooleanField(default=False)
    operator_names = models.TextField(blank=True, null=True)

    raw_pdf_path = models.TextField(blank=True, null=True)
    raw_zip_path = models.TextField(blank=True, null=True)
    source = models.TextField(blank=True, null=True)
    status = models.TextField(blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    last_imported_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "conversations"
        managed = False
        verbose_name = "Conversation"
        verbose_name_plural = "Conversations"
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.conversation_id} - {self.client_name or ''}"


class Message(models.Model):
    message_pk = models.UUIDField(primary_key=True)
    message_id = models.TextField(blank=True, null=True)

    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        related_name="messages",
        on_delete=models.DO_NOTHING,
    )

    sent_at = models.DateTimeField(blank=True, null=True)
    sender_type = models.TextField(blank=True, null=True)
    sender_name = models.TextField(blank=True, null=True)
    message_text = models.TextField(blank=True, null=True)
    message_type = models.TextField(blank=True, null=True)
    button_clicked = models.TextField(blank=True, null=True)
    is_client_reply = models.BooleanField(default=False)

    raw_line_hash = models.TextField(blank=True, null=True)
    raw_payload = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "messages"
        managed = False
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        ordering = ["sent_at"]

    def __str__(self):
        return f"{self.conversation_id} | {self.sender_type} | {self.sent_at}"


class CustomerProfile(models.Model):
    customer_id = models.UUIDField(primary_key=True)
    profile_key = models.TextField(unique=True)

    display_name = models.TextField(blank=True, null=True)
    phone = models.TextField(blank=True, null=True)
    email = models.TextField(blank=True, null=True)

    first_seen_at = models.DateTimeField(blank=True, null=True)
    last_seen_at = models.DateTimeField(blank=True, null=True)

    total_conversations = models.IntegerField(default=0)
    total_messages = models.IntegerField(default=0)

    last_product_detected = models.TextField(blank=True, null=True)
    last_conversation_id = models.TextField(blank=True, null=True)
    status = models.TextField(default="active")

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "customer_profiles"
        managed = False
        verbose_name = "Customer Profile"
        verbose_name_plural = "Customer Profiles"
        ordering = ["-last_seen_at", "display_name"]

    def __str__(self):
        return self.display_name or self.phone or self.email or self.profile_key


class CustomerChannelIdentity(models.Model):
    CHANNEL_CHOICES = [
        ("whatsapp", "WhatsApp"),
        ("sms", "SMS"),
        ("phone", "Phone"),
        ("email", "Email"),
        ("push", "Push notification"),
        ("web", "Web"),
        ("other", "Other"),
    ]

    identity_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.UUIDField(db_index=True)
    channel = models.CharField(max_length=30, choices=CHANNEL_CHOICES)
    identifier = models.TextField()
    normalized_identifier = models.TextField()
    provider = models.TextField(blank=True, null=True)
    provider_contact_id = models.TextField(blank=True, null=True)
    is_primary = models.BooleanField(default=False)
    status = models.CharField(max_length=30, default="active")
    first_seen_at = models.DateTimeField(blank=True, null=True)
    last_seen_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_channel_identities"
        verbose_name = "CRM Channel Identity"
        verbose_name_plural = "CRM Channel Identities"
        ordering = ["customer_id", "channel", "normalized_identifier"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "normalized_identifier"],
                name="uniq_crm_channel_identity",
            )
        ]
        indexes = [
            models.Index(fields=["customer_id", "channel"]),
            models.Index(fields=["channel", "normalized_identifier"]),
        ]

    def __str__(self):
        return f"{self.customer_id} | {self.channel} | {self.identifier}"


class CustomerCommunicationEvent(models.Model):
    CHANNEL_CHOICES = CustomerChannelIdentity.CHANNEL_CHOICES
    DIRECTION_CHOICES = [
        ("inbound", "Inbound"),
        ("outbound", "Outbound"),
        ("system", "System"),
        ("unknown", "Unknown"),
    ]

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.UUIDField(db_index=True)
    channel_identity_id = models.UUIDField(blank=True, null=True, db_column="identity_id", db_index=True)
    channel = models.CharField(max_length=30, choices=CHANNEL_CHOICES)
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES, default="unknown")
    event_type = models.CharField(max_length=60, default="message")
    status = models.CharField(max_length=60, blank=True, null=True)
    provider = models.TextField(blank=True, null=True)
    provider_message_id = models.TextField(blank=True, null=True)
    conversation_id = models.TextField(blank=True, null=True)
    message_id = models.TextField(blank=True, null=True)
    campaign_id = models.TextField(blank=True, null=True)
    workflow_id = models.TextField(blank=True, null=True)
    subject = models.TextField(blank=True, null=True)
    body_preview = models.TextField(blank=True, null=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_communication_events"
        verbose_name = "CRM Communication Event"
        verbose_name_plural = "CRM Communication Events"
        ordering = ["-occurred_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_message_id"],
                condition=models.Q(provider_message_id__isnull=False),
                name="uniq_crm_provider_message",
            )
        ]
        indexes = [
            models.Index(fields=["customer_id", "channel", "occurred_at"]),
            models.Index(fields=["conversation_id"]),
            models.Index(fields=["message_id"]),
            models.Index(fields=["event_type", "status"]),
        ]

    def __str__(self):
        return f"{self.occurred_at} | {self.channel} | {self.direction} | {self.customer_id}"


class CustomerOrder(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("submitted", "Submitted"),
        ("confirmed", "Confirmed"),
        ("paid", "Paid"),
        ("fulfilled", "Fulfilled"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
        ("returned", "Returned"),
        ("failed", "Failed"),
    ]

    order_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.UUIDField(blank=True, null=True, db_index=True)
    product_id = models.TextField()
    sku = models.TextField(blank=True, null=True)
    quantity = models.IntegerField(default=1)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="RON")
    cost_eur_estimate = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    eur_exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, blank=True, null=True)
    exchange_rate_month = models.DateField(blank=True, null=True)
    exchange_rate_source = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="submitted")
    source_channel = models.CharField(max_length=30, default="whatsapp")
    source_conversation_id = models.TextField(blank=True, null=True)
    source_message_id = models.TextField(blank=True, null=True)
    external_order_id = models.TextField(blank=True, null=True)
    external_status = models.TextField(blank=True, null=True)
    idempotency_key = models.TextField(unique=True)
    webhook_url = models.TextField(blank=True, null=True)
    webhook_http_status = models.IntegerField(blank=True, null=True)
    customer_comment = models.TextField(blank=True, null=True)
    order_payload = models.JSONField(default=dict, blank=True)
    raw_response = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_customer_orders"
        verbose_name = "CRM Customer Order"
        verbose_name_plural = "CRM Customer Orders"
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["customer_id", "submitted_at"]),
            models.Index(fields=["product_id", "submitted_at"]),
            models.Index(fields=["source_conversation_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["currency", "submitted_at"], name="crm_order_currency_month_idx"),
            models.Index(fields=["cost_eur_estimate"], name="crm_order_cost_eur_idx"),
        ]

    def __str__(self):
        return f"{self.product_id} x{self.quantity} | {self.status} | {self.submitted_at}"


class CurrencyMonthlyRate(models.Model):
    rate_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    currency = models.CharField(max_length=10)
    month = models.DateField()
    units_per_eur = models.DecimalField(max_digits=20, decimal_places=8)
    rate_to_eur = models.DecimalField(max_digits=20, decimal_places=10)
    source = models.TextField(default="frankfurter_ecb_monthly_average")
    source_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_currency_monthly_rates"
        verbose_name = "CRM Currency Monthly Rate"
        verbose_name_plural = "CRM Currency Monthly Rates"
        ordering = ["-month", "currency"]
        constraints = [
            models.UniqueConstraint(fields=["currency", "month"], name="uniq_crm_currency_month")
        ]
        indexes = [
            models.Index(fields=["currency", "month"], name="crm_fx_currency_month_idx"),
            models.Index(fields=["month"], name="crm_fx_month_idx"),
        ]

    def __str__(self):
        return f"{self.currency} {self.month:%Y-%m} | 1 {self.currency} = {self.rate_to_eur} EUR"


class CustomerOrderPhoneLink(models.Model):
    link_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(
        CustomerOrder,
        on_delete=models.CASCADE,
        related_name="phone_links",
    )
    customer_id = models.UUIDField(blank=True, null=True, db_index=True)
    normalized_phone = models.TextField(db_index=True)
    raw_phone = models.TextField(blank=True, null=True)
    is_primary = models.BooleanField(default=False)
    source = models.CharField(max_length=40, default="wyzbox")
    country_id = models.PositiveIntegerField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_customer_order_phone_links"
        verbose_name = "CRM Customer Order Phone Link"
        verbose_name_plural = "CRM Customer Order Phone Links"
        ordering = ["order_id", "-is_primary", "normalized_phone"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "normalized_phone"],
                name="uniq_crm_order_phone_link",
            )
        ]
        indexes = [
            models.Index(fields=["normalized_phone"], name="crm_ordphone_norm_idx"),
            models.Index(fields=["customer_id"], name="crm_ordphone_customer_idx"),
            models.Index(fields=["source", "country_id"], name="crm_ordphone_src_country_idx"),
        ]

    def __str__(self):
        return f"{self.order_id} | {self.normalized_phone}"


class CustomerConversionEvent(models.Model):
    EVENT_CHOICES = [
        ("sent", "Sent"),
        ("delivered", "Delivered"),
        ("opened", "Opened"),
        ("read", "Read"),
        ("clicked", "Clicked"),
        ("replied", "Replied"),
        ("lead", "Lead"),
        ("order_submitted", "Order submitted"),
        ("buy", "Buy"),
        ("paid", "Paid"),
        ("delivered_order", "Delivered order"),
        ("cancelled", "Cancelled"),
        ("returned", "Returned"),
    ]

    conversion_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_id = models.UUIDField(db_index=True)
    communication_event_id = models.UUIDField(blank=True, null=True, db_index=True)
    order_id = models.UUIDField(blank=True, null=True, db_index=True)
    channel = models.CharField(max_length=30, choices=CustomerChannelIdentity.CHANNEL_CHOICES)
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    product_id = models.TextField(blank=True, null=True)
    campaign_id = models.TextField(blank=True, null=True)
    conversation_id = models.TextField(blank=True, null=True)
    value = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    currency = models.CharField(max_length=10, blank=True, null=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "crm_conversion_events"
        verbose_name = "CRM Conversion Event"
        verbose_name_plural = "CRM Conversion Events"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["customer_id", "event_type", "occurred_at"]),
            models.Index(fields=["channel", "event_type"]),
            models.Index(fields=["product_id", "event_type"]),
            models.Index(fields=["conversation_id"]),
        ]

    def __str__(self):
        return f"{self.event_type} | {self.channel} | {self.customer_id}"


class ShortLink(models.Model):
    link_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=40, unique=True, db_index=True)
    target_url = models.TextField()
    title = models.TextField(blank=True, null=True)

    business_slug = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    source_channel = models.CharField(max_length=30, default="whatsapp")
    source_template = models.TextField(blank=True, null=True)
    source_message_id = models.TextField(blank=True, null=True)
    intent = models.TextField(blank=True, null=True)

    conversation_id = models.TextField(blank=True, null=True, db_index=True)
    contact_id = models.TextField(blank=True, null=True)
    channel_id = models.TextField(blank=True, null=True)
    customer_id = models.UUIDField(blank=True, null=True, db_index=True)
    phone = models.TextField(blank=True, null=True)

    product_id = models.TextField(blank=True, null=True, db_index=True)
    product_name = models.TextField(blank=True, null=True)
    campaign_id = models.TextField(blank=True, null=True)

    thank_you_enabled = models.BooleanField(default=True)
    thank_you_body = models.TextField(
        default="Thanks for checking it. If you need help choosing, just reply here."
    )
    thank_you_attempted_at = models.DateTimeField(blank=True, null=True)
    thank_you_sent_at = models.DateTimeField(blank=True, null=True)
    thank_you_message_id = models.TextField(blank=True, null=True)
    last_thank_you_error = models.TextField(blank=True, null=True)

    click_count = models.PositiveIntegerField(default=0)
    first_clicked_at = models.DateTimeField(blank=True, null=True)
    last_clicked_at = models.DateTimeField(blank=True, null=True)

    active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    created_by = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "short_links"
        verbose_name = "Short Link"
        verbose_name_plural = "Short Links"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business_slug", "conversation_id"], name="shortlink_biz_conv_idx"),
            models.Index(fields=["product_id", "created_at"], name="shortlink_product_idx"),
            models.Index(fields=["campaign_id"], name="shortlink_campaign_idx"),
            models.Index(fields=["active", "expires_at"], name="shortlink_active_exp_idx"),
        ]

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def __str__(self):
        return f"{self.code} -> {self.target_url}"


class ShortLinkClick(models.Model):
    click_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    link = models.ForeignKey(ShortLink, on_delete=models.CASCADE, related_name="clicks")
    clicked_at = models.DateTimeField(default=timezone.now)

    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    referer = models.TextField(blank=True, null=True)
    request_method = models.CharField(max_length=12, default="GET")
    query_params = models.JSONField(default=dict, blank=True)
    is_preview = models.BooleanField(default=False)

    thank_you_queued = models.BooleanField(default=False)
    thank_you_result = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "short_link_clicks"
        verbose_name = "Short Link Click"
        verbose_name_plural = "Short Link Clicks"
        ordering = ["-clicked_at"]
        indexes = [
            models.Index(fields=["link", "clicked_at"], name="shortclick_link_time_idx"),
            models.Index(fields=["ip_address"], name="shortclick_ip_idx"),
            models.Index(fields=["is_preview"], name="shortclick_preview_idx"),
        ]

    def __str__(self):
        return f"{self.link_id} | {self.clicked_at}"


class CustomerSegment(models.Model):
    AUDIENCE_CHOICES = [
        ("marketing", "Marketing"),
        ("transactional", "Transactional"),
        ("mixed", "Mixed"),
        ("suppression", "Suppression / do not contact"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("archived", "Archived"),
    ]

    segment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True, null=True)
    audience_type = models.CharField(max_length=30, choices=AUDIENCE_CHOICES, default="marketing")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")

    is_dynamic = models.BooleanField(default=False)
    country = models.CharField(max_length=20, blank=True, null=True)
    channel = models.CharField(max_length=30, blank=True, null=True)
    product_id = models.TextField(blank=True, null=True)
    crm_stage = models.CharField(max_length=40, blank=True, null=True)
    profile_status = models.CharField(max_length=40, blank=True, null=True)
    rules = models.JSONField(default=dict, blank=True)

    profile_count = models.IntegerField(default=0)
    last_built_at = models.DateTimeField(blank=True, null=True)
    created_by = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "crm_customer_segments"
        verbose_name = "CRM Customer Segment"
        verbose_name_plural = "CRM Customer Segments"
        ordering = ["audience_type", "name"]
        indexes = [
            models.Index(fields=["audience_type", "status"]),
            models.Index(fields=["country", "channel"]),
            models.Index(fields=["product_id"]),
            models.Index(fields=["crm_stage"]),
        ]

    def __str__(self):
        return f"{self.name} | {self.audience_type}"


class CustomerSegmentMembership(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("excluded", "Excluded"),
        ("removed", "Removed"),
    ]
    SOURCE_CHOICES = [
        ("manual", "Manual"),
        ("filtered_selection", "Filtered selection"),
        ("dynamic_rule", "Dynamic rule"),
        ("import", "Import"),
        ("system", "System"),
    ]

    membership_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    segment = models.ForeignKey(
        CustomerSegment,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    customer_id = models.UUIDField(db_index=True)
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES, default="manual")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="active")
    added_by = models.TextField(blank=True, null=True)
    added_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "crm_customer_segment_memberships"
        verbose_name = "CRM Segment Membership"
        verbose_name_plural = "CRM Segment Memberships"
        ordering = ["segment", "-added_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["segment", "customer_id"],
                name="uniq_crm_segment_customer",
            )
        ]
        indexes = [
            models.Index(fields=["segment", "status"]),
            models.Index(fields=["customer_id", "status"]),
        ]

    def __str__(self):
        return f"{self.segment_id} | {self.customer_id} | {self.status}"



class ConversationAnalysis(models.Model):
    analysis_id = models.UUIDField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        related_name="analyses",
        on_delete=models.DO_NOTHING,
    )
    product_id = models.TextField(blank=True, null=True)
    model = models.TextField(blank=True, null=True)
    prompt_version = models.TextField(default="ai_analysis_v1")
    analysis_status = models.TextField(blank=True, null=True)

    lead_score = models.IntegerField(blank=True, null=True)
    client_intent = models.TextField(blank=True, null=True)
    lead_stage = models.TextField(blank=True, null=True)
    main_objection = models.TextField(blank=True, null=True)
    sale_outcome = models.TextField(blank=True, null=True)

    summary = models.TextField(blank=True, null=True)
    missed_opportunity = models.TextField(blank=True, null=True)
    operator_issue = models.TextField(blank=True, null=True)
    workflow_issue = models.TextField(blank=True, null=True)
    recommended_action = models.TextField(blank=True, null=True)
    recommended_message = models.TextField(blank=True, null=True)

    raw_result = models.JSONField(blank=True, null=True)
    error = models.TextField(blank=True, null=True)

    analyzed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "analysis_results"
        managed = False
        verbose_name = "Conversation Analysis"
        verbose_name_plural = "Conversation Analyses"
        ordering = ["-analyzed_at"]

    def __str__(self):
        return f"{self.conversation_id} | score={self.lead_score}"


class ProductFeedSuggestion(models.Model):
    suggestion_id = models.UUIDField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        related_name="product_feed_suggestions",
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )
    analysis = models.ForeignKey(
        ConversationAnalysis,
        db_column="analysis_id",
        related_name="suggestions",
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )

    product_id = models.TextField(blank=True, null=True)
    suggestion_type = models.TextField()
    title = models.TextField(blank=True, null=True)
    suggested_question = models.TextField(blank=True, null=True)
    suggested_answer = models.TextField(blank=True, null=True)
    suggested_rule = models.TextField(blank=True, null=True)
    suggested_keyword = models.TextField(blank=True, null=True)

    reason = models.TextField(blank=True, null=True)
    evidence = models.TextField(blank=True, null=True)
    confidence_score = models.IntegerField(blank=True, null=True)

    status = models.TextField(default="pending_review")
    created_by = models.TextField(blank=True, null=True)
    reviewed_by = models.TextField(blank=True, null=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    applied_at = models.DateTimeField(blank=True, null=True)

    raw_payload = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "product_feed_suggestions"
        managed = False
        verbose_name = "Product Feed Suggestion"
        verbose_name_plural = "Product Feed Suggestions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product_id} | {self.suggestion_type} | {self.status}"



class AiSalesDashboardLink(models.Model):
    class Meta:
        managed = False
        verbose_name = "AI Sales Dashboard"
        verbose_name_plural = "AI Sales Dashboard"
        app_label = "superchatsync"

    def __str__(self):
        return "AI Sales Dashboard"


class KnowledgeCenterLink(models.Model):
    class Meta:
        managed = False
        verbose_name = "Knowledge Center"
        verbose_name_plural = "Knowledge Center"
        app_label = "superchatsync"

    def __str__(self):
        return "Knowledge Center"


class PeekoWorkspaceLink(models.Model):
    class Meta:
        managed = False
        verbose_name = "Peeko Workspace"
        verbose_name_plural = "Peeko Workspace"
        app_label = "superchatsync"

    def __str__(self):
        return "Peeko Workspace"



# --- Product Knowledge Imports ---
import uuid as _knowledge_uuid
from productfeed.models import Product as _KnowledgeProduct


class ProductKnowledgeImport(models.Model):
    import_id = models.UUIDField(
        primary_key=True,
        default=_knowledge_uuid.uuid4,
        editable=False,
    )

    product = models.ForeignKey(
        _KnowledgeProduct,
        db_column="product_id",
        to_field="product_id",
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )

    title = models.TextField(blank=True, null=True)
    source_file = models.FileField(upload_to="product_knowledge_imports/")
    original_filename = models.TextField(blank=True, null=True)

    status = models.TextField(default="uploaded")
    notes = models.TextField(blank=True, null=True)

    extracted_text = models.TextField(blank=True, null=True)
    extracted_char_count = models.IntegerField(default=0)
    suggestions_created_count = models.IntegerField(default=0)

    error = models.TextField(blank=True, null=True)
    created_by = models.TextField(blank=True, null=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    knowledge_package_status = models.TextField(default="not_created")
    knowledge_package_dir = models.TextField(blank=True, null=True)
    knowledge_package_error = models.TextField(blank=True, null=True)
    knowledge_package_created_at = models.DateTimeField(blank=True, null=True)
    package_suggestions_created_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "product_knowledge_imports"
        managed = False
        verbose_name = "Product Knowledge Import"
        verbose_name_plural = "Product Knowledge Imports"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.product_id or 'no-product'} | {self.title or self.source_file}"
# --- End Product Knowledge Imports ---



# --- Product Knowledge Items ---
class ProductKnowledgeItem(models.Model):
    item_id = models.UUIDField(primary_key=True, editable=False)

    knowledge_import = models.ForeignKey(
        ProductKnowledgeImport,
        db_column="import_id",
        on_delete=models.DO_NOTHING,
        related_name="knowledge_items",
    )

    product = models.ForeignKey(
        _KnowledgeProduct,
        db_column="product_id",
        to_field="product_id",
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
    )

    category = models.TextField()

    title = models.TextField(blank=True, null=True)
    question = models.TextField(blank=True, null=True)
    answer = models.TextField(blank=True, null=True)
    rule = models.TextField(blank=True, null=True)
    keyword = models.TextField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    price = models.TextField(blank=True, null=True)

    target_product_name = models.TextField(blank=True, null=True)
    target_product_id = models.TextField(blank=True, null=True)

    evidence = models.TextField(blank=True, null=True)
    confidence_score = models.IntegerField(default=70)
    priority = models.IntegerField(default=50)

    status = models.TextField(default="pending_review")

    applied_target_table = models.TextField(blank=True, null=True)
    applied_target_id = models.TextField(blank=True, null=True)
    apply_error = models.TextField(blank=True, null=True)

    raw_payload = models.JSONField(blank=True, null=True)

    reviewed_by = models.TextField(blank=True, null=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    applied_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "product_knowledge_items"
        managed = False
        verbose_name = "Product Knowledge Item"
        verbose_name_plural = "Product Knowledge Items"
        ordering = ["category", "-confidence_score", "-created_at"]

    def __str__(self):
        return f"{self.category} | {self.title or self.question or self.keyword or self.item_id}"
# --- End Product Knowledge Items ---

# Product Creative Library

import uuid
from pathlib import Path
from django.db import models


def product_creative_upload_to(instance, filename):
    ext = Path(filename).suffix.lower()
    return f"product_creatives/{instance.product_id}/{uuid.uuid4()}{ext}"


class ProductCreativeAsset(models.Model):
    asset_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product_id = models.TextField()

    asset_type = models.TextField(
        choices=[
            ("image", "Image"),
            ("video", "Video"),
            ("document", "Document"),
        ]
    )

    title = models.TextField()
    description = models.TextField()

    usage_context = models.TextField(blank=True, null=True)
    sales_stage = models.TextField(blank=True, null=True)
    intent = models.TextField(blank=True, null=True)
    next_best_action = models.TextField(blank=True, null=True)

    tags = models.JSONField(default=list, blank=True)
    priority = models.IntegerField(default=100)

    public_url = models.TextField(blank=True, null=True)

    use_superchat_file = models.BooleanField(default=False)
    superchat_file_id = models.TextField(blank=True, null=True)

    file = models.FileField(
        upload_to=product_creative_upload_to,
        db_column="storage_path",
        blank=True,
        null=True
    )

    original_filename = models.TextField(blank=True, null=True)
    mime_type = models.TextField(blank=True, null=True)
    file_size_bytes = models.BigIntegerField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_creative_assets"
        managed = False
        verbose_name = "Product Creative Asset"
        verbose_name_plural = "Product Creative Assets"

    def __str__(self):
        return f"{self.product_id} — {self.title}"


class BusinessClient(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("archived", "Archived"),
    ]

    business_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.TextField()
    domain = models.TextField(blank=True, null=True)
    default_language = models.CharField(max_length=12, default="en")
    default_currency = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_clients"
        verbose_name = "Business Client"
        verbose_name_plural = "Business Clients"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"


class WhatsappAgentInboxRoute(models.Model):
    AGENT_FITEXPRESS_PRODUCT = "fitexpress_product_agent"
    AGENT_PEEKO_BUSINESS = "peeko_business_agent"
    AGENT_CHOICES = [
        (AGENT_FITEXPRESS_PRODUCT, "Fitexpress product agent"),
        (AGENT_PEEKO_BUSINESS, "Peeko business agent"),
    ]

    route_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()

    channel_id = models.TextField(unique=True, blank=True, null=True)
    channel_phone = models.TextField(blank=True, null=True)
    channel_phone_digits = models.CharField(max_length=32, unique=True, blank=True, null=True)
    channel_name = models.TextField(blank=True, null=True)

    inbox_id = models.TextField(blank=True, null=True)
    inbox_name = models.TextField(blank=True, null=True)

    agent_type = models.CharField(max_length=40, choices=AGENT_CHOICES)
    business = models.ForeignKey(
        BusinessClient,
        on_delete=models.SET_NULL,
        related_name="whatsapp_agent_routes",
        blank=True,
        null=True,
    )
    default_product_id = models.TextField(blank=True, null=True)

    require_handle_status = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "whatsapp_agent_inbox_routes"
        verbose_name = "WhatsApp Agent Inbox Route"
        verbose_name_plural = "WhatsApp Agent Inbox Routes"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["active", "agent_type"], name="wa_route_active_agent_idx"),
            models.Index(fields=["inbox_id"], name="wa_route_inbox_idx"),
        ]

    @staticmethod
    def digits(value):
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    @classmethod
    def match_target(cls, target):
        channel_id = str(target.get("channel_id") or "").strip()
        channel_phone = (
            target.get("channel_phone_number")
            or target.get("channel_name")
            or target.get("channel_phone")
        )
        channel_digits = cls.digits(channel_phone)
        routes = cls.objects.filter(active=True)
        if channel_id:
            route = routes.filter(channel_id=channel_id).select_related("business").first()
            if route:
                return route
        if channel_digits:
            return routes.filter(channel_phone_digits=channel_digits).select_related("business").first()
        return None

    def save(self, *args, **kwargs):
        digits = self.digits(self.channel_phone or self.channel_name)
        self.channel_phone_digits = digits or None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} | {self.channel_phone or self.channel_id} -> {self.agent_type}"


class BusinessKnowledgeImportRun(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    run_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="knowledge_import_runs")
    source_url = models.TextField()
    source_type = models.CharField(max_length=40, default="website_crawl")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    pages_found = models.IntegerField(default=0)
    pages_crawled = models.IntegerField(default=0)
    products_found = models.IntegerField(default=0)
    products_imported = models.IntegerField(default=0)
    knowledge_items_created = models.IntegerField(default=0)
    media_assets_created = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)
    notes = models.TextField(blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_knowledge_import_runs"
        verbose_name = "Business Knowledge Import Run"
        verbose_name_plural = "Business Knowledge Import Runs"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["source_type"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.source_type} | {self.status} | {self.started_at:%Y-%m-%d %H:%M}"


class BusinessProduct(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("archived", "Archived"),
    ]

    product_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="products")
    external_id = models.TextField(blank=True, null=True)
    slug = models.SlugField(max_length=220)
    name = models.TextField()
    url = models.TextField(blank=True, null=True)
    vendor = models.TextField(blank=True, null=True)
    product_type = models.TextField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    tags = models.JSONField(default=list, blank=True)
    options = models.JSONField(default=list, blank=True)
    variants = models.JSONField(default=list, blank=True)
    currency = models.CharField(max_length=10, blank=True, null=True)
    min_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    max_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    source_payload = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_products"
        verbose_name = "Business Product"
        verbose_name_plural = "Business Products"
        ordering = ["business", "name"]
        constraints = [
            models.UniqueConstraint(fields=["business", "slug"], name="uniq_business_product_slug"),
            models.UniqueConstraint(
                fields=["business", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_business_product_external_id",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "name"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.name}"


class BusinessProductRanking(models.Model):
    RANK_TYPE_CHOICES = [
        ("best_seller", "Best seller"),
        ("trending", "Trending"),
        ("category_trending", "Category trending"),
    ]

    ranking_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="product_rankings")
    product = models.ForeignKey(BusinessProduct, on_delete=models.CASCADE, related_name="rankings")
    rank_type = models.CharField(max_length=40, choices=RANK_TYPE_CHOICES, default="best_seller")
    collection_slug = models.SlugField(max_length=220, blank=True, null=True)
    collection_title = models.TextField(blank=True, null=True)
    source_url = models.TextField(blank=True, null=True)
    rank = models.PositiveIntegerField(default=0)
    score = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_product_rankings"
        verbose_name = "Business Product Ranking"
        verbose_name_plural = "Business Product Rankings"
        ordering = ["business", "rank_type", "rank", "product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "product", "rank_type", "collection_slug"],
                name="uniq_business_product_ranking",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "rank_type", "active"]),
            models.Index(fields=["business", "collection_slug", "active"]),
            models.Index(fields=["product", "active"]),
            models.Index(fields=["rank"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.rank_type} | {self.rank} | {self.product.name}"


class BusinessCrawlPage(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("crawled", "Crawled"),
        ("error", "Error"),
        ("skipped", "Skipped"),
    ]

    page_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="crawl_pages")
    import_run = models.ForeignKey(
        BusinessKnowledgeImportRun,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="crawl_pages",
    )
    product = models.ForeignKey(
        BusinessProduct,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="crawl_pages",
    )
    url = models.TextField()
    url_hash = models.CharField(max_length=64)
    canonical_url = models.TextField(blank=True, null=True)
    page_type = models.CharField(max_length=40, default="page")
    title = models.TextField(blank=True, null=True)
    meta_description = models.TextField(blank=True, null=True)
    language = models.CharField(max_length=12, default="en")
    extracted_text = models.TextField(blank=True, null=True)
    extracted_char_count = models.IntegerField(default=0)
    text_hash = models.CharField(max_length=64, blank=True, null=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    http_status = models.IntegerField(blank=True, null=True)
    error = models.TextField(blank=True, null=True)
    source_payload = models.JSONField(default=dict, blank=True)
    crawled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_crawl_pages"
        verbose_name = "Business Crawl Page"
        verbose_name_plural = "Business Crawl Pages"
        ordering = ["business", "page_type", "url"]
        constraints = [
            models.UniqueConstraint(fields=["business", "url_hash"], name="uniq_business_crawl_page_url_hash"),
        ]
        indexes = [
            models.Index(fields=["business", "page_type"]),
            models.Index(fields=["business", "status"]),
            models.Index(fields=["text_hash"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.page_type} | {self.title or self.url}"


class BusinessKnowledgeItem(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("archived", "Archived"),
    ]
    SCOPE_CHOICES = [
        ("general", "General"),
        ("category", "Category"),
        ("product", "Product"),
    ]
    TYPE_CHOICES = [
        ("general_policy", "General policy"),
        ("brand_voice", "Brand voice"),
        ("product_fact", "Product fact"),
        ("product_benefit", "Product benefit"),
        ("product_usage", "Product usage"),
        ("product_specs", "Product specs"),
        ("product_faq", "Product FAQ"),
        ("pricing_offer", "Pricing / offer"),
        ("objection_answer", "Objection answer"),
        ("raw_section", "Raw section"),
    ]

    item_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="knowledge_items")
    import_run = models.ForeignKey(
        BusinessKnowledgeImportRun,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="knowledge_items",
    )
    page = models.ForeignKey(
        BusinessCrawlPage,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="knowledge_items",
    )
    product = models.ForeignKey(
        BusinessProduct,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="knowledge_items",
    )
    scope = models.CharField(max_length=30, choices=SCOPE_CHOICES, default="general")
    item_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    title = models.TextField(blank=True, null=True)
    question = models.TextField(blank=True, null=True)
    answer = models.TextField(blank=True, null=True)
    body = models.TextField()
    evidence = models.TextField(blank=True, null=True)
    source_url = models.TextField(blank=True, null=True)
    source_url_hash = models.CharField(max_length=64, blank=True, null=True)
    source_section = models.TextField(blank=True, null=True)
    language = models.CharField(max_length=12, default="en")
    confidence_score = models.IntegerField(default=75)
    priority = models.IntegerField(default=50)
    content_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    metadata = models.JSONField(default=dict, blank=True)
    reviewed_by = models.TextField(blank=True, null=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_knowledge_items"
        verbose_name = "Business Knowledge Item"
        verbose_name_plural = "Business Knowledge Items"
        ordering = ["business", "scope", "item_type", "-confidence_score", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "source_url_hash", "item_type", "content_hash"],
                name="uniq_business_knowledge_source_hash",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["business", "scope", "item_type"]),
            models.Index(fields=["product", "item_type"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.scope} | {self.item_type} | {self.title or self.item_id}"


class BusinessMediaAsset(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("archived", "Archived"),
    ]

    asset_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(BusinessClient, on_delete=models.CASCADE, related_name="media_assets")
    import_run = models.ForeignKey(
        BusinessKnowledgeImportRun,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="media_assets",
    )
    page = models.ForeignKey(
        BusinessCrawlPage,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="media_assets",
    )
    product = models.ForeignKey(
        BusinessProduct,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="media_assets",
    )
    asset_type = models.CharField(max_length=30, default="image")
    image_role = models.CharField(max_length=40, default="unknown")
    title = models.TextField(blank=True, null=True)
    alt_text = models.TextField(blank=True, null=True)
    source_url = models.TextField()
    source_url_hash = models.CharField(max_length=64)
    local_path = models.TextField(blank=True, null=True)
    mime_type = models.TextField(blank=True, null=True)
    file_size_bytes = models.BigIntegerField(blank=True, null=True)
    width = models.IntegerField(blank=True, null=True)
    height = models.IntegerField(blank=True, null=True)
    source_page_url = models.TextField(blank=True, null=True)
    language = models.CharField(max_length=12, default="en")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="draft")
    metadata = models.JSONField(default=dict, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "business_media_assets"
        verbose_name = "Business Media Asset"
        verbose_name_plural = "Business Media Assets"
        ordering = ["business", "product", "image_role", "title"]
        constraints = [
            models.UniqueConstraint(fields=["business", "source_url_hash"], name="uniq_business_media_source_hash"),
        ]
        indexes = [
            models.Index(fields=["business", "status"]),
            models.Index(fields=["product", "image_role"]),
            models.Index(fields=["asset_type"]),
        ]

    def __str__(self):
        return f"{self.business.slug} | {self.asset_type} | {self.title or self.source_url}"


class AiLlmCallLog(models.Model):
    call_id = models.UUIDField(primary_key=True)
    conversation_id = models.TextField(blank=True, null=True)
    product_id = models.TextField(blank=True, null=True)

    provider = models.TextField(blank=True, null=True)
    model = models.TextField(blank=True, null=True)
    purpose = models.TextField(blank=True, null=True)

    prompt_text = models.TextField(blank=True, null=True)
    request_json = models.JSONField(blank=True, null=True)

    response_status = models.IntegerField(blank=True, null=True)
    raw_response = models.JSONField(blank=True, null=True)
    output_text = models.TextField(blank=True, null=True)

    error = models.TextField(blank=True, null=True)
    duration_ms = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "ai_llm_call_logs"
        verbose_name = "AI LLM Call Log"
        verbose_name_plural = "AI LLM Call Logs"

    def __str__(self):
        return f"{self.created_at} | {self.purpose} | {self.conversation_id}"


class AiResponseProcessRun(models.Model):
    run_id = models.UUIDField(primary_key=True)
    conversation_id = models.TextField(blank=True, null=True)
    product_id = models.TextField(blank=True, null=True)
    client_message = models.TextField(blank=True, null=True)

    status = models.TextField(blank=True, null=True)
    final_action = models.TextField(blank=True, null=True)
    final_score = models.IntegerField(blank=True, null=True)
    final_body = models.TextField(blank=True, null=True)
    final_buttons = models.JSONField(blank=True, null=True)

    attempts_count = models.IntegerField(blank=True, null=True)
    error = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "ai_response_process_runs"
        verbose_name = "AI Response Process Run"
        verbose_name_plural = "AI Response Process Runs"

    def __str__(self):
        return f"{self.created_at} | {self.status} | {self.conversation_id}"


class AiResponseProcessStep(models.Model):
    step_id = models.UUIDField(primary_key=True)
    run = models.ForeignKey(
        AiResponseProcessRun,
        db_column="run_id",
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name="steps",
    )

    conversation_id = models.TextField(blank=True, null=True)
    product_id = models.TextField(blank=True, null=True)

    step_name = models.TextField(blank=True, null=True)
    attempt = models.IntegerField(blank=True, null=True)

    input_json = models.JSONField(blank=True, null=True)
    output_json = models.JSONField(blank=True, null=True)

    approved = models.BooleanField(blank=True, null=True)
    score = models.IntegerField(blank=True, null=True)
    severity = models.TextField(blank=True, null=True)
    action = models.TextField(blank=True, null=True)

    fail_reasons = models.JSONField(blank=True, null=True)
    blocking_issues = models.JSONField(blank=True, null=True)
    feedback_for_repair = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "ai_response_process_steps"
        verbose_name = "AI Response Process Step"
        verbose_name_plural = "AI Response Process Steps"

    def __str__(self):
        return f"{self.created_at} | {self.step_name} | score={self.score}"


class AiDecisionRoadmap(AiResponseProcessRun):
    class Meta:
        proxy = True
        verbose_name = "AI Decision Roadmap"
        verbose_name_plural = "AI Decision Roadmap"


class LandingLeadSubmission(models.Model):
    STATUS_RECEIVED = "received"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_VALIDATION_FAILED = "validation_failed"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Received"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_VALIDATION_FAILED, "Validation failed"),
    ]

    lead_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RECEIVED, db_index=True)

    customer_name = models.TextField(blank=True, null=True)
    customer_phone = models.TextField(blank=True, null=True, db_index=True)
    customer_region = models.PositiveIntegerField(blank=True, null=True, db_index=True)
    customer_address = models.TextField(blank=True, null=True)
    quantity = models.PositiveIntegerField(blank=True, null=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    product = models.TextField(blank=True, null=True, db_index=True)
    referral = models.TextField(blank=True, null=True)
    customer_comment = models.TextField(blank=True, null=True)

    request_payload = models.JSONField(default=dict, blank=True)
    forwarded_payload = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(default=dict, blank=True)

    forward_url = models.TextField(blank=True, null=True)
    upstream_http_status = models.PositiveIntegerField(blank=True, null=True)
    upstream_response = models.TextField(blank=True, null=True)
    external_order_id = models.TextField(blank=True, null=True, db_index=True)
    error = models.TextField(blank=True, null=True)

    source_ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    origin = models.TextField(blank=True, null=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "landing_lead_submissions"
        verbose_name = "Landing Lead Submission"
        verbose_name_plural = "Landing Lead Submissions"
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["status", "received_at"], name="landlead_status_time_idx"),
            models.Index(fields=["product", "received_at"], name="landlead_product_time_idx"),
        ]

    def __str__(self):
        return f"{self.received_at} | {self.customer_phone or '-'} | {self.status}"
