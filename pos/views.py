from datetime import datetime
from calendar import month_name
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Expense, Order, OrderItem, Payment, PrintJob, Product, StockMovement
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


def _order_product_quantities(order):
    quantities = {}
    for item in order.items.exclude(product__isnull=True).select_related("product"):
        quantities[item.product_id] = quantities.get(item.product_id, Decimal("0")) + Decimal(item.quantity)
    return quantities


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

    quantities = _order_product_quantities(order)
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


def _cash_wave_breakdown(orders):
    cash = Decimal("0")
    wave = Decimal("0")
    for order in orders:
        method_totals = {}
        for payment in order.payments.all():
            amount = Decimal(str(payment.amount_received or 0))
            if payment.method == "cash":
                amount -= Decimal(str(payment.change_given or 0))
                cash += amount
            elif payment.method == "wave":
                wave += amount
            method_totals[payment.method] = method_totals.get(payment.method, Decimal("0")) + amount
        labels = []
        if method_totals.get("cash"):
            labels.append(f"Cash {method_totals['cash']}")
        if method_totals.get("wave"):
            labels.append(f"Wave {method_totals['wave']}")
        if not labels:
            labels = [payment.method.upper() for payment in order.payments.all()]
        order.payment_display = ", ".join(labels) if labels else "-"
    return cash, wave


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

    return render(
        request,
        "pos/products.html",
        {
            "products": page_obj,
            "page_obj": page_obj,
            "query": query,
            "next_product_code": _next_product_code(),
            "today": timezone.localdate(),
        },
    )


def stock_movements_page(request):
    product_id = (request.GET.get("product") or "").strip()
    movement_type = (request.GET.get("type") or "").strip()
    raw_date = (request.GET.get("date") or "").strip()

    movements = StockMovement.objects.select_related("product", "order").order_by("-occurred_at", "-id")
    selected_product = ""
    selected_type = ""
    selected_date = None

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

    total_in = movements.filter(quantity_delta__gt=0).aggregate(total=Sum("quantity_delta"))["total"] or Decimal("0")
    total_out = movements.filter(quantity_delta__lt=0).aggregate(total=Sum("quantity_delta"))["total"] or Decimal("0")

    paginator = Paginator(movements, 30)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "pos/stock_movements.html",
        {
            "movements": page_obj,
            "page_obj": page_obj,
            "products": Product.objects.all().order_by(*_product_code_ordering()),
            "movement_type_choices": StockMovement.TYPE_CHOICES,
            "selected_product": selected_product,
            "selected_type": selected_type,
            "selected_date_value": selected_date.strftime("%Y-%m-%d") if selected_date else "",
            "total_movement_count": paginator.count,
            "total_in": total_in,
            "total_out": total_out,
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
    orders = Order.objects.exclude(status="unpaid").order_by("-order_number")
    if query:
        orders = orders.filter(order_number__icontains=query)

    paginator = Paginator(orders, 25)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)

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
        Order.objects.exclude(status="unpaid").prefetch_related("items", "payments"),
        id=order_id,
    )
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
            "total": order.total_amount(),
            "back_url": back_url,
        },
    )


def edit_product(request, product_id):
    product = get_object_or_404(Product, id=product_id)

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
                return redirect("edit_product", product_id=product.id)

        if not name:
            messages.error(request, "Name is required.")
            return redirect("edit_product", product_id=product.id)

        try:
            cost_price = Decimal(cost_price)
            if cost_price < 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Cost price must be a valid number (0 or more).")
            return redirect("edit_product", product_id=product.id)

        try:
            selling_price = Decimal(selling_price) if selling_price != "" else Decimal("0")
            if selling_price <= 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Selling price must be a valid number (greater than 0).")
            return redirect("edit_product", product_id=product.id)

        try:
            low_stock_threshold = (
                Decimal(low_stock_threshold_raw) if low_stock_threshold_raw != "" else Decimal("0")
            )
            if low_stock_threshold < 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Low stock threshold must be a valid number (0 or more).")
            return redirect("edit_product", product_id=product.id)

        product.code = code
        product.name = name
        product.cost_price = cost_price
        product.selling_price = selling_price
        product.low_stock_threshold = low_stock_threshold
        try:
            product.save()
        except IntegrityError:
            messages.error(request, "That product name or code is already in use.")
            return redirect("edit_product", product_id=product.id)

        messages.success(request, f"Updated product: {product.name}")
        return redirect("products")

    return render(
        request,
        "pos/product_edit.html",
        {
            "product": product,
            "next_product_code": _next_product_code(),
        },
    )


