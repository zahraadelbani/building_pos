from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import Max
from .models import Order, OrderItem, Payment


def create_new_order(request):
    last_number = Order.objects.aggregate(
        max_num=Max('order_number')
    )['max_num'] or 0

    order = Order.objects.create(
        order_number=last_number + 1,
        status='unpaid'
    )

    return redirect('pos_order', order_id=order.id)


def pos_home(request, order_id=None):
    # All open tabs (unpaid orders)
    open_orders = Order.objects.filter(status='unpaid', is_active=True).order_by('order_number')

    # If no unpaid orders exist → create one
    if not open_orders.exists():
        return redirect('create_new_order')

    # Determine active order
    if order_id:
        order = get_object_or_404(Order, id=order_id, status='unpaid')
    else:
        order = open_orders.first()

    # Add item to active order
    if request.method == 'POST':
        item_name = request.POST.get('item_name', '').strip()
        quantity = request.POST.get('quantity')
        unit_price = request.POST.get('unit_price')

        if item_name and quantity and unit_price:
            OrderItem.objects.create(
                order=order,
                item_name=item_name,
                quantity=quantity,
                unit_price=unit_price
            )

        return redirect('pos_order', order_id=order.id)

    context = {
        'open_orders': open_orders,
        'order': order,
        'items': order.items.all(),
        'total': order.total_amount(),
    }

    return render(request, 'pos/pos.html', context)

def pay_later(request, order_id):
    order = get_object_or_404(Order, id=order_id, status='unpaid')
    order.is_active = False
    order.save()
    return redirect('pos_home')


def unpaid_orders(request):
    query = request.GET.get('q', '')
    orders = Order.objects.filter(status='unpaid', is_active=False)

    if query:
        orders = orders.filter(order_number__icontains=query)

    return render(request, 'pos/unpaid_orders.html', {
        'orders': orders,
        'query': query,
    })


def resume_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, status='unpaid')
    order.is_active = True
    order.save()
    return redirect('pos_order', order_id=order.id)

def pay_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status != 'unpaid':
        return redirect('pos_home')

    error = None
    total = order.total_amount()
    change = Decimal('0.00')

    if request.method == 'POST':
        method = request.POST.get('method')
        amount_received = request.POST.get('amount_received')
        bank_name = request.POST.get('bank_name', '').strip()

        try:
            amount_received = Decimal(amount_received)
        except:
            error = "Invalid amount entered."

        if not error:
            if method == 'cash':
                if amount_received < total:
                    error = "Amount received is LESS than total."
                else:
                    change = amount_received - total
            else:
                if amount_received != total:
                    error = "For non-cash payments, amount must equal total."
                if method == 'bank' and not bank_name:
                    error = "Bank name is required for bank transfers."
                change = Decimal('0.00')

        if not error:
            Payment.objects.create(
                order=order,
                method=method,
                bank_name=bank_name if method == 'bank' else None,
                amount_received=amount_received,
                change_given=change
            )

            order.status = 'paid'
            order.is_active = False
            order.save()

            # DO NOT REDIRECT — SHOW RESULT
            return render(request, 'pos/payment_success.html', {
                'order': order,
                'total': total,
                'amount_received': amount_received,
                'change': change,
                'method': method,
            })

    return render(request, 'pos/payment.html', {
        'order': order,
        'total': total,
        'error': error,
    })

def print_receipt(request, order_id, copy_type):
    order = get_object_or_404(Order, id=order_id)

    # ❌ Block printing refunded orders
    if order.status == 'refunded':
        return render(request, 'pos/refunded_notice.html', {
            'order': order
        })

    if copy_type not in ['customer', 'store']:
        return redirect('pos_home')

    payment = order.payments.last()

    return render(request, 'pos/receipt.html', {
        'order': order,
        'items': order.items.all(),
        'payment': payment,
        'copy_type': copy_type,
        'total': order.total_amount(),
    })


def refund_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    # Only paid orders can be refunded
    if order.status != 'paid':
        return redirect('pos_home')

    if request.method == 'POST':
        order.status = 'refunded'
        order.is_active = False
        order.save()
        return redirect('pos_home')

    return render(request, 'pos/refund_confirm.html', {
        'order': order,
        'total': order.total_amount(),
    })
