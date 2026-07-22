from datetime import datetime
from calendar import month_name
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Expense, Order, OrderItem, Payment, PrintJob, Product, Refund, RefundItem, StockBatch, StockMovement
from .printing import (
    enqueue_day_summary,
    enqueue_month_summary,
    enqueue_profit_report,
    enqueue_refund_receipt,
    enqueue_sale_copy,
    enqueue_sale_receipt,
)


DEVICE_ID_SESSION_KEY = "pos_device_id"
DEVICE_ID_COOKIE_NAME = "pos_device_id"
DEVICE_ID_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _next_product_code():
    return (Product.objects.aggregate(max_code=Max("code"))["max_code"] or 0) + 1


def _product_code_ordering():
    return ("code", "name", "id")


def _sum_expenses(expenses):
    return expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")


def _safe_next_url(request, fallback):
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return fallback


def _is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _order_product_quantities(order):
    quantities = {}
    for item in order.items.exclude(product__isnull=True).select_related("product"):
        quantities[item.product_id] = quantities.get(item.product_id, Decimal("0")) + Decimal(item.quantity)
    return quantities


def _available_batches(product):
    return product.stock_batches.select_for_update().filter(remaining_quantity__gt=0).order_by("id")


def _ensure_legacy_batch(product):
    """Represent stock created before FIFO batches were introduced."""
    if product.stock_quantity > 0 and not product.stock_batches.filter(remaining_quantity__gt=0).exists():
        StockBatch.objects.create(
            product=product,
            initial_quantity=product.stock_quantity,
            remaining_quantity=product.stock_quantity,
            cost_price=product.cost_price,
            selling_price=product.selling_price,
            received_at=product.created_at,
        )


def _consume_fifo_batches(product, quantity):
    """Consume available priced stock oldest-first; oversold quantity has no batch."""
    _ensure_legacy_batch(product)
    remaining = Decimal(str(quantity))
    for batch in _available_batches(product):
        if remaining <= 0:
            break
        used = min(remaining, batch.remaining_quantity)
        batch.remaining_quantity -= used
        batch.save(update_fields=["remaining_quantity"])
        remaining -= used

    active = _available_batches(product).first()
    if active and (product.cost_price != active.cost_price or product.selling_price != active.selling_price):
        product.cost_price = active.cost_price
        product.selling_price = active.selling_price
        product.save(update_fields=["cost_price", "selling_price"])


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _order_refund_scale(order):
    subtotal = Decimal(str(order.subtotal_amount()))
    if subtotal <= 0:
        return Decimal("1")
    return Decimal(str(order.total_amount())) / subtotal


def _refund_amount_for(order, gross_amount):
    return _money(Decimal(str(gross_amount)) * _order_refund_scale(order))


def _refund_profit_for(order, gross_profit):
    return _money(Decimal(str(gross_profit)) * _order_refund_scale(order))


def _attach_refund_state(order):
    refunded_amount = Decimal(str(order.refunded_amount()))
    order.refunded_total = refunded_amount
    order.net_total = order.net_amount()
    order.refund_display = order.refund_status_label()
    return order


def _refund_context_items(order):
    items = []
    for item in order.items.select_related("product").order_by("id"):
        refunded_quantity = Decimal(str(item.refunded_quantity()))
        refundable_quantity = Decimal(str(item.quantity)) - refunded_quantity
        if refundable_quantity < 0:
            refundable_quantity = Decimal("0")
        refund_unit_amount = _refund_amount_for(order, item.selling_price)
        item.refunded_quantity_value = refunded_quantity
        item.refundable_quantity_value = refundable_quantity
        item.refund_unit_amount = refund_unit_amount
        item.refundable_amount = _money(refundable_quantity * refund_unit_amount)
        items.append(item)
    return items


def _refunds_payment_breakdown(refunds):
    totals = {value: Decimal("0") for value, _label in Payment.PAYMENT_METHODS}
    for refund in refunds:
        if refund.method in totals:
            totals[refund.method] += Decimal(str(refund.amount or 0))
    return totals


def _net_payment_totals(payment_totals, refund_totals):
    return {
        key: payment_totals.get(key, Decimal("0")) - refund_totals.get(key, Decimal("0"))
        for key, _label in Payment.PAYMENT_METHODS
    }


def _expenses_payment_breakdown(expenses):
    totals = {value: Decimal("0") for value, _label in Expense.PAYMENT_METHODS}
    for expense in expenses:
        if expense.payment_method in totals:
            totals[expense.payment_method] += Decimal(str(expense.amount or 0))
    return totals


def _deduct_order_stock(order):
    if order.stock_deducted_at:
        return

    quantities = _order_product_quantities(order)
    if not quantities:
        order.stock_deducted_at = timezone.now()
        order.save(update_fields=["stock_deducted_at"])
        return

    products = {
        product.id: product
        for product in Product.objects.select_for_update().filter(id__in=quantities.keys())
    }
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if not product:
            continue
        product.stock_quantity -= quantity
        product.save(update_fields=["stock_quantity"])
        _consume_fifo_batches(product, quantity)
        StockMovement.objects.create(
            product=product,
            order=order,
            movement_type=StockMovement.TYPE_SALE,
            quantity_delta=-quantity,
            reason=f"Sale order #{order.order_number}",
        )

    order.stock_deducted_at = timezone.now()
    order.save(update_fields=["stock_deducted_at"])


def _restore_order_stock(order, reason_prefix="Refund"):
    if not order.stock_deducted_at:
        return

    quantities = {}
    for item in order.items.exclude(product__isnull=True).select_related("product"):
        quantity = Decimal(str(item.refundable_quantity()))
        if quantity > 0:
            quantities[item.product_id] = quantities.get(item.product_id, Decimal("0")) + quantity
    products = {
        product.id: product
        for product in Product.objects.select_for_update().filter(id__in=quantities.keys())
    }
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if not product:
            continue
        product.stock_quantity += quantity
        product.save(update_fields=["stock_quantity"])
        StockMovement.objects.create(
            product=product,
            order=order,
            movement_type=StockMovement.TYPE_REFUND,
            quantity_delta=quantity,
            reason=f"{reason_prefix} order #{order.order_number}",
        )

    order.stock_deducted_at = None
    order.save(update_fields=["stock_deducted_at"])


def _payment_breakdown(orders):
    totals = {
        "cash": Decimal("0"),
        "wave": Decimal("0"),
        "bank": Decimal("0"),
        "aps": Decimal("0"),
    }
    for order in orders:
        method_totals = {}
        for payment in order.payments.all():
            amount = Decimal(str(payment.amount_received or 0))
            if payment.method == "cash":
                amount -= Decimal(str(payment.change_given or 0))
            if payment.method in totals:
                totals[payment.method] += amount
            method_totals[payment.method] = method_totals.get(payment.method, Decimal("0")) + amount
        labels = [
            f"{label} {method_totals[value]}"
            for value, label in Payment.PAYMENT_METHODS
            if method_totals.get(value)
        ]
        if not labels:
            labels = [payment.method.upper() for payment in order.payments.all()]
        order.payment_display = ", ".join(labels) if labels else "-"
    return totals


def _decimal_from_post(request, name):
    val = (request.POST.get(name) or "").strip()
    if not val:
        return Decimal("0")
    return Decimal(val)


