from django.db import models
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone


class Product(models.Model):
    name = models.CharField(max_length=255, unique=True)
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Internal cost per unit (not shown on customer receipt)",
        default=0,
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class PrintJob(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_CLAIMED = "CLAIMED"
    STATUS_PRINTED = "PRINTED"
    STATUS_FAILED = "FAILED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CLAIMED, "Claimed"),
        (STATUS_PRINTED, "Printed"),
        (STATUS_FAILED, "Failed"),
    ]

    JOB_SALE_RECEIPT = "SALE_RECEIPT"
    JOB_REFUND_RECEIPT = "REFUND_RECEIPT"
    JOB_PROFIT_REPORT = "PROFIT_REPORT"

    JOB_TYPE_CHOICES = [
        (JOB_SALE_RECEIPT, "Sale receipt"),
        (JOB_REFUND_RECEIPT, "Refund receipt"),
        (JOB_PROFIT_REPORT, "Profit report"),
    ]

    COPY_CUSTOMER = "CUSTOMER"
    COPY_STORE = "STORE"
    COPY_PROFIT = "PROFIT"

    COPY_TYPE_CHOICES = [
        (COPY_CUSTOMER, "Customer"),
        (COPY_STORE, "Store"),
        (COPY_PROFIT, "Profit"),
    ]

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    job_type = models.CharField(max_length=20, choices=JOB_TYPE_CHOICES)
    copy_type = models.CharField(max_length=10, choices=COPY_TYPE_CHOICES)
    payload = models.JSONField()

    target_device_id = models.CharField(max_length=64, null=True, blank=True)
    claimed_by_device_id = models.CharField(max_length=64, null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    priority = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["status", "target_device_id", "-priority", "created_at"]),
            models.Index(fields=["status", "-priority", "created_at"]),
            models.Index(fields=["claimed_by_device_id", "claimed_at"]),
        ]


class Order(models.Model):
    STATUS_CHOICES = [
        ("unpaid", "Unpaid"),
        ("paid", "Paid"),
        ("refunded", "Refunded"),
    ]

    order_number = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="unpaid")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def total_amount(self):
        total_expr = ExpressionWrapper(
            F("quantity") * F("selling_price"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total = self.items.aggregate(total=Sum(total_expr))["total"]
        return total or 0

    def total_profit(self):
        profit_expr = ExpressionWrapper(
            (F("selling_price") - F("cost_price_snapshot")) * F("quantity"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total = self.items.aggregate(total=Sum(profit_expr))["total"]
        return total or 0


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(
        Product,
        related_name="order_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    item_name = models.CharField(
        max_length=255,
        help_text="Include unit in name if needed (e.g. Cement bag, Steel meter)",
    )
    box = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Optional box/pack label shown on receipts when provided",
    )
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Selling price per unit (entered at POS time)",
    )
    cost_price_snapshot = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Internal cost per unit captured at time of sale",
        default=0,
    )

    def line_total(self):
        return self.quantity * self.selling_price

    def line_profit(self):
        return (self.selling_price - self.cost_price_snapshot) * self.quantity

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"


class Payment(models.Model):
    PAYMENT_METHODS = [
        ("cash", "Cash"),
        ("bank", "Bank Transfer"),
        ("wave", "Wave"),
        ("aps", "APS"),
    ]

    order = models.ForeignKey(Order, related_name="payments", on_delete=models.PROTECT)
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    amount_received = models.DecimalField(max_digits=10, decimal_places=2)
    change_given = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.method.upper()} payment for Order #{self.order.order_number}"
