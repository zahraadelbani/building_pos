from django.contrib import admin
from .models import Order, OrderItem, Payment, PrintJob, Product


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
        "created_at",
    )
    search_fields = ("name",)
    ordering = ("name",)


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