def _parse_payments_from_post(request, total):
    bank_name = (request.POST.get("bank_name") or "").strip()
    has_split_fields = any(
        key in request.POST
        for key in ("cash", "bank", "wave", "aps")
    )
    payments_to_create = []
    change = Decimal("0.00")

    if has_split_fields:
        cash_given = _decimal_from_post(request, "cash")
        bank_amt = _decimal_from_post(request, "bank")
        wave_amt = _decimal_from_post(request, "wave")
        aps_amt = _decimal_from_post(request, "aps")

        if cash_given < 0 or bank_amt < 0 or wave_amt < 0 or aps_amt < 0:
            raise ValueError("Payment amounts must be 0 or more.")

        non_cash = bank_amt + wave_amt + aps_amt
        if non_cash > total:
            raise ValueError("Non-cash payments exceed the order total.")
        if bank_amt > 0 and not bank_name:
            raise ValueError("Bank name is required for bank payments.")

        cash_needed = total - non_cash
        if cash_given < cash_needed:
            raise ValueError("Total paid is less than the order total.")

        change = cash_given - cash_needed
        if bank_amt > 0:
            payments_to_create.append(("bank", bank_amt, bank_name, Decimal("0.00")))
        if wave_amt > 0:
            payments_to_create.append(("wave", wave_amt, "", Decimal("0.00")))
        if aps_amt > 0:
            payments_to_create.append(("aps", aps_amt, "", Decimal("0.00")))
        if cash_given > 0:
            payments_to_create.append(("cash", cash_given, "", change))

        if not payments_to_create:
            raise ValueError("Enter at least one payment amount.")

        return payments_to_create, "split", sum((p[1] for p in payments_to_create), Decimal("0")), change

    method = (request.POST.get("method") or "cash").strip()
    valid_methods = {choice[0] for choice in Payment.PAYMENT_METHODS}
    if method not in valid_methods:
        raise ValueError("Payment method must be valid.")

    amount_raw = (request.POST.get("amount_received") or "").strip()
    amount_received = Decimal(amount_raw) if amount_raw else total
    if amount_received <= 0:
        raise ValueError("Payment amount must be greater than 0.")

    if method == "cash":
        if amount_received < total:
            raise ValueError("Cash amount is less than the order total.")
        change = amount_received - total
    else:
        if amount_received != total:
            raise ValueError(f"{method.upper()} amount must equal the order total.")
        if method == "bank" and not bank_name:
            raise ValueError("Bank name is required for bank payments.")

    payments_to_create.append((
        method,
        amount_received,
        bank_name if method == "bank" else "",
        change if method == "cash" else Decimal("0.00"),
    ))
    return payments_to_create, method, amount_received, change


def get_device_id(request):
    device_id = (request.session.get(DEVICE_ID_SESSION_KEY) or "").strip()
    if device_id:
        return device_id
    return (request.COOKIES.get(DEVICE_ID_COOKIE_NAME) or "").strip() or None


def _redirect_back(request, fallback_name="pos_home"):
    next_url = request.META.get("HTTP_REFERER") or ""
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback_name)


def _require_device_id(request):
    device_id = get_device_id(request)
    if not device_id:
        messages.error(request, "Device ID is not set. Go to Device Setup first.")
        return None
    return device_id


def device_setup(request):
    current = get_device_id(request) or ""

    if request.method == "POST":
        device_id = (request.POST.get("device_id") or "").strip()
        if not device_id:
            messages.error(request, "Device ID is required (e.g. cashier-1).")
            return redirect("device_setup")

        request.session[DEVICE_ID_SESSION_KEY] = device_id
        response = redirect("pos_home")
        response.set_cookie(
            DEVICE_ID_COOKIE_NAME,
            device_id,
            max_age=DEVICE_ID_COOKIE_MAX_AGE_SECONDS,
            samesite="Lax",
        )
        messages.success(request, f"Device set to: {device_id}")
        return response

    return render(request, "pos/device_setup.html", {"device_id": current})


def create_new_order(request):
    last_number = Order.objects.aggregate(max_num=Max("order_number"))["max_num"] or 0
    order = Order.objects.create(order_number=last_number + 1, status="unpaid")
    return redirect("pos_order", order_id=order.id)


def pos_home(request, order_id=None):
    open_orders = Order.objects.filter(status="unpaid", is_active=True).order_by("order_number")

    if not open_orders.exists():
        return redirect("create_new_order")

    if order_id:
        order = get_object_or_404(Order, id=order_id, status="unpaid")
    else:
        order = open_orders.first()

    query = (request.GET.get("q") or "").strip()
    products = []
    if query:
        qs = Product.objects.all()
        if query.isdigit():
            qs = qs.filter(code=int(query))
        else:
            qs = qs.filter(name__icontains=query)
        products = list(qs.order_by(*_product_code_ordering())[:30])

    if request.GET.get("partial") == "1":
        return render(
            request,
            "pos/_product_results.html",
            {
                "order": order,
                "query": query,
                "products": products,
            },
        )

    return render(
        request,
        "pos/pos.html",
        {
            "open_orders": open_orders,
            "order": order,
            "items": order.items.all(),
            "total": order.total_amount(),
            "query": query,
            "products": products,
            "can_delete_order": (order.status == "unpaid" and not order.payments.exists()),
            "is_paid_correction": order.status == "unpaid" and order.payments.exists(),
            "device_id": get_device_id(request),
        },
    )


def products_page(request):
    query = (request.GET.get("q") or request.GET.get("product_q") or "").strip()

    products = Product.objects.all().order_by(*_product_code_ordering())
    if query:
        if query.isdigit():
            products = products.filter(code=int(query))
        else:
            products = products.filter(name__icontains=query)

    paginator = Paginator(products, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    page_range = paginator.get_elided_page_range(
        number=page_obj.number,
        on_each_side=2,
        on_ends=1,
    )

    return render(
        request,
        "pos/products.html",
        {
            "products": page_obj,
            "page_obj": page_obj,
            "page_range": page_range,
            "query": query,
            "next_product_code": _next_product_code(),
            "today": timezone.localdate(),
        },
    )


def stock_movements_page(request):
    product_id = (request.GET.get("product") or "").strip()
    movement_type = (request.GET.get("type") or "").strip()
    raw_date = (request.GET.get("date") or "").strip()
    raw_year = (request.GET.get("year") or "").strip()

    movements = StockMovement.objects.select_related("product", "order").order_by("-occurred_at", "-id")
    selected_product = ""
    selected_type = ""
    selected_date = None
    selected_year = None

    if product_id:
        try:
            selected_product = str(int(product_id))
            movements = movements.filter(product_id=selected_product)
        except Exception:
            selected_product = ""

    valid_types = {choice[0] for choice in StockMovement.TYPE_CHOICES}
    if movement_type in valid_types:
        selected_type = movement_type
        movements = movements.filter(movement_type=movement_type)

    if raw_date:
        try:
            selected_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            movements = movements.filter(occurred_at__date=selected_date)
        except Exception:
            selected_date = None
    elif raw_year:
        try:
            selected_year = int(raw_year)
            if selected_year < 2000 or selected_year > 9999:
                raise ValueError()
            movements = movements.filter(occurred_at__year=selected_year)
        except Exception:
            selected_year = None

    total_stocked = (
        movements.filter(movement_type__in=[StockMovement.TYPE_RESTOCK, StockMovement.TYPE_ADJUSTMENT])
        .aggregate(total=Sum("quantity_delta"))["total"]
        or Decimal("0")
    )
    total_sold_delta = (
        movements.filter(movement_type__in=[StockMovement.TYPE_SALE, StockMovement.TYPE_REFUND])
        .aggregate(total=Sum("quantity_delta"))["total"]
        or Decimal("0")
    )
    total_sold = -total_sold_delta
    summary_products = Product.objects.all().order_by(*_product_code_ordering())
    if selected_product:
        summary_products = summary_products.filter(id=selected_product)
    summary_rows = []
    summary_filter = Q()
    if selected_date:
        summary_filter &= Q(stock_movements__occurred_at__date=selected_date)
    elif selected_year:
        summary_filter &= Q(stock_movements__occurred_at__year=selected_year)

    for product in summary_products.annotate(
        stocked_total=Sum(
            "stock_movements__quantity_delta",
            filter=summary_filter
            & Q(
                stock_movements__movement_type__in=[
                    StockMovement.TYPE_RESTOCK,
                    StockMovement.TYPE_ADJUSTMENT,
                ]
            ),
        ),
        sold_delta_total=Sum(
            "stock_movements__quantity_delta",
            filter=summary_filter
            & Q(stock_movements__movement_type__in=[StockMovement.TYPE_SALE, StockMovement.TYPE_REFUND]),
        ),
    ):
        stocked_total = product.stocked_total or Decimal("0")
        sold_total = -(product.sold_delta_total or Decimal("0"))
        if stocked_total or sold_total or product.stock_quantity:
            summary_rows.append(
                {
                    "product": product,
                    "stocked_total": stocked_total,
                    "sold_total": sold_total,
                    "remaining": product.stock_quantity,
                }
            )

    paginator = Paginator(movements, 30)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    page_range = paginator.get_elided_page_range(
        number=page_obj.number,
        on_each_side=2,
        on_ends=1,
    )

    return render(
        request,
        "pos/stock_movements.html",
        {
            "movements": page_obj,
            "page_obj": page_obj,
            "page_range": page_range,
            "products": Product.objects.all().order_by(*_product_code_ordering()),
            "movement_type_choices": StockMovement.TYPE_CHOICES,
            "selected_product": selected_product,
            "selected_type": selected_type,
            "selected_date_value": selected_date.strftime("%Y-%m-%d") if selected_date else "",
            "selected_year": selected_year or "",
            "total_movement_count": paginator.count,
            "total_stocked": total_stocked,
            "total_sold": total_sold,
            "total_in": total_stocked,
            "total_out": -total_sold,
            "summary_rows": summary_rows,
        },
    )


def stock_alerts_page(request):
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    products = Product.objects.all().order_by("stock_quantity", *_product_code_ordering())
    if query:
        if query.isdigit():
            products = products.filter(code=int(query))
        else:
            products = products.filter(name__icontains=query)

    out_of_stock_count = products.filter(stock_quantity__lte=0).count()
    low_stock_count = products.filter(stock_quantity__gt=0, stock_quantity__lte=F("low_stock_threshold")).count()

    if status == "out":
        products = products.filter(stock_quantity__lte=0)
    elif status == "low":
        products = products.filter(stock_quantity__gt=0, stock_quantity__lte=F("low_stock_threshold"))
    else:
        status = ""
        products = products.filter(Q(stock_quantity__lte=0) | Q(stock_quantity__lte=F("low_stock_threshold")))

    paginator = Paginator(products, 30)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/stock_alerts.html",
        {
            "products": page_obj,
            "page_obj": page_obj,
            "query": query,
            "selected_status": status,
            "low_stock_count": low_stock_count,
            "out_of_stock_count": out_of_stock_count,
        },
    )


