from django.db import models
from django.db.models import F, Sum
from django.utils import timezone


class Product(models.Model):
    name = models.CharField(max_length=255, unique=True)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Default selling price per unit",
    )
    default_quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=1,
        help_text="Default quantity used when adding to receipt",
    )
    stock_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Current stock on hand",
    )
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


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
        total = self.items.aggregate(total=Sum(F("quantity") * F("unit_price")))["total"]
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
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Cost per single quantity",
    )

    def line_total(self):
        return self.quantity * self.unit_price

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
