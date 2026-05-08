from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone


LINE_WIDTH_80MM = 48


def _money(value) -> str:
    if value is None:
        value = Decimal("0")
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _text(value) -> str:
    return (value or "").replace("\r", "").replace("\t", " ").strip()


def _center(line: str, width: int) -> str:
    line = _text(line)
    if len(line) >= width:
        return line[:width]
    left = (width - len(line)) // 2
    return (" " * left) + line


def _hr(width: int) -> str:
    return "-" * width


def _truncate(line: str, width: int) -> str:
    line = _text(line)
    if len(line) <= width:
        return line
    return line[: max(0, width - 3)] + "..."


def _line_lr(left: str, right: str, width: int) -> str:
    left = _text(left)
    right = _text(right)
    space = width - len(left) - len(right)
    if space < 1:
        left = _truncate(left, max(0, width - len(right) - 1))
        space = max(1, width - len(left) - len(right))
    return f"{left}{' ' * space}{right}"


def _has_box(items) -> bool:
    try:
        return any(_text(getattr(item, "box", "")) for item in items)
    except Exception:
        return False


def _format_sale_row(name, qty, price, total, *, box=None, has_box=False, width: int = LINE_WIDTH_80MM) -> str:
    if has_box:
        item_w, qty_w, box_w, price_w, total_w = (20, 6, 8, 7, 7)
        return (
            _truncate(name, item_w).ljust(item_w)
            + _text(qty).rjust(qty_w)
            + _text(box or "").rjust(box_w)
            + _text(price).rjust(price_w)
            + _text(total).rjust(total_w)
        )[:width]

    item_w, qty_w, price_w, total_w = (26, 6, 8, 8)
    return (
        _truncate(name, item_w).ljust(item_w)
        + _text(qty).rjust(qty_w)
        + _text(price).rjust(price_w)
        + _text(total).rjust(total_w)
    )[:width]


def _format_profit_row(
    name,
    qty,
    cost,
    price,
    total,
    *,
    box=None,
    has_box=False,
    width: int = LINE_WIDTH_80MM,
) -> str:
    if has_box:
        item_w, qty_w, box_w, cost_w, price_w, total_w = (16, 5, 7, 7, 7, 6)
        return (
            _truncate(name, item_w).ljust(item_w)
            + _text(qty).rjust(qty_w)
            + _text(box or "").rjust(box_w)
            + _text(cost).rjust(cost_w)
            + _text(price).rjust(price_w)
            + _text(total).rjust(total_w)
        )[:width]

    item_w, qty_w, cost_w, price_w, total_w = (20, 6, 7, 7, 8)
    return (
        _truncate(name, item_w).ljust(item_w)
        + _text(qty).rjust(qty_w)
        + _text(cost).rjust(cost_w)
        + _text(price).rjust(price_w)
        + _text(total).rjust(total_w)
    )[:width]