def product_barcode(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    # Ensure product has a code assigned
    if product.code is None:
        product.save()
    return render(request, "pos/product_barcode.html", {"product": product})


def reprint_orders(request):
    query = (request.GET.get("q") or "").strip()
    orders = Order.objects.exclude(status="unpaid").prefetch_related("refunds").order_by("-order_number")
    if query:
        orders = orders.filter(order_number__icontains=query)

    paginator = Paginator(orders, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    for order in page_obj:
        _attach_refund_state(order)
        order.can_edit_receipt = order.status == "paid" and not order.refunds.exists()

    return render(
        request,
        "pos/reprint.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "query": query,
        },
    )


def view_order(request, order_id):
    order = get_object_or_404(
        Order.objects.exclude(status="unpaid").prefetch_related(
            "items",
            "items__refund_items",
            "payments",
            "refunds",
            "refunds__items",
        ),
        id=order_id,
    )
    _attach_refund_state(order)
    back_url = request.GET.get("next") or ""
    if not back_url or not url_has_allowed_host_and_scheme(back_url, allowed_hosts={request.get_host()}):
        back_url = None

    return render(
        request,
        "pos/order_view.html",
        {
            "order": order,
            "items": order.items.all(),
            "payments": order.payments.all().order_by("paid_at"),
            "refunds": order.refunds.all().prefetch_related("items"),
            "total": order.total_amount(),
            "refunded_total": order.refunded_total,
            "net_total": order.net_total,
            "back_url": back_url,
            "can_edit": order.status == "paid" and not order.refunds.exists(),
        },
    )


def _rebalance_order_payments(order):
    """Keep payment-method totals equal to a corrected paid receipt total."""
    payments = list(order.payments.select_for_update().order_by("id"))
    if not payments:
        return

    target = _money(order.total_amount())
    net_amounts = []
    for payment in payments:
        net = Decimal(str(payment.amount_received))
        if payment.method == "cash":
            net -= Decimal(str(payment.change_given))
        net_amounts.append(max(net, Decimal("0")))

    weight_total = sum(net_amounts, Decimal("0"))
    allocated = Decimal("0")
    for index, payment in enumerate(payments):
        if index == len(payments) - 1:
            new_net = target - allocated
        elif weight_total:
            new_net = _money(target * net_amounts[index] / weight_total)
            allocated += new_net
        else:
            new_net = Decimal("0")

        payment.amount_received = new_net + (
            Decimal(str(payment.change_given)) if payment.method == "cash" else Decimal("0")
        )
        payment.save(update_fields=["amount_received"])


def start_paid_order_correction(request, order_id):
    if request.method != "POST":
        return redirect("view_order", order_id=order_id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="paid")
        if order.refunds.exists():
            messages.error(request, "A receipt with refund history cannot be edited.")
            return redirect("view_order", order_id=order.id)
        if not order.payments.exists():
            messages.error(request, "This paid receipt has no payment record to preserve.")
            return redirect("view_order", order_id=order.id)

        _restore_order_stock(order, reason_prefix="Opened paid correction")
        order.status = "unpaid"
        order.is_active = True
        order.save(update_fields=["status", "is_active"])

    messages.warning(request, f"Editing paid receipt #{order.order_number}. Save the correction when finished.")
    return redirect("pos_order", order_id=order.id)


def save_paid_order_correction(request, order_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, status="unpaid")
    total = order.total_amount()
    try:
        payments_to_create, method, amount_received, change = _parse_payments_from_post(request, total)
    except Exception as e:
        messages.error(request, str(e) if str(e) else "Invalid corrected payment.")
        return redirect("pos_order", order_id=order.id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="unpaid")
        if not order.payments.exists():
            messages.error(request, "This order is not a paid-receipt correction.")
            return redirect("pos_order", order_id=order.id)
        if order.refunds.exists():
            messages.error(request, "A receipt with refund history cannot be edited.")
            return redirect("pos_order", order_id=order.id)
        if order.total_amount() <= 0:
            messages.error(request, "The corrected receipt must contain at least one item.")
            return redirect("pos_order", order_id=order.id)

        original_paid_at = order.payments.order_by("paid_at", "id").values_list("paid_at", flat=True).first()
        order.payments.all().delete()
        for pay_method, pay_amount, pay_bank, pay_change in payments_to_create:
            Payment.objects.create(
                order=order,
                method=pay_method,
                bank_name=pay_bank,
                amount_received=pay_amount,
                change_given=pay_change,
                paid_at=original_paid_at or timezone.now(),
            )
        order.status = "paid"
        order.is_active = False
        order.save(update_fields=["status", "is_active"])
        _deduct_order_stock(order)

    messages.success(request, f"Paid receipt #{order.order_number} and its {method.upper()} payment were corrected successfully.")
    return redirect("view_order", order_id=order.id)


def correct_paid_order_item(request, order_id, item_id):
    if request.method != "POST":
        return redirect("view_order", order_id=order_id)

    item_name = (request.POST.get("item_name") or "").strip()
    box = (request.POST.get("box") or "").strip()[:50]
    try:
        quantity = Decimal((request.POST.get("quantity") or "").strip())
        selling_price = Decimal((request.POST.get("selling_price") or "").strip())
    except Exception:
        messages.error(request, "Quantity and selling price must be valid numbers.")
        return redirect("view_order", order_id=order_id)

    if not item_name or quantity <= 0 or selling_price <= 0:
        messages.error(request, "Item name, quantity, and selling price are required.")
        return redirect("view_order", order_id=order_id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="paid")
        if order.refunds.exists():
            messages.error(request, "A receipt with refund history cannot be edited.")
            return redirect("view_order", order_id=order.id)

        item = get_object_or_404(
            OrderItem.objects.select_for_update().select_related("product"), id=item_id, order=order
        )
        old_quantity = Decimal(str(item.quantity))
        quantity_change = quantity - old_quantity

        if item.product_id and quantity_change:
            product = Product.objects.select_for_update().get(id=item.product_id)
            product.stock_quantity -= quantity_change
            product.save(update_fields=["stock_quantity"])
            StockMovement.objects.create(
                product=product,
                order=order,
                movement_type=StockMovement.TYPE_SALE,
                quantity_delta=-quantity_change,
                reason=f"Corrected paid order #{order.order_number}",
            )

        item.item_name = item_name[:255]
        item.quantity = quantity
        item.selling_price = selling_price
        item.box = box or None
        item.save(update_fields=["item_name", "quantity", "selling_price", "box"])
        _rebalance_order_payments(order)

    messages.success(request, f"Corrected item on paid receipt #{order.order_number}.")
    return redirect("view_order", order_id=order.id)


def edit_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    fallback_url = reverse("products")
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = fallback_url
    edit_url = f"{reverse('edit_product', args=[product.id])}?next={quote(next_url, safe='')}"

    if request.method == "POST":
        code_raw = (request.POST.get("code") or "").strip()
        name = (request.POST.get("name") or "").strip()
        cost_price = (request.POST.get("cost_price") or "").strip()
        selling_price = (request.POST.get("selling_price") or "").strip()
        low_stock_threshold_raw = (request.POST.get("low_stock_threshold") or "").strip()

        code = None
        if code_raw != "":
            try:
                code = int(code_raw)
                if code < 0:
                    raise ValueError()
            except Exception:
                messages.error(request, "Product code must be a valid number (0 or more).")
                return redirect(edit_url)

        if not name:
            messages.error(request, "Name is required.")
            return redirect(edit_url)

        try:
            cost_price = Decimal(cost_price)
            if cost_price < 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Cost price must be a valid number (0 or more).")
            return redirect(edit_url)

        try:
            selling_price = Decimal(selling_price) if selling_price != "" else Decimal("0")
            if selling_price <= 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Selling price must be a valid number (greater than 0).")
            return redirect(edit_url)

        try:
            low_stock_threshold = (
                Decimal(low_stock_threshold_raw) if low_stock_threshold_raw != "" else Decimal("0")
            )
            if low_stock_threshold < 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Low stock threshold must be a valid number (0 or more).")
            return redirect(edit_url)

        product.code = code
        product.name = name
        product.cost_price = cost_price
        product.selling_price = selling_price
        product.low_stock_threshold = low_stock_threshold
        try:
            product.save()
        except IntegrityError:
            messages.error(request, "That product name or code is already in use.")
            return redirect(edit_url)

        messages.success(request, f"Updated product: {product.name}")
        return redirect(next_url)

    return render(
        request,
        "pos/product_edit.html",
        {
            "product": product,
            "next_product_code": _next_product_code(),
            "next_url": next_url,
        },
    )


def delete_product(request, product_id):
    if request.method != "POST":
        return redirect("products")

    next_url = _safe_next_url(request, reverse("products"))
    product = get_object_or_404(Product, id=product_id)
    product_name = product.name
    product_id = product.id
    product.delete()
    if _is_ajax(request):
        return JsonResponse({"ok": True, "product_id": product_id, "message": f"Deleted product: {product_name}"})

    messages.success(request, f"Deleted product: {product_name}")
    return redirect(next_url)


def adjust_product_stock(request, product_id):
    if request.method != "POST":
        return redirect("products")

    next_url = _safe_next_url(request, reverse("products"))
    adjustment_type = (request.POST.get("adjustment_type") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    note = (request.POST.get("note") or "").strip()
    occurred_at_raw = (request.POST.get("occurred_at") or "").strip()
    cost_price_raw = (request.POST.get("cost_price") or "").strip()
    selling_price_raw = (request.POST.get("selling_price") or "").strip()

    try:
        quantity = Decimal(quantity_raw)
    except Exception:
        if _is_ajax(request):
            return _json_error("Stock quantity must be a valid number.")
        messages.error(request, "Stock quantity must be a valid number.")
        return redirect(next_url)

    if quantity <= 0:
        if _is_ajax(request):
            return _json_error("Stock quantity must be greater than 0.")
        messages.error(request, "Stock quantity must be greater than 0.")
        return redirect(next_url)

    if adjustment_type not in {"restock", "reduce"}:
        if _is_ajax(request):
            return _json_error("Stock movement type is required.")
        messages.error(request, "Stock movement type is required.")
        return redirect(next_url)

    try:
        occurred_date = datetime.strptime(occurred_at_raw, "%Y-%m-%d").date() if occurred_at_raw else timezone.localdate()
    except Exception:
        if _is_ajax(request):
            return _json_error("Stock movement date must be valid.")
        messages.error(request, "Stock movement date must be valid.")
        return redirect(next_url)

    occurred_at = timezone.make_aware(datetime.combine(occurred_date, datetime.min.time()))

    cost_price = selling_price = None
    if adjustment_type == "restock":
        if cost_price_raw or selling_price_raw:
            try:
                cost_price = Decimal(cost_price_raw)
                selling_price = Decimal(selling_price_raw)
                if cost_price < 0 or selling_price <= 0:
                    raise ValueError()
            except Exception:
                if _is_ajax(request):
                    return _json_error("Restock cost and selling prices must be valid.")
                messages.error(request, "Restock cost and selling prices must be valid.")
                return redirect(next_url)

    with transaction.atomic():
        product = get_object_or_404(Product.objects.select_for_update(), id=product_id)
        if adjustment_type == "restock" and cost_price is None:
            cost_price = product.cost_price
            selling_price = product.selling_price
        delta = quantity if adjustment_type == "restock" else -quantity
        if product.stock_quantity + delta < 0:
            if _is_ajax(request):
                return _json_error(
                    f"Cannot reduce {product.name} by {quantity}; only {product.stock_quantity} in stock."
                )
            messages.error(
                request,
                f"Cannot reduce {product.name} by {quantity}; only {product.stock_quantity} in stock.",
            )
            return redirect(next_url)

        if adjustment_type == "restock":
            _ensure_legacy_batch(product)
        product.stock_quantity += delta
        product.save(update_fields=["stock_quantity"])
        if adjustment_type == "restock":
            StockBatch.objects.create(
                product=product,
                initial_quantity=quantity,
                remaining_quantity=quantity,
                cost_price=cost_price,
                selling_price=selling_price,
                received_at=occurred_at,
            )
            active = _available_batches(product).first()
            if active:
                product.cost_price = active.cost_price
                product.selling_price = active.selling_price
                product.save(update_fields=["cost_price", "selling_price"])
        else:
            _consume_fifo_batches(product, quantity)
        StockMovement.objects.create(
            product=product,
            movement_type=(
                StockMovement.TYPE_RESTOCK
                if adjustment_type == "restock"
                else StockMovement.TYPE_ADJUSTMENT
            ),
            quantity_delta=delta,
            reason=reason[:255] or ("Restock" if adjustment_type == "restock" else "Stock adjustment"),
            note=note,
            occurred_at=occurred_at,
        )

    if _is_ajax(request):
        return JsonResponse(
            {
                "ok": True,
                "product_id": product.id,
                "stock_quantity": str(product.stock_quantity),
                "stock_status": product.stock_status(),
                "message": f"Updated stock for {product.name}.",
            }
        )

    messages.success(request, f"Updated stock for {product.name}.")
    return redirect(next_url)


def expenses_page(request):
    raw_date = (request.GET.get("date") or "").strip()
    raw_month = (request.GET.get("month") or "").strip()
    category = (request.GET.get("category") or "").strip()
    payment_method = (request.GET.get("payment_method") or "").strip()

    expenses = Expense.objects.all()
    selected_date = None
    selected_month_value = ""

    if raw_date:
        try:
            selected_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            expenses = expenses.filter(date=selected_date)
        except Exception:
            selected_date = None
    elif raw_month:
        try:
            selected_dt = datetime.strptime(raw_month, "%Y-%m")
            selected_month_value = f"{selected_dt.year:04d}-{selected_dt.month:02d}"
            expenses = expenses.filter(date__year=selected_dt.year, date__month=selected_dt.month)
        except Exception:
            selected_month_value = ""

    valid_categories = {choice[0] for choice in Expense.CATEGORY_CHOICES}
    if category in valid_categories:
        expenses = expenses.filter(category=category)
    else:
        category = ""

    valid_payment_methods = {choice[0] for choice in Expense.PAYMENT_METHODS}
    if payment_method in valid_payment_methods:
        expenses = expenses.filter(payment_method=payment_method)
    else:
        payment_method = ""

    total_expenses = _sum_expenses(expenses)
    paginator = Paginator(expenses, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/expenses.html",
        {
            "expenses": page_obj,
            "page_obj": page_obj,
            "category_choices": Expense.CATEGORY_CHOICES,
            "payment_method_choices": Expense.PAYMENT_METHODS,
            "selected_date": selected_date,
            "selected_date_value": selected_date.strftime("%Y-%m-%d") if selected_date else "",
            "selected_month_value": selected_month_value,
            "selected_category": category,
            "selected_payment_method": payment_method,
            "total_expenses": total_expenses,
            "today": timezone.localdate(),
        },
    )


def create_expense(request):
    if request.method != "POST":
        return redirect("expenses")

    date_raw = (request.POST.get("date") or "").strip()
    category = (request.POST.get("category") or "").strip()
    description = (request.POST.get("description") or "").strip()
    amount_raw = (request.POST.get("amount") or "").strip()
    payment_method = (request.POST.get("payment_method") or "").strip()
    bank_name = (request.POST.get("bank_name") or "").strip()
    notes = (request.POST.get("notes") or "").strip()

    next_url = request.POST.get("next")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("expenses")

    try:
        expense_date = datetime.strptime(date_raw, "%Y-%m-%d").date() if date_raw else timezone.localdate()
    except Exception:
        messages.error(request, "Expense date must be valid.")
        return redirect(next_url)

    if category not in {choice[0] for choice in Expense.CATEGORY_CHOICES}:
        messages.error(request, "Expense category must be valid.")
        return redirect(next_url)
    if payment_method not in {choice[0] for choice in Expense.PAYMENT_METHODS}:
        messages.error(request, "Expense payment method must be valid.")
        return redirect(next_url)
    if payment_method == Expense.PAYMENT_BANK and not bank_name:
        messages.error(request, "Bank name is required for bank expenses.")
        return redirect(next_url)

    try:
        amount = Decimal(amount_raw)
    except Exception:
        messages.error(request, "Expense amount must be a valid number.")
        return redirect(next_url)

    if not description:
        messages.error(request, "Expense description is required.")
        return redirect(next_url)
    if amount <= 0:
        messages.error(request, "Expense amount must be greater than 0.")
        return redirect(next_url)

    Expense.objects.create(
        date=expense_date,
        category=category,
        description=description[:255],
        amount=amount,
        payment_method=payment_method,
        bank_name=bank_name[:100] if payment_method == Expense.PAYMENT_BANK else "",
        notes=notes,
    )
    messages.success(request, f"Added expense: {description[:60]}")
    return redirect(next_url)


def delete_expense(request, expense_id):
    if request.method != "POST":
        return redirect("expenses")

    expense = get_object_or_404(Expense, id=expense_id)
    description = expense.description
    expense.delete()
    messages.success(request, f"Deleted expense: {description[:60]}")

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("expenses")


def create_product(request):
    if request.method != "POST":
        return redirect("products")

    code_raw = request.POST.get("code", "").strip()
    name = request.POST.get("name", "").strip()
    cost_price = request.POST.get("cost_price", "").strip()
    selling_price = request.POST.get("selling_price", "").strip()
    stock_quantity_raw = request.POST.get("stock_quantity", "").strip()
    low_stock_threshold_raw = request.POST.get("low_stock_threshold", "").strip()

    next_url = request.POST.get("next")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = request.META.get("HTTP_REFERER") or ""
        if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = "/products/"

    def fail(message):
        if _is_ajax(request):
            return _json_error(message)
        messages.error(request, message)
        return redirect(next_url)

    if not name or not cost_price or selling_price == "":
        return fail("Name, cost price, and selling price are required.")

    code = None
    if code_raw != "":
        try:
            code = int(code_raw)
            if code < 0:
                raise ValueError()
        except Exception:
            return fail("Product code must be a valid number (0 or more).")

    try:
        cost_price = Decimal(cost_price)
        if cost_price < 0:
            raise ValueError()
    except Exception:
        return fail("Cost price must be a valid number (0 or more).")

    try:
        selling_price = Decimal(selling_price)
        if selling_price <= 0:
            raise ValueError()
    except Exception:
        return fail("Selling price must be a valid number (greater than 0).")

    try:
        stock_quantity = Decimal(stock_quantity_raw) if stock_quantity_raw else Decimal("0")
        if stock_quantity < 0:
            raise ValueError()
    except Exception:
        return fail("Opening stock must be a valid number (0 or more).")

    try:
        low_stock_threshold = Decimal(low_stock_threshold_raw) if low_stock_threshold_raw else Decimal("5")
        if low_stock_threshold < 0:
            raise ValueError()
    except Exception:
        return fail("Low stock threshold must be a valid number (0 or more).")

    try:
        product = Product.objects.create(
            code=code,
            name=name,
            cost_price=cost_price,
            selling_price=selling_price,
            stock_quantity=stock_quantity,
            low_stock_threshold=low_stock_threshold,
        )
        if stock_quantity > 0:
            StockBatch.objects.create(
                product=product,
                initial_quantity=stock_quantity,
                remaining_quantity=stock_quantity,
                cost_price=cost_price,
                selling_price=selling_price,
            )
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.TYPE_RESTOCK,
                quantity_delta=stock_quantity,
                reason="Opening stock",
            )
    except IntegrityError:
        return fail("That product name or code is already in use.")

    if _is_ajax(request):
        return JsonResponse(
            {
                "ok": True,
                "product_id": product.id,
                "message": f"Added product: {name}",
                "next_product_code": _next_product_code(),
            }
        )

    messages.success(request, f"Added product: {name}")
    return redirect(next_url)


def add_product_to_order(request, order_id, product_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    next_url = request.POST.get("next")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("pos_order", args=[order_id])

    quantity = request.POST.get("quantity", "").strip()
    selling_price_raw = request.POST.get("selling_price", "").strip()
    box = (request.POST.get("box") or "").strip()
    if len(box) > 50:
        box = box[:50]
    try:
        quantity = Decimal(quantity)
    except Exception:
        quantity = Decimal("0")

    # Use product's default selling price when POS didn't send/enter one.
    selling_price = None
    if selling_price_raw != "":
        try:
            selling_price = Decimal(selling_price_raw)
        except Exception:
            selling_price = None

    if quantity <= 0:
        messages.error(request, "Quantity and selling price are required (must be greater than 0).")
        return redirect(next_url)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="unpaid")
        product = get_object_or_404(Product.objects.select_for_update(), id=product_id)
        if selling_price is None:
            selling_price = product.selling_price

        if selling_price <= 0:
            messages.error(request, "Quantity and selling price are required (must be greater than 0).")
            return redirect(next_url)

        _ensure_legacy_batch(product)
        remaining = quantity
        batches = list(_available_batches(product))
        use_batch_selling_price = selling_price_raw == "" or selling_price == product.selling_price
        for batch in batches:
            if remaining <= 0:
                break
            portion = min(remaining, batch.remaining_quantity)
            OrderItem.objects.create(
                order=order, product=product, item_name=product.name, quantity=portion,
                selling_price=batch.selling_price if use_batch_selling_price else selling_price,
                box=box or None, cost_price_snapshot=batch.cost_price,
            )
            remaining -= portion
        if remaining > 0:
            OrderItem.objects.create(
                order=order, product=product, item_name=product.name, quantity=remaining,
                selling_price=selling_price, box=box or None, cost_price_snapshot=product.cost_price,
            )

    return redirect(next_url)


def add_custom_item_to_order(request, order_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, status="unpaid")
    item_name = (request.POST.get("item_name") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "").strip()
    selling_price_raw = (request.POST.get("selling_price") or "").strip()
    cost_price_raw = (request.POST.get("cost_price") or "").strip()
    box = (request.POST.get("box") or "").strip()

    next_url = request.POST.get("next")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("pos_order", args=[order.id])

    if len(item_name) > 255:
        item_name = item_name[:255]
    if len(box) > 50:
        box = box[:50]

    try:
        quantity = Decimal(quantity_raw)
        selling_price = Decimal(selling_price_raw)
        cost_price = Decimal(cost_price_raw) if cost_price_raw else Decimal("0")
    except Exception:
        messages.error(request, "Custom item quantity, price, and cost must be valid numbers.")
        return redirect(next_url)

    if not item_name:
        messages.error(request, "Custom item name is required.")
        return redirect(next_url)
    if quantity <= 0 or selling_price <= 0 or cost_price < 0:
        messages.error(request, "Custom item quantity and price must be greater than 0. Cost must be 0 or more.")
        return redirect(next_url)

    OrderItem.objects.create(
        order=order,
        product=None,
        item_name=item_name,
        quantity=quantity,
        selling_price=selling_price,
        box=box or None,
        cost_price_snapshot=cost_price,
    )

    messages.success(request, f"Added custom item: {item_name}")
    return redirect(next_url)


def update_order_details(request, order_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, status="unpaid")
    next_url = _safe_next_url(request, reverse("pos_order", args=[order.id]))

    customer_name = (request.POST.get("customer_name") or "").strip()
    discount_type = (request.POST.get("discount_type") or Order.DISCOUNT_NONE).strip()
    discount_value_raw = (request.POST.get("discount_value") or "").strip()

    if discount_type not in {choice[0] for choice in Order.DISCOUNT_TYPE_CHOICES}:
        messages.error(request, "Discount type must be valid.")
        return redirect(next_url)

    try:
        discount_value = Decimal(discount_value_raw) if discount_value_raw else Decimal("0")
    except Exception:
        messages.error(request, "Discount value must be a valid number.")
        return redirect(next_url)

    if discount_type == Order.DISCOUNT_NONE:
        discount_value = Decimal("0")
    if discount_value < 0:
        messages.error(request, "Discount cannot be negative.")
        return redirect(next_url)
    if discount_type == Order.DISCOUNT_PERCENT and discount_value > 100:
        messages.error(request, "Percent discount cannot be more than 100.")
        return redirect(next_url)
    if discount_type == Order.DISCOUNT_AMOUNT and discount_value > Decimal(str(order.subtotal_amount())):
        messages.error(request, "Discount amount cannot be more than the subtotal.")
        return redirect(next_url)

    order.customer_name = customer_name[:255]
    order.discount_type = discount_type
    order.discount_value = discount_value
    order.save(update_fields=["customer_name", "discount_type", "discount_value"])
    messages.success(request, "Updated bill details.")
    return redirect(next_url)


def delete_order(request, order_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id)
        order_number = order.order_number
        if order.stock_deducted_at:
            _restore_order_stock(order, reason_prefix="Deleted")
            StockMovement.objects.filter(order=order).delete()

        Refund.objects.filter(order=order).delete()
        Payment.objects.filter(order=order).delete()
        PrintJob.objects.filter(payload__order_id=order.id).delete()
        order.delete()

    messages.success(request, f"Deleted bill #{order_number}.")
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("pos_home")


delete_empty_order = delete_order


def remove_order_item(request, order_id, item_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, status="unpaid")
    item = get_object_or_404(OrderItem, id=item_id, order=order)

    label = item.item_name
    item.delete()
    messages.success(request, f"Removed item: {label}")
    return redirect("pos_order", order_id=order_id)


def update_order_item(request, order_id, item_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    item_name = (request.POST.get("item_name") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "").strip()
    selling_price_raw = (request.POST.get("selling_price") or "").strip()
    box = (request.POST.get("box") or "").strip()
    if len(box) > 50:
        box = box[:50]

    if not item_name:
        messages.error(request, "Item name is required.")
        return redirect("pos_order", order_id=order_id)

    try:
        quantity = Decimal(quantity_raw)
        selling_price = Decimal(selling_price_raw)
    except Exception:
        messages.error(request, "Quantity and selling price must be valid numbers.")
        return redirect("pos_order", order_id=order_id)

    if quantity <= 0 or selling_price <= 0:
        messages.error(request, "Quantity and selling price must be greater than 0.")
        return redirect("pos_order", order_id=order_id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="unpaid")
        item = get_object_or_404(
            OrderItem.objects.select_for_update().select_related("product"),
            id=item_id,
            order=order,
        )

        item.item_name = item_name[:255]
        item.quantity = quantity
        item.selling_price = selling_price
        item.box = box or None
        item.save(update_fields=["item_name", "quantity", "selling_price", "box"])

    messages.success(request, f"Updated item: {item.item_name}")
    return redirect("pos_order", order_id=order_id)


def pay_later(request, order_id):
    order = get_object_or_404(Order, id=order_id, status="unpaid")

    if request.method != "POST":
        return redirect("pos_order", order_id=order.id)

    customer_name = (request.POST.get("customer_name") or "").strip()
    if not customer_name:
        messages.error(request, "Customer name is required for an unpaid bill.")
        return redirect("pos_order", order_id=order.id)

    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="unpaid")
        order.customer_name = customer_name[:255]
        order.is_active = False
        order.save(update_fields=["customer_name", "is_active"])
        _deduct_order_stock(order)
    messages.success(request, f"Bill saved for {order.customer_name}. You can find it under Unpaid.")
    return redirect("pos_home")


def unpaid_orders(request):
    query = request.GET.get("q", "").strip()
    orders = Order.objects.filter(status="unpaid", is_active=False)

    if query:
        orders = orders.filter(Q(order_number__icontains=query) | Q(customer_name__icontains=query))

    paginator = Paginator(orders.order_by("-order_number"), 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/unpaid_orders.html",
        {
            "orders": page_obj,
            "page_obj": page_obj,
            "query": query,
        },
    )


def resume_order(request, order_id):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), id=order_id, status="unpaid")
        # Return reserved stock while the bill is reopened for editing. It will be
        # deducted again when the bill is paid or saved for later.
        _restore_order_stock(order, reason_prefix="Reopened unpaid")
        order.is_active = True
        order.save(update_fields=["is_active"])
    return redirect("pos_order", order_id=order.id)


