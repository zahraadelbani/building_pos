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

