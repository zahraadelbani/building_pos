from decimal import Decimal

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Max, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Order, OrderItem, Payment, Product


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

    if request.method == "POST":
        item_name = request.POST.get("item_name", "").strip()
        quantity = request.POST.get("quantity")
        unit_price = request.POST.get("unit_price")

        if item_name and quantity and unit_price:
            OrderItem.objects.create(
                order=order,
                item_name=item_name,
                quantity=quantity,
                unit_price=unit_price,
            )

        return redirect("pos_order", order_id=order.id)

    return render(
        request,
        "pos/pos.html",
        {
            "open_orders": open_orders,
            "order": order,
            "items": order.items.all(),
            "total": order.total_amount(),
        },
    )


def products_page(request):
    query = (request.GET.get("q") or request.GET.get("product_q") or "").strip()
    order_id = (request.GET.get("order_id") or "").strip()
    order = None

    if order_id:
        order = get_object_or_404(Order, id=order_id, status="unpaid")

    products = Product.objects.all().order_by("name")
    if query:
        products = products.filter(name__icontains=query)

    return render(
        request,
        "pos/products.html",
        {
            "products": products,
            "query": query,
            "order": order,
        },
    )


def create_product(request):
    if request.method != "POST":
        return redirect("products")

    name = request.POST.get("name", "").strip()
    unit_price = request.POST.get("unit_price", "").strip()
    stock_quantity = request.POST.get("stock_quantity", "0").strip()

    next_url = request.POST.get("next")
    if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = request.META.get("HTTP_REFERER") or ""
        if not next_url or not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            next_url = "/products/"

    if not name or not unit_price:
        messages.error(request, "Name and unit price are required.")
        return redirect(next_url)

    try:
        stock_quantity = Decimal(stock_quantity)
        if stock_quantity < 0:
            raise ValueError()
    except Exception:
        messages.error(request, "Stock quantity must be a valid number (0 or more).")
        return redirect(next_url)

    try:
        Product.objects.create(name=name, unit_price=unit_price, stock_quantity=stock_quantity)
    except IntegrityError:
        messages.error(request, "A product with this name already exists.")
        return redirect(next_url)

    messages.success(request, f"Added product: {name}")
    return redirect(next_url)


def add_product_to_order(request, order_id, product_id):
    if request.method != "POST":
        return redirect("pos_order", order_id=order_id)

    order = get_object_or_404(Order, id=order_id, status="unpaid")
    product = get_object_or_404(Product, id=product_id)

    quantity = request.POST.get("quantity", "1")
    try:
        quantity = Decimal(quantity)
    except Exception:
        quantity = Decimal("1")

    if quantity <= 0:
        quantity = Decimal("1")

    if product.stock_quantity <= 0:
        messages.error(request, f"{product.name} is out of stock.")
        next_url = request.POST.get("next")
        if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
        return redirect("products")

    if product.default_quantity != quantity:
        product.default_quantity = quantity
        product.save(update_fields=["default_quantity"])

    OrderItem.objects.create(
        order=order,
        product=product,
        item_name=product.name,
        quantity=quantity,
        unit_price=product.unit_price,
    )

    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)

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

    return render(
        request,
        "pos/unpaid_orders.html",
        {
            "orders": orders,
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

    error = None
    total = order.total_amount()
    change = Decimal("0.00")

    if request.method == "POST":
        method = request.POST.get("method")
        amount_received = request.POST.get("amount_received")
        bank_name = request.POST.get("bank_name", "").strip()

        try:
            amount_received = Decimal(amount_received)
        except Exception:
            error = "Invalid amount entered."

        if not error:
            if method == "cash":
                if amount_received < total:
                    error = "Amount received is LESS than total."
                else:
                    change = amount_received - total
            else:
                if amount_received != total:
                    error = "For non-cash payments, amount must equal total."
                if method == "bank" and not bank_name:
                    error = "Bank name is required for bank transfers."
                change = Decimal("0.00")

        if not error:
            try:
                with transaction.atomic():
                    Order.objects.select_for_update().get(id=order.id)

                    needed = (
                        order.items.exclude(product__isnull=True)
                        .values("product_id")
                        .annotate(needed=Sum("quantity"))
                    )

                    product_ids = [row["product_id"] for row in needed]
                    products_by_id = {
                        p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)
                    }

                    for row in needed:
                        product_id = row["product_id"]
                        required_qty = row["needed"] or Decimal("0")
                        product = products_by_id.get(product_id)
                        if not product:
                            raise ValueError("Missing product for one of the line items.")
                        if product.stock_quantity < required_qty:
                            raise ValueError(
                                f"Not enough stock for {product.name}. In stock: {product.stock_quantity}"
                            )

                    for row in needed:
                        product = products_by_id[row["product_id"]]
                        product.stock_quantity = product.stock_quantity - (row["needed"] or Decimal("0"))
                        product.save(update_fields=["stock_quantity"])

                    Payment.objects.create(
                        order=order,
                        method=method,
                        bank_name=bank_name if method == "bank" else None,
                        amount_received=amount_received,
                        change_given=change,
                    )

                    order.status = "paid"
                    order.is_active = False
                    order.save()
            except ValueError as e:
                error = str(e)
            else:
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

    return render(
        request,
        "pos/payment.html",
        {
            "order": order,
            "total": total,
            "error": error,
        },
    )


def print_receipt(request, order_id, copy_type):
    order = get_object_or_404(Order, id=order_id)

    if order.status == "refunded":
        return render(request, "pos/refunded_notice.html", {"order": order})

    if copy_type not in ["customer", "store"]:
        return redirect("pos_home")

    payment = order.payments.last()

    return render(
        request,
        "pos/receipt.html",
        {
            "order": order,
            "items": order.items.all(),
            "payment": payment,
            "copy_type": copy_type,
            "total": order.total_amount(),
        },
    )


def refund_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status != "paid":
        return redirect("pos_home")

    if request.method == "POST":
        with transaction.atomic():
            Order.objects.select_for_update().get(id=order.id)

            returned = (
                order.items.exclude(product__isnull=True)
                .values("product_id")
                .annotate(qty=Sum("quantity"))
            )

            product_ids = [row["product_id"] for row in returned]
            products_by_id = {
                p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids)
            }

            for row in returned:
                product = products_by_id.get(row["product_id"])
                if product:
                    product.stock_quantity = product.stock_quantity + (row["qty"] or Decimal("0"))
                    product.save(update_fields=["stock_quantity"])

            order.status = "refunded"
            order.is_active = False
            order.save()

        return redirect("pos_home")

    return render(
        request,
        "pos/refund_confirm.html",
        {
            "order": order,
            "total": order.total_amount(),
        },
    )
