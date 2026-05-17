from django.contrib import admin
from .models import Expense, Order, OrderItem, Payment, PrintJob, Product, StockMovement


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ()

    fields = (
        "product",
        "item_name",
        "quantity",
        "selling_price",
        "cost_price_snapshot",
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'status',
        'customer_name',
        'discount_type',
        'discount_value',
        'created_at',
        'total_amount',
    )

    list_filter = ('status',)
    ordering = ('-order_number',)

    inlines = [OrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        'order',
        'method',
        'amount_received',
        'change_given',
        'paid_at',
    )

    list_filter = ('method',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "cost_price",
        "selling_price",
        "stock_quantity",
        "low_stock_threshold",
        "created_at",
    )
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "category",
        "description",
        "payment_method",
        "bank_name",
        "amount",
        "created_at",
    )
    list_filter = ("category", "payment_method", "date")
    search_fields = ("description", "notes")
    ordering = ("-date", "-created_at")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "product",
        "movement_type",
        "quantity_delta",
        "reason",
        "order",
    )
    list_filter = ("movement_type", "occurred_at")
    search_fields = ("product__name", "reason", "note")
    ordering = ("-occurred_at", "-id")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "job_type",
        "copy_type",
        "target_device_id",
        "claimed_by_device_id",
        "attempts",
        "priority",
        "created_at",
    )
    list_filter = ("status", "job_type", "copy_type")
    search_fields = ("target_device_id", "claimed_by_device_id", "last_error")
    ordering = ("-priority", "created_at")