def pay_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status != "unpaid":
        return redirect("pos_home")

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    error = None
    total = order.total_amount()
    change = Decimal("0.00")

    if request.method == "POST":
        try:
            payments_to_create, method, amount_received, change = _parse_payments_from_post(request, total)
        except Exception as e:
            error = str(e) if str(e) else "Invalid amount entered."

        if not error:
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(id=order.id)
                    for pay_method, pay_amount, pay_bank, pay_change in payments_to_create:
                        Payment.objects.create(
                            order=order,
                            method=pay_method,
                            bank_name=pay_bank,
                            amount_received=pay_amount,
                            change_given=pay_change,
                        )

                    order.status = "paid"
                    order.is_active = False
                    order.save()
                    _deduct_order_stock(order)
            except ValueError as e:
                error = str(e)
            else:
                device_id = get_device_id(request)
                if device_id:
                    enqueue_sale_receipt(order=order, target_device_id=device_id)
                    messages.success(request, "Printing: Customer receipt sent to this device.")
                else:
                    messages.error(
                        request,
                        "Payment completed, but receipt was NOT printed because Device ID is not set.",
                    )

                if is_ajax:
                    return JsonResponse(
                        {
                            "ok": True,
                            "order_id": order.id,
                            "order_number": order.order_number,
                            "total": str(total),
                            "method": method,
                            "amount_received": str(amount_received),
                            "change": str(change),
                        }
                    )

                return render(
                    request,
                    "pos/payment_success.html",
                    {
                        "order": order,
                        "total": total,
                        "amount_received": amount_received,
                        "change": change,
                        "method": method,
                    },
                )

        if is_ajax and error:
            return JsonResponse({"ok": False, "error": error}, status=400)

    return render(
        request,
        "pos/payment.html",
        {
            "order": order,
            "total": total,
            "error": error,
        },
    )