def sale_receipt_text(
    *,
    order,
    items,
    total,
    copy_label: str,
    payments=None,
    width: int = LINE_WIDTH_80MM,
) -> str:
    items = list(items)
    has_box = _has_box(items)
    lines = []
    lines.append(_center("G-A SHOP", width))
    lines.append(_center("Jimpex - Kanifing", width))
    lines.append(_center("Tel: 2866668", width))
    lines.append(_center(f"{copy_label} COPY", width))
    lines.append(_hr(width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Date", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    if _text(getattr(order, "customer_name", "")):
        lines.append(_line_lr("Customer", _text(order.customer_name), width))
    lines.append(_hr(width))

    header_box = "Box" if has_box else None
    lines.append(_format_sale_row("Item", "Qty", "Price", "Total", box=header_box, has_box=has_box, width=width))
    lines.append(_hr(width))

    for item in items:
        lines.append(
            _format_sale_row(
                _text(item.item_name),
                _money(item.quantity),
                _money(item.selling_price),
                _money(item.line_total()),
                box=_text(getattr(item, "box", "")) if has_box else None,
                has_box=has_box,
                width=width,
            )
        )

    lines.append(_hr(width))
    subtotal = getattr(order, "subtotal_amount", lambda: total)()
    discount = getattr(order, "discount_amount", lambda: Decimal("0"))()
    if Decimal(str(discount)) > 0:
        lines.append(_line_lr("SUBTOTAL", _money(subtotal), width))
        lines.append(_line_lr("DISCOUNT", "-" + _money(discount), width))
    lines.append(_line_lr("TOTAL", _money(total), width))

    payments = list(payments or [])
    if payments:
        lines.append(_hr(width))
        for p in payments:
            method = (getattr(p, "method", "") or "").upper()
            label = method
            if getattr(p, "method", "") == "bank" and getattr(p, "bank_name", None):
                label = f"BANK ({_text(p.bank_name)})"
            lines.append(_line_lr(label, _money(p.amount_received), width))
            if getattr(p, "method", "") == "cash" and getattr(p, "change_given", None):
                if Decimal(p.change_given or 0) > 0:
                    lines.append(_line_lr("CHANGE", _money(p.change_given), width))

    lines.append("")
    lines.append(_center("Thank you", width))
    lines.append("\n\n")
    return "\n".join(lines)


def refund_receipt_text(
    *,
    order,
    items,
    total,
    copy_label: str,
    payments=None,
    width: int = LINE_WIDTH_80MM,
) -> str:
    items = list(items)
    has_box = _has_box(items)
    lines = []
    lines.append(_center("G-A SHOP", width))
    lines.append(_center("Jimpex - Kanifing", width))
    lines.append(_center("Tel: 2866668", width))
    lines.append(_center("REFUND RECEIPT", width))
    lines.append(_center(f"{copy_label} COPY", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    if _text(getattr(order, "customer_name", "")):
        lines.append(_line_lr("Customer", _text(order.customer_name), width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Original", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_hr(width))

    header_box = "Box" if has_box else None
    lines.append(_format_sale_row("Item", "Qty", "Price", "Total", box=header_box, has_box=has_box, width=width))
    lines.append(_hr(width))

    for item in items:
        lines.append(
            _format_sale_row(
                _text(item.item_name),
                _money(item.quantity),
                _money(item.selling_price),
                _money(item.line_total()),
                box=_text(getattr(item, "box", "")) if has_box else None,
                has_box=has_box,
                width=width,
            )
        )

    lines.append(_hr(width))
    subtotal = getattr(order, "subtotal_amount", lambda: total)()
    discount = getattr(order, "discount_amount", lambda: Decimal("0"))()
    if Decimal(str(discount)) > 0:
        lines.append(_line_lr("SUBTOTAL", _money(subtotal), width))
        lines.append(_line_lr("DISCOUNT", "-" + _money(discount), width))
    lines.append(_line_lr("REFUND TOTAL", _money(total), width))

    payments = list(payments or [])
    if payments:
        lines.append(_hr(width))
        for p in payments:
            method = (getattr(p, "method", "") or "").upper()
            label = method
            if getattr(p, "method", "") == "bank" and getattr(p, "bank_name", None):
                label = f"BANK ({_text(p.bank_name)})"
            lines.append(_line_lr(label, _money(p.amount_received), width))
            if getattr(p, "method", "") == "cash" and getattr(p, "change_given", None):
                if Decimal(p.change_given or 0) > 0:
                    lines.append(_line_lr("CHANGE", _money(p.change_given), width))

    lines.append("")
    lines.append(_center("INTERNAL/STORE POLICY APPLIES", width))
    lines.append("\n\n")
    return "\n".join(lines)


def profit_report_text(*, order, items, total, total_profit, width: int = LINE_WIDTH_80MM) -> str:
    items = list(items)
    has_box = _has_box(items)
    lines = []
    lines.append(_center("G-A SHOP", width))
    lines.append(_center("Jimpex - Kanifing", width))
    lines.append(_center("Tel: 2866668", width))
    lines.append(_center("PROFIT REPORT (INTERNAL)", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    if _text(getattr(order, "customer_name", "")):
        lines.append(_line_lr("Customer", _text(order.customer_name), width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Date", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_hr(width))

    header_box = "Box" if has_box else None
    lines.append(
        _format_profit_row(
            "Item",
            "Qty",
            "Cost",
            "Price",
            "Total",
            box=header_box,
            has_box=has_box,
            width=width,
        )
    )
    lines.append(_hr(width))

    for item in items:
        lines.append(
            _format_profit_row(
                _text(item.item_name),
                _money(item.quantity),
                _money(item.cost_price_snapshot),
                _money(item.selling_price),
                _money(item.line_total()),
                box=_text(getattr(item, "box", "")) if has_box else None,
                has_box=has_box,
                width=width,
            )
        )
        lines.append(_line_lr("Profit", _money(item.line_profit()), width))

    lines.append(_hr(width))
    subtotal = getattr(order, "subtotal_amount", lambda: total)()
    discount = getattr(order, "discount_amount", lambda: Decimal("0"))()
    if Decimal(str(discount)) > 0:
        lines.append(_line_lr("SUBTOTAL", _money(subtotal), width))
        lines.append(_line_lr("DISCOUNT", "-" + _money(discount), width))
    lines.append(_line_lr("TOTAL", _money(total), width))
    lines.append(_line_lr("PROFIT", _money(total_profit), width))
    lines.append("\n\n")
    return "\n".join(lines)


def _expense_label(expense) -> str:
    category = getattr(expense, "get_category_display", lambda: "")()
    description = _text(getattr(expense, "description", ""))
    if category and description:
        return f"{category}: {description}"
    return category or description or "Expense"


def _payment_breakdown(orders):
    breakdown = {
        "cash": Decimal("0"),
        "wave": Decimal("0"),
        "bank": Decimal("0"),
        "aps": Decimal("0"),
    }
    for order in orders:
        for payment in order.payments.all():
            method = getattr(payment, "method", "") or ""
            amount = Decimal(str(getattr(payment, "amount_received", 0) or 0))
            if method == "cash":
                amount -= Decimal(str(getattr(payment, "change_given", 0) or 0))
            if method in breakdown:
                breakdown[method] += amount
    return breakdown


def day_summary_text(*, date, orders, expenses=None, width: int = LINE_WIDTH_80MM) -> str:
    orders = list(orders)
    expenses = list(expenses or [])
    lines = []
    lines.append(_center("G-A SHOP", width))
    lines.append(_center("Jimpex - Kanifing", width))
    lines.append(_center("Tel: 2866668", width))
    lines.append(_center("DAY SUMMARY (INTERNAL)", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Date", date.strftime("%Y-%m-%d"), width))
    lines.append(_hr(width))

    total_sales = Decimal("0")
    total_profit = Decimal("0")
    count = 0

    for order in orders:
        count += 1
        sales = Decimal(str(order.total_amount()))
        profit = Decimal(str(order.total_profit()))
        total_sales += sales
        total_profit += profit
        lines.append(_line_lr(f"Order #{order.order_number}", _money(sales), width))

    lines.append(_hr(width))
    lines.append(_line_lr("Orders", str(count), width))
    lines.append(_line_lr("Total Sales", _money(total_sales), width))
    payments = _payment_breakdown(orders)
    lines.append(_hr(width))
    lines.append("Payment Breakdown")
    lines.append(_line_lr("Cash", _money(payments["cash"]), width))
    lines.append(_line_lr("Wave", _money(payments["wave"]), width))
    lines.append(_hr(width))
    lines.append(_line_lr("Gross Profit", _money(total_profit), width))
    if expenses:
        lines.append(_hr(width))
        total_expenses = Decimal("0")
        lines.append("Expenses")
        for expense in expenses:
            amount = Decimal(str(getattr(expense, "amount", 0)))
            total_expenses += amount
            lines.append(_line_lr(_expense_label(expense), _money(amount), width))
    else:
        total_expenses = Decimal("0")
    lines.append(_line_lr("Total Expenses", _money(total_expenses), width))
    lines.append(_line_lr("Net Profit", _money(total_profit - total_expenses), width))
    lines.append("\n\n")
    return "\n".join(lines)


def month_summary_text(*, year: int, month: int, orders, expenses=None, width: int = LINE_WIDTH_80MM) -> str:
    orders = list(orders)
    expenses = list(expenses or [])
    lines = []
    lines.append(_center("G-A SHOP", width))
    lines.append(_center("Jimpex - Kanifing", width))
    lines.append(_center("Tel: 2866668", width))
    lines.append(_center("MONTH SUMMARY (INTERNAL)", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Month", f"{year:04d}-{month:02d}", width))
    lines.append(_hr(width))

    total_sales = Decimal("0")
    total_profit = Decimal("0")
    count = 0

    for order in orders:
        count += 1
        sales = Decimal(str(order.total_amount()))
        profit = Decimal(str(order.total_profit()))
        total_sales += sales
        total_profit += profit
        lines.append(_line_lr(f"Order #{order.order_number}", _money(sales), width))

    lines.append(_hr(width))
    lines.append(_line_lr("Orders", str(count), width))
    lines.append(_line_lr("Total Sales", _money(total_sales), width))
    payments = _payment_breakdown(orders)
    lines.append(_hr(width))
    lines.append("Payment Breakdown")
    lines.append(_line_lr("Cash", _money(payments["cash"]), width))
    lines.append(_line_lr("Wave", _money(payments["wave"]), width))
    lines.append(_hr(width))
    lines.append(_line_lr("Gross Profit", _money(total_profit), width))
    if expenses:
        lines.append(_hr(width))
        total_expenses = Decimal("0")
        lines.append("Expenses")
        for expense in expenses:
            amount = Decimal(str(getattr(expense, "amount", 0)))
            total_expenses += amount
            lines.append(_line_lr(_expense_label(expense), _money(amount), width))
    else:
        total_expenses = Decimal("0")
    lines.append(_line_lr("Total Expenses", _money(total_expenses), width))
    lines.append(_line_lr("Net Profit", _money(total_profit - total_expenses), width))
    lines.append("\n\n")
    return "\n".join(lines)
