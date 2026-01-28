from decimal import Decimal, ROUND_HALF_UP

from django import template

register = template.Library()


@register.filter(name="whole")
def whole(value):
    if value is None or value == "":
        return "0"
    try:
        if isinstance(value, Decimal):
            return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return str(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
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