def mark_order_paid(request, order_id):
    order = get_object_or_404(Order, id=order_id, status="unpaid")

    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    total = order.total_amount()
    if total <= 0:
        messages.error(request, "Add at least one item before marking the order as paid.")
        return redirect("pos_order", order_id=order_id)

    try:
        payments_to_create, method, amount_received, change = _parse_payments_from_post(request, total)
    except Exception as e:
        messages.error(request, str(e) if str(e) else "Invalid payment entered.")
        return redirect("pos_order", order_id=order_id)

    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order.id)
        if order.status != "unpaid":
            return redirect("pos_home")

        for pay_method, pay_amount, pay_bank, pay_change in payments_to_create:
            Payment.objects.create(
                order=order,
                method=pay_method,
                bank_name=pay_bank,
                amount_received=pay_amount,
                change_given=pay_change,
            )
        order.status = "paid"
        order.is_active = False
        order.save()
        _deduct_order_stock(order)

    device_id = get_device_id(request)
    if device_id:
        enqueue_sale_receipt(order=order, target_device_id=device_id)
        messages.success(request, f"Order #{order.order_number} marked as paid by {method.upper()}. Receipt sent to printer.")
    else:
        messages.error(
            request,
            f"Order #{order.order_number} marked as paid, but receipt was NOT printed because Device ID is not set.",
        )
    return redirect("pos_home")


