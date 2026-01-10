from django.contrib import admin
from .models import Order, OrderItem, Payment


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ()

    fields = (
        'item_name',
        'quantity',
        'unit_price',
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
