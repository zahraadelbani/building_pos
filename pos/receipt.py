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


def sale_receipt_text(
    *,
    order,
    items,
    total,
    copy_label: str,
    payments=None,
    width: int = LINE_WIDTH_80MM,
) -> str:
    lines = []
    lines.append(_center("BUILDING MATERIALS SHOP", width))
    lines.append(_center(f"{copy_label} COPY", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Date", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_hr(width))

    for item in items:
        name = _text(item.item_name)
        lines.append(_truncate(name, width))
        detail = f"{_money(item.quantity)} x {_money(item.selling_price)}"
        lines.append(_line_lr(detail, _money(item.line_total()), width))

    lines.append(_hr(width))
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
    lines = []
    lines.append(_center("BUILDING MATERIALS SHOP", width))
    lines.append(_center("REFUND RECEIPT", width))
    lines.append(_center(f"{copy_label} COPY", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Original", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_hr(width))

    for item in items:
        name = _text(item.item_name)
        lines.append(_truncate(name, width))
        detail = f"{_money(item.quantity)} x {_money(item.selling_price)}"
        lines.append(_line_lr(detail, _money(item.line_total()), width))

    lines.append(_hr(width))
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
    lines = []
    lines.append(_center("BUILDING MATERIALS SHOP", width))
    lines.append(_center("PROFIT REPORT (INTERNAL)", width))
    lines.append(_hr(width))
    lines.append(_line_lr("Order #", str(order.order_number), width))
    created = timezone.localtime(order.created_at) if order.created_at else timezone.now()
    lines.append(_line_lr("Date", created.strftime("%Y-%m-%d %H:%M"), width))
    lines.append(_hr(width))

    for item in items:
        name = _text(item.item_name)
        lines.append(_truncate(name, width))
        detail = f"Qty {_money(item.quantity)}  Sell {_money(item.selling_price)}  Cost {_money(item.cost_price_snapshot)}"
        lines.append(_truncate(detail, width))
        lines.append(_line_lr("Profit", _money(item.line_profit()), width))

    lines.append(_hr(width))
    lines.append(_line_lr("TOTAL PROFIT", _money(total_profit), width))
    lines.append(_line_lr("ORDER TOTAL", _money(total), width))
    lines.append("\n\n")
    return "\n".join(lines)