def print_receipt(request, order_id, copy_type):
    order = get_object_or_404(Order, id=order_id)

    if order.status == "refunded":
        return render(request, "pos/refunded_notice.html", {"order": order})

    if request.method != "POST":
        return _redirect_back(request)

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    copy_type = (copy_type or "").lower()
    if copy_type != "customer":
        messages.error(request, "Store copy has been removed. Only the customer receipt can be printed.")
        return _redirect_back(request)

    enqueue_sale_copy(order=order, copy_type=PrintJob.COPY_CUSTOMER, target_device_id=device_id)
    messages.success(request, "Printing: Customer receipt sent to this device.")

    return _redirect_back(request)


def print_profit_receipt(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status == "refunded":
        return render(request, "pos/refunded_notice.html", {"order": order})

    if request.method != "POST":
        return _redirect_back(request)

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    enqueue_profit_report(order=order, target_device_id=device_id)
    messages.success(request, "Printing: Profit report sent to this device (internal).")
    return _redirect_back(request)


def refund_order(request, order_id):
    order = get_object_or_404(
        Order.objects.prefetch_related("items", "items__refund_items", "refunds"),
        id=order_id,
    )

    if order.status not in {"paid", "refunded"} or order.net_amount() <= 0:
        return redirect("pos_home")

    if request.method == "POST":
        method = (request.POST.get("method") or "cash").strip()
        bank_name = (request.POST.get("bank_name") or "").strip()
        note = (request.POST.get("note") or "").strip()
        valid_methods = {choice[0] for choice in Payment.PAYMENT_METHODS}
        if method not in valid_methods:
            messages.error(request, "Refund method must be valid.")
            return redirect("refund_order", order_id=order.id)
        if method == "bank" and not bank_name:
            messages.error(request, "Bank name is required for bank refunds.")
            return redirect("refund_order", order_id=order.id)

        refund = None
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(id=order.id)
                order_items = list(
                    OrderItem.objects.select_for_update()
                    .select_related("product")
                    .filter(order=order)
                    .order_by("id")
                )
                has_quantity_fields = any(f"quantity_{item.id}" in request.POST for item in order_items)
                selected_items = []

                for item in order_items:
                    raw_quantity = (request.POST.get(f"quantity_{item.id}") or "").strip()
                    if not has_quantity_fields:
                        quantity = Decimal(str(item.refundable_quantity()))
                    elif raw_quantity:
                        quantity = Decimal(raw_quantity)
                    else:
                        quantity = Decimal("0")

                    if quantity < 0:
                        raise ValueError("Refund quantities cannot be negative.")
                    if quantity == 0:
                        continue

                    refundable_quantity = Decimal(str(item.refundable_quantity()))
                    if quantity > refundable_quantity:
                        raise ValueError(f"{item.item_name} can only refund {refundable_quantity}.")

                    gross_amount = Decimal(str(item.selling_price)) * quantity
                    gross_profit = (Decimal(str(item.selling_price)) - Decimal(str(item.cost_price_snapshot))) * quantity
                    selected_items.append(
                        {
                            "item": item,
                            "quantity": quantity,
                            "amount": _refund_amount_for(order, gross_amount),
                            "profit_amount": _refund_profit_for(order, gross_profit),
                        }
                    )

                if not selected_items:
                    raise ValueError("Choose at least one item quantity to refund.")

                refund_total = sum((row["amount"] for row in selected_items), Decimal("0"))
                remaining_total = Decimal(str(order.total_amount())) - Decimal(str(order.refunded_amount()))
                remaining_after_refund = {}
                for item in order_items:
                    selected_quantity = sum(
                        (row["quantity"] for row in selected_items if row["item"].id == item.id),
                        Decimal("0"),
                    )
                    remaining_after_refund[item.id] = Decimal(str(item.refundable_quantity())) - selected_quantity
                is_final_refund = all(quantity <= 0 for quantity in remaining_after_refund.values())
                if is_final_refund and selected_items:
                    difference = _money(remaining_total - refund_total)
                    selected_items[-1]["amount"] = _money(selected_items[-1]["amount"] + difference)
                    refund_total = _money(remaining_total)
                if refund_total > remaining_total:
                    refund_total = _money(remaining_total)
                if refund_total <= 0:
                    raise ValueError("This order has no refundable balance left.")

                refund_profit = sum((row["profit_amount"] for row in selected_items), Decimal("0"))
                if is_final_refund:
                    remaining_profit = Decimal(str(order.total_profit())) - Decimal(str(order.refunded_profit()))
                    profit_difference = _money(remaining_profit - refund_profit)
                    selected_items[-1]["profit_amount"] = _money(selected_items[-1]["profit_amount"] + profit_difference)
                    refund_profit = _money(remaining_profit)
                refund = Refund.objects.create(
                    order=order,
                    method=method,
                    bank_name=bank_name if method == "bank" else "",
                    amount=refund_total,
                    profit_amount=refund_profit,
                    note=note,
                )

                for row in selected_items:
                    item = row["item"]
                    RefundItem.objects.create(
                        refund=refund,
                        order_item=item,
                        product=item.product,
                        item_name=item.item_name,
                        box=item.box,
                        quantity=row["quantity"],
                        selling_price=item.selling_price,
                        cost_price_snapshot=item.cost_price_snapshot,
                        amount=row["amount"],
                        profit_amount=row["profit_amount"],
                    )
                    if item.product:
                        product = Product.objects.select_for_update().get(id=item.product_id)
                        product.stock_quantity += row["quantity"]
                        product.save(update_fields=["stock_quantity"])
                        StockMovement.objects.create(
                            product=product,
                            order=order,
                            movement_type=StockMovement.TYPE_REFUND,
                            quantity_delta=row["quantity"],
                            reason=f"Refund order #{order.order_number}",
                            note=f"Refund #{refund.id}",
                        )

                order.status = "refunded" if order.net_amount() <= 0 else "paid"
                order.is_active = False
                update_fields = ["status", "is_active"]
                if order.status == "refunded":
                    order.stock_deducted_at = None
                    update_fields.append("stock_deducted_at")
                order.save(update_fields=update_fields)
        except Exception as e:
            messages.error(request, str(e) if str(e) else "Could not complete refund.")
            return redirect("refund_order", order_id=order.id)

        device_id = get_device_id(request)
        if device_id:
            enqueue_refund_receipt(order=order, refund=refund, target_device_id=device_id)
            messages.success(request, "Printing: Refund receipt sent to this device.")
        else:
            messages.error(
                request,
                "Refund completed, but refund receipt was NOT printed because Device ID is not set.",
            )

        return redirect("view_order", order_id=order.id)

    _attach_refund_state(order)
    return render(
        request,
        "pos/refund_confirm.html",
        {
            "order": order,
            "items": _refund_context_items(order),
            "total": order.total_amount(),
            "refunded_total": order.refunded_total,
            "net_total": order.net_total,
            "payment_methods": Payment.PAYMENT_METHODS,
        },
    )


def day_view(request):
    raw_date = (request.GET.get("date") or "").strip()
    if raw_date:
        try:
            selected = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except Exception:
            selected = timezone.localdate()
    else:
        selected = timezone.localdate()

    orders_qs = (
        Order.objects.exclude(status="unpaid").filter(payments__paid_at__date=selected)
        .distinct()
        .order_by("order_number")
        .prefetch_related("items", "payments")
    )

    refunds_qs = Refund.objects.filter(refunded_at__date=selected).prefetch_related("items", "order")
    refund_total = sum((refund.amount for refund in refunds_qs), Decimal("0"))
    refund_profit = sum((refund.profit_amount for refund in refunds_qs), Decimal("0"))
    total_sales = sum((o.total_amount() for o in orders_qs), Decimal("0")) - refund_total
    total_profit = sum((o.total_profit() for o in orders_qs), Decimal("0")) - refund_profit
    expenses_qs = Expense.objects.filter(date=selected).order_by("-created_at", "-id")
    total_expenses = _sum_expenses(expenses_qs)
    payment_totals = _payment_breakdown(orders_qs)
    payment_totals = _net_payment_totals(payment_totals, _refunds_payment_breakdown(refunds_qs))
    payment_totals = _net_payment_totals(payment_totals, _expenses_payment_breakdown(expenses_qs))
    net_profit = total_profit - total_expenses
    paginator = Paginator(orders_qs, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/day.html",
        {
            "selected_date": selected,
            "orders": page_obj,
            "page_obj": page_obj,
            "total_order_count": paginator.count,
            "refunds": refunds_qs,
            "refund_total": refund_total,
            "total_sales": total_sales,
            "total_profit": total_profit,
            "total_cash": payment_totals["cash"],
            "total_wave": payment_totals["wave"],
            "total_bank": payment_totals["bank"],
            "total_aps": payment_totals["aps"],
            "expenses": expenses_qs,
            "total_expenses": total_expenses,
            "net_profit": net_profit,
            "category_choices": Expense.CATEGORY_CHOICES,
        },
    )


def month_view(request):
    raw_month = (request.GET.get("month") or "").strip()
    today = timezone.localdate()
    if raw_month:
        try:
            selected_dt = datetime.strptime(raw_month, "%Y-%m")
            selected_year = selected_dt.year
            selected_month = selected_dt.month
        except Exception:
            selected_year = today.year
            selected_month = today.month
    else:
        selected_year = today.year
        selected_month = today.month

    orders_qs = (
        Order.objects.exclude(status="unpaid").filter(
            payments__paid_at__year=selected_year,
            payments__paid_at__month=selected_month,
        )
        .distinct()
        .order_by("order_number")
        .prefetch_related("items", "payments")
    )

    refunds_qs = Refund.objects.filter(
        refunded_at__year=selected_year,
        refunded_at__month=selected_month,
    ).prefetch_related("items", "order")
    refund_total = sum((refund.amount for refund in refunds_qs), Decimal("0"))
    refund_profit = sum((refund.profit_amount for refund in refunds_qs), Decimal("0"))
    total_sales = sum((o.total_amount() for o in orders_qs), Decimal("0")) - refund_total
    total_profit = sum((o.total_profit() for o in orders_qs), Decimal("0")) - refund_profit
    expenses_qs = Expense.objects.filter(date__year=selected_year, date__month=selected_month)
    total_expenses = _sum_expenses(expenses_qs)
    payment_totals = _payment_breakdown(orders_qs)
    payment_totals = _net_payment_totals(payment_totals, _refunds_payment_breakdown(refunds_qs))
    payment_totals = _net_payment_totals(payment_totals, _expenses_payment_breakdown(expenses_qs))
    net_profit = total_profit - total_expenses
    paginator = Paginator(orders_qs, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/month.html",
        {
            "selected_month_value": f"{selected_year:04d}-{selected_month:02d}",
            "selected_month_label": f"{month_name[selected_month]} {selected_year}",
            "selected_year": selected_year,
            "selected_month": selected_month,
            "orders": page_obj,
            "page_obj": page_obj,
            "total_order_count": paginator.count,
            "refunds": refunds_qs,
            "refund_total": refund_total,
            "total_sales": total_sales,
            "total_profit": total_profit,
            "total_cash": payment_totals["cash"],
            "total_wave": payment_totals["wave"],
            "total_bank": payment_totals["bank"],
            "total_aps": payment_totals["aps"],
            "total_expenses": total_expenses,
            "net_profit": net_profit,
        },
    )


def print_day_summary(request):
    if request.method != "POST":
        return redirect("day_view")

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    raw_date = (request.POST.get("date") or "").strip()
    try:
        selected = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except Exception:
        selected = timezone.localdate()

    enqueue_day_summary(date=selected, target_device_id=device_id)
    messages.success(request, f"Printing: Day summary for {selected} sent to this device.")
    return redirect(f"{request.META.get('HTTP_REFERER') or '/day/'}")


def print_day_receipts(request):
    if request.method != "POST":
        return redirect("day_view")

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    raw_date = (request.POST.get("date") or "").strip()
    try:
        selected = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except Exception:
        selected = timezone.localdate()

    orders = (
        Order.objects.filter(status="paid", payments__paid_at__date=selected)
        .distinct()
        .order_by("order_number")
    )
    count = 0
    for order in orders:
        enqueue_sale_receipt(order=order, target_device_id=device_id)
        count += 1

    messages.success(request, f"Printing: {count} receipts for {selected} sent to this device.")
    return redirect(f"{request.META.get('HTTP_REFERER') or '/day/'}")


def print_month_summary(request):
    if request.method != "POST":
        return redirect("month_view")

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    raw_month = (request.POST.get("month") or "").strip()
    today = timezone.localdate()
    try:
        selected_dt = datetime.strptime(raw_month, "%Y-%m")
        selected_year = selected_dt.year
        selected_month = selected_dt.month
    except Exception:
        selected_year = today.year
        selected_month = today.month

    enqueue_month_summary(year=selected_year, month=selected_month, target_device_id=device_id)
    messages.success(
        request,
        f"Printing: Month summary for {selected_year:04d}-{selected_month:02d} sent to this device.",
    )
    return redirect(f"{request.META.get('HTTP_REFERER') or '/month/'}")


def print_month_receipts(request):
    if request.method != "POST":
        return redirect("month_view")

    device_id = _require_device_id(request)
    if not device_id:
        return redirect("device_setup")

    raw_month = (request.POST.get("month") or "").strip()
    today = timezone.localdate()
    try:
        selected_dt = datetime.strptime(raw_month, "%Y-%m")
        selected_year = selected_dt.year
        selected_month = selected_dt.month
    except Exception:
        selected_year = today.year
        selected_month = today.month

    orders = (
        Order.objects.filter(
            status="paid",
            payments__paid_at__year=selected_year,
            payments__paid_at__month=selected_month,
        )
        .distinct()
        .order_by("order_number")
    )
    count = 0
    for order in orders:
        enqueue_sale_receipt(order=order, target_device_id=device_id)
        count += 1

    messages.success(
        request,
        f"Printing: {count} receipts for {selected_year:04d}-{selected_month:02d} sent to this device.",
    )
    return redirect(f"{request.META.get('HTTP_REFERER') or '/month/'}")