def delete_product(request, product_id):
    if request.method != "POST":
        return redirect("products")

    product = get_object_or_404(Product, id=product_id)
    product_name = product.name
    product.delete()
    messages.success(request, f"Deleted product: {product_name}")
    return redirect("products")


def adjust_product_stock(request, product_id):
    if request.method != "POST":
        return redirect("products")

    next_url = _safe_next_url(request, reverse("products"))
    adjustment_type = (request.POST.get("adjustment_type") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    note = (request.POST.get("note") or "").strip()
    occurred_at_raw = (request.POST.get("occurred_at") or "").strip()

    try:
        quantity = Decimal(quantity_raw)
    except Exception:
        messages.error(request, "Stock quantity must be a valid number.")
        return redirect(next_url)

    if quantity <= 0:
        messages.error(request, "Stock quantity must be greater than 0.")
        return redirect(next_url)

    if adjustment_type not in {"restock", "reduce"}:
        messages.error(request, "Stock movement type is required.")
        return redirect(next_url)

    try:
        occurred_date = datetime.strptime(occurred_at_raw, "%Y-%m-%d").date() if occurred_at_raw else timezone.localdate()
    except Exception:
        messages.error(request, "Stock movement date must be valid.")
        return redirect(next_url)

    occurred_at = timezone.make_aware(datetime.combine(occurred_date, datetime.min.time()))

    with transaction.atomic():
        product = get_object_or_404(Product.objects.select_for_update(), id=product_id)
        delta = quantity if adjustment_type == "restock" else -quantity
        if product.stock_quantity + delta < 0:
            messages.error(
                request,
                f"Cannot reduce {product.name} by {quantity}; only {product.stock_quantity} in stock.",
            )
            return redirect(next_url)

        product.stock_quantity += delta
        product.save(update_fields=["stock_quantity"])
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

    messages.success(request, f"Updated stock for {product.name}.")
    return redirect(next_url)


def expenses_page(request):
    raw_date = (request.GET.get("date") or "").strip()
    raw_month = (request.GET.get("month") or "").strip()
    category = (request.GET.get("category") or "").strip()

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
            "selected_date": selected_date,
            "selected_date_value": selected_date.strftime("%Y-%m-%d") if selected_date else "",
            "selected_month_value": selected_month_value,
            "selected_category": category,
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

    if not name or not cost_price or selling_price == "":
        messages.error(request, "Name, cost price, and selling price are required.")
        return redirect(next_url)

    code = None
    if code_raw != "":
        try:
            code = int(code_raw)
            if code < 0:
                raise ValueError()
        except Exception:
            messages.error(request, "Product code must be a valid number (0 or more).")
            return redirect(next_url)

    try:
        cost_price = Decimal(cost_price)
        if cost_price < 0:
            raise ValueError()
    except Exception:
        messages.error(request, "Cost price must be a valid number (0 or more).")
        return redirect(next_url)

    try:
        selling_price = Decimal(selling_price)
        if selling_price <= 0:
            raise ValueError()
    except Exception:
        messages.error(request, "Selling price must be a valid number (greater than 0).")
        return redirect(next_url)

    try:
        stock_quantity = Decimal(stock_quantity_raw) if stock_quantity_raw else Decimal("0")
        if stock_quantity < 0:
            raise ValueError()
    except Exception:
        messages.error(request, "Opening stock must be a valid number (0 or more).")
        return redirect(next_url)

    try:
        low_stock_threshold = Decimal(low_stock_threshold_raw) if low_stock_threshold_raw else Decimal("5")
        if low_stock_threshold < 0:
            raise ValueError()
    except Exception:
        messages.error(request, "Low stock threshold must be a valid number (0 or more).")
        return redirect(next_url)

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
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.TYPE_RESTOCK,
                quantity_delta=stock_quantity,
                reason="Opening stock",
            )
    except IntegrityError:
        messages.error(request, "That product name or code is already in use.")
        return redirect(next_url)

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

        OrderItem.objects.create(
            order=order,
            product=product,
            item_name=product.name,
            quantity=quantity,
            selling_price=selling_price,
            box=box or None,
            cost_price_snapshot=product.cost_price,
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
        if order.status == "paid":
            _restore_order_stock(order, reason_prefix="Deleted paid")

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

    quantity_raw = (request.POST.get("quantity") or "").strip()
    selling_price_raw = (request.POST.get("selling_price") or "").strip()
    box = (request.POST.get("box") or "").strip()
    if len(box) > 50:
        box = box[:50]

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

        item.quantity = quantity
        item.selling_price = selling_price
        item.box = box or None
        item.save(update_fields=["quantity", "selling_price", "box"])

    messages.success(request, f"Updated item: {item.item_name}")
    return redirect("pos_order", order_id=order_id)


def pay_later(request, order_id):
    order = get_object_or_404(Order, id=order_id, status="unpaid")
    order.is_active = False
    order.save()
    return redirect("pos_home")


def unpaid_orders(request):
    query = request.GET.get("q", "")
    orders = Order.objects.filter(status="unpaid", is_active=False)

    if query:
        orders = orders.filter(order_number__icontains=query)

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
    order = get_object_or_404(Order, id=order_id, status="unpaid")
    order.is_active = True
    order.save()
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
        bank_name = request.POST.get("bank_name", "").strip()

        def _d(name):
            val = (request.POST.get(name) or "").strip()
            if not val:
                return Decimal("0")
            return Decimal(val)

        split_cash = request.POST.get("cash") is not None
        split_bank = request.POST.get("bank") is not None
        split_wave = request.POST.get("wave") is not None
        split_aps = request.POST.get("aps") is not None
        using_split = split_cash or split_bank or split_wave or split_aps

        payments_to_create = []
        method = None
        amount_received = None

        try:
            if using_split:
                cash_given = _d("cash")
                bank_amt = _d("bank")
                wave_amt = _d("wave")
                aps_amt = _d("aps")

                if cash_given < 0 or bank_amt < 0 or wave_amt < 0 or aps_amt < 0:
                    raise ValueError("Amounts must be 0 or more.")

                non_cash = bank_amt + wave_amt + aps_amt
                if non_cash > total:
                    raise ValueError("Non-cash exceeds total.")
                if bank_amt > 0 and not bank_name:
                    raise ValueError("Bank name is required for bank transfers.")

                cash_needed = total - non_cash
                if cash_given < cash_needed:
                    raise ValueError("Total paid is LESS than total.")

                change = cash_given - cash_needed

                if bank_amt > 0:
                    payments_to_create.append(("bank", bank_amt, bank_name))
                if wave_amt > 0:
                    payments_to_create.append(("wave", wave_amt, None))
                if aps_amt > 0:
                    payments_to_create.append(("aps", aps_amt, None))
                if cash_given > 0:
                    payments_to_create.append(("cash", cash_given, None))

                amount_received = non_cash + cash_given
                method = "split"
            else:
                method = request.POST.get("method")
                amount_received = Decimal((request.POST.get("amount_received") or "").strip())

                if method == "cash":
                    if amount_received < total:
                        raise ValueError("Amount received is LESS than total.")
                    change = amount_received - total
                    payments_to_create.append(("cash", amount_received, None))
                else:
                    if amount_received != total:
                        raise ValueError("For non-cash payments, amount must equal total.")
                    if method == "bank" and not bank_name:
                        raise ValueError("Bank name is required for bank transfers.")
                    payments_to_create.append((method, amount_received, bank_name if method == "bank" else None))
                    change = Decimal("0.00")
        except Exception as e:
            error = str(e) if str(e) else "Invalid amount entered."

        if not error:
            try:
                with transaction.atomic():
                    order = Order.objects.select_for_update().get(id=order.id)
                    for pay_method, pay_amount, pay_bank in payments_to_create:
                        Payment.objects.create(
                            order=order,
                            method=pay_method,
                            bank_name=pay_bank,
                            amount_received=pay_amount,
                            change_given=change if pay_method == "cash" else Decimal("0.00"),
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

    method = (request.POST.get("method") or "cash").strip()
    valid_methods = {choice[0] for choice in Payment.PAYMENT_METHODS}
    if method not in valid_methods:
        messages.error(request, "Payment method must be valid.")
        return redirect("pos_order", order_id=order_id)

    bank_name = (request.POST.get("bank_name") or "").strip()
    if method == "bank" and not bank_name:
        messages.error(request, "Bank name is required for bank transfers.")
        return redirect("pos_order", order_id=order_id)

    amount_raw = (request.POST.get("amount_received") or "").strip()
    try:
        amount_received = Decimal(amount_raw) if amount_raw else total
    except Exception:
        messages.error(request, "Payment amount must be a valid number.")
        return redirect("pos_order", order_id=order_id)

    if method != "cash" and amount_received != total:
        messages.error(request, f"{method.upper()} amount must equal the order total.")
        return redirect("pos_order", order_id=order_id)
    if method == "cash" and amount_received < total:
        messages.error(request, "Cash amount is less than the order total.")
        return redirect("pos_order", order_id=order_id)

    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order.id)
        if order.status != "unpaid":
            return redirect("pos_home")

        Payment.objects.create(
            order=order,
            method=method,
            bank_name=bank_name if method == "bank" else None,
            amount_received=amount_received,
            change_given=amount_received - total if method == "cash" else Decimal("0.00"),
        )
        order.status = "paid"
        order.is_active = False
        order.save()
        _deduct_order_stock(order)

    messages.success(request, f"Order #{order.order_number} marked as paid by {method.upper()}.")
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
    order = get_object_or_404(Order, id=order_id)

    if order.status != "paid":
        return redirect("pos_home")

    if request.method == "POST":
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order.id)
            _restore_order_stock(order, reason_prefix="Refund")

            order.status = "refunded"
            order.is_active = False
            order.save()

        device_id = get_device_id(request)
        if device_id:
            enqueue_refund_receipt(order=order, target_device_id=device_id)
            messages.success(request, "Printing: Refund receipt sent to this device.")
        else:
            messages.error(
                request,
                "Refund completed, but refund receipt was NOT printed because Device ID is not set.",
            )

        return redirect("pos_home")

    return render(
        request,
        "pos/refund_confirm.html",
        {
            "order": order,
            "total": order.total_amount(),
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
        Order.objects.filter(status="paid", payments__paid_at__date=selected)
        .distinct()
        .order_by("order_number")
        .prefetch_related("items", "payments")
    )

    total_sales = sum((o.total_amount() for o in orders_qs), Decimal("0"))
    total_profit = sum((o.total_profit() for o in orders_qs), Decimal("0"))
    total_cash, total_wave = _cash_wave_breakdown(orders_qs)
    expenses_qs = Expense.objects.filter(date=selected).order_by("-created_at", "-id")
    total_expenses = _sum_expenses(expenses_qs)
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
            "total_sales": total_sales,
            "total_profit": total_profit,
            "total_cash": total_cash,
            "total_wave": total_wave,
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
        Order.objects.filter(
            status="paid",
            payments__paid_at__year=selected_year,
            payments__paid_at__month=selected_month,
        )
        .distinct()
        .order_by("order_number")
        .prefetch_related("items", "payments")
    )

    total_sales = sum((o.total_amount() for o in orders_qs), Decimal("0"))
    total_profit = sum((o.total_profit() for o in orders_qs), Decimal("0"))
    total_cash, total_wave = _cash_wave_breakdown(orders_qs)
    expenses_qs = Expense.objects.filter(date__year=selected_year, date__month=selected_month)
    total_expenses = _sum_expenses(expenses_qs)
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
            "total_sales": total_sales,
            "total_profit": total_profit,
            "total_cash": total_cash,
            "total_wave": total_wave,
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
