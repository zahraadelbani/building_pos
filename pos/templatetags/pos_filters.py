from decimal import Decimal, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter(name="whole")
def whole(value):
    if value is None or value == "":
        return "0"
    try:
        number = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        formatted = format(number, "f")
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
    except Exception:
        return str(value)


@register.filter(name="quantity")
def quantity(value):
    """Display decimal quantities without unnecessary trailing zeroes."""
    if value is None or value == "":
        return "0"
    try:
        formatted = format(Decimal(str(value)), "f")
        return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
    except Exception:
        return str(value)


@register.filter(name="any_box")
def any_box(items):
    try:
        for item in items:
            if str(getattr(item, "box", "") or "").strip():
                return True
    except Exception:
        return False
    return False
