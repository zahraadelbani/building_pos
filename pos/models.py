from decimal import Decimal

from django.db import models
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone


class Product(models.Model):
    code = models.PositiveIntegerField(unique=True, null=True, blank=True)
    name = models.CharField(max_length=255, unique=True)
    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Internal cost per unit (not shown on customer receipt)",
        default=0,
    )
    selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Default selling price per unit (POS can override per bill)",
        default=0,
    )
    stock_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Current quantity available for sale",
    )
    low_stock_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=5,
        help_text="Show low-stock warning at or below this quantity",
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name

    def stock_status(self):
        if self.stock_quantity <= 0:
            return "out"
        if self.low_stock_threshold > 0 and self.stock_quantity <= self.low_stock_threshold:
            return "low"
        return "ok"

    def save(self, *args, **kwargs):
        if self.code is None:
            last_code = Product.objects.aggregate(max_code=models.Max("code"))["max_code"] or 0
            self.code = last_code + 1
        super().save(*args, **kwargs)


class Expense(models.Model):
    CATEGORY_RENT = "rent"
    CATEGORY_UTILITIES = "utilities"
    CATEGORY_SUPPLIES = "supplies"
    CATEGORY_SALARIES = "salaries"
    CATEGORY_TRANSPORT = "transport"
    CATEGORY_REPAIRS = "repairs"
    CATEGORY_OTHER = "other"

    CATEGORY_CHOICES = [
        (CATEGORY_RENT, "Rent"),
        (CATEGORY_UTILITIES, "Utilities"),
        (CATEGORY_SUPPLIES, "Supplies"),
        (CATEGORY_SALARIES, "Salaries"),
        (CATEGORY_TRANSPORT, "Transport"),
        (CATEGORY_REPAIRS, "Repairs"),
        (CATEGORY_OTHER, "Other"),
    ]

    PAYMENT_CASH = "cash"
    PAYMENT_WAVE = "wave"
    PAYMENT_BANK = "bank"
    PAYMENT_APS = "aps"

    PAYMENT_METHODS = [
        (PAYMENT_CASH, "Cash"),
        (PAYMENT_WAVE, "Wave"),
        (PAYMENT_BANK, "Bank"),
        (PAYMENT_APS, "APS"),
    ]

    date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_OTHER)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default=PAYMENT_CASH)
    bank_name = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["category"]),
            models.Index(fields=["payment_method"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} - {self.description}"


class StockMovement(models.Model):
    TYPE_RESTOCK = "restock"
    TYPE_ADJUSTMENT = "adjustment"
    TYPE_SALE = "sale"
    TYPE_REFUND = "refund"

    TYPE_CHOICES = [
        (TYPE_RESTOCK, "Restock"),
        (TYPE_ADJUSTMENT, "Adjustment"),
        (TYPE_SALE, "Sale"),
        (TYPE_REFUND, "Refund"),
    ]

    product = models.ForeignKey(Product, related_name="stock_movements", on_delete=models.CASCADE)
    order = models.ForeignKey(
        "Order",
        related_name="stock_movements",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    movement_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["product", "-occurred_at"]),
            models.Index(fields=["movement_type", "-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.product} {self.quantity_delta} ({self.get_movement_type_display()})"


class StockBatch(models.Model):
    """A FIFO layer of stock that keeps the prices paid and charged for that delivery."""

    product = models.ForeignKey(Product, related_name="stock_batches", on_delete=models.CASCADE)
    initial_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    remaining_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    received_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["product", "id"])]

    def __str__(self):
        return f"{self.product} batch ({self.remaining_quantity} remaining)"


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
    JOB_DAY_SUMMARY = "DAY_SUMMARY"
    JOB_MONTH_SUMMARY = "MONTH_SUMMARY"

    JOB_TYPE_CHOICES = [
        (JOB_SALE_RECEIPT, "Sale receipt"),
        (JOB_REFUND_RECEIPT, "Refund receipt"),
        (JOB_PROFIT_REPORT, "Profit report"),
        (JOB_DAY_SUMMARY, "Day summary"),
        (JOB_MONTH_SUMMARY, "Month summary"),
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
    DISCOUNT_NONE = "none"
    DISCOUNT_AMOUNT = "amount"
    DISCOUNT_PERCENT = "percent"

    DISCOUNT_TYPE_CHOICES = [
        (DISCOUNT_NONE, "No discount"),
        (DISCOUNT_AMOUNT, "Amount"),
        (DISCOUNT_PERCENT, "Percent"),
    ]

    STATUS_CHOICES = [
        ("unpaid", "Unpaid"),
        ("paid", "Paid"),
        ("refunded", "Refunded"),
    ]

    order_number = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="unpaid")
    is_active = models.BooleanField(default=True)
    customer_name = models.CharField(max_length=255, blank=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_TYPE_CHOICES, default=DISCOUNT_NONE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock_deducted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def subtotal_amount(self):
        total_expr = ExpressionWrapper(
            F("quantity") * F("selling_price"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total = self.items.aggregate(total=Sum(total_expr))["total"]
        return total or 0

    def discount_amount(self):
        subtotal = self.subtotal_amount()
        if self.discount_type == self.DISCOUNT_AMOUNT:
            discount = self.discount_value
        elif self.discount_type == self.DISCOUNT_PERCENT:
            discount = subtotal * self.discount_value / 100
        else:
            discount = 0
        if discount < 0:
            return 0
        if discount > subtotal:
            return subtotal
        return discount

    def total_amount(self):
        return self.subtotal_amount() - self.discount_amount()

    def total_profit(self):
        profit_expr = ExpressionWrapper(
            (F("selling_price") - F("cost_price_snapshot")) * F("quantity"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total = self.items.aggregate(total=Sum(profit_expr))["total"]
        return (total or 0) - self.discount_amount()

    def refunded_amount(self):
        total = self.refunds.aggregate(total=Sum("amount"))["total"]
        return total or Decimal("0")

    def refunded_profit(self):
        total = self.refunds.aggregate(total=Sum("profit_amount"))["total"]
        return total or Decimal("0")

    def net_amount(self):
        net = Decimal(str(self.total_amount())) - Decimal(str(self.refunded_amount()))
        return net if net > 0 else Decimal("0")

    def refund_status_label(self):
        refunded = Decimal(str(self.refunded_amount()))
        if refunded <= 0:
            return self.get_status_display()
        if self.net_amount() <= 0:
            return "Refunded"
        return "Partial refund"


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

    def refunded_quantity(self):
        total = self.refund_items.aggregate(total=Sum("quantity"))["total"]
        return total or Decimal("0")

    def refundable_quantity(self):
        remaining = Decimal(str(self.quantity)) - Decimal(str(self.refunded_quantity()))
        return remaining if remaining > 0 else Decimal("0")

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


class Refund(models.Model):
    PAYMENT_METHODS = Payment.PAYMENT_METHODS

    order = models.ForeignKey(Order, related_name="refunds", on_delete=models.PROTECT)
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    bank_name = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    refunded_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-refunded_at", "-id"]
        indexes = [
            models.Index(fields=["order", "-refunded_at"]),
            models.Index(fields=["method", "-refunded_at"]),
            models.Index(fields=["refunded_at"]),
        ]

    def __str__(self):
        return f"{self.method.upper()} refund for Order #{self.order.order_number}"


class RefundItem(models.Model):
    refund = models.ForeignKey(Refund, related_name="items", on_delete=models.CASCADE)
    order_item = models.ForeignKey(OrderItem, related_name="refund_items", on_delete=models.PROTECT)
    product = models.ForeignKey(
        Product,
        related_name="refund_items",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    item_name = models.CharField(max_length=255)
    box = models.CharField(max_length=50, null=True, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price_snapshot = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    profit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def line_total(self):
        return self.amount

    def line_profit(self):
        return self.profit_amount

    def __str__(self):
        return f"Refund {self.item_name} x {self.quantity}"
