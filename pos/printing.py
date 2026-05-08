from django.db import transaction

from .models import Expense, Order, PrintJob
from .receipt import (
    LINE_WIDTH_80MM,
    day_summary_text,
    month_summary_text,
    profit_report_text,
    refund_receipt_text,
    sale_receipt_text,
)


def enqueue_sale_receipt(*, order, target_device_id: str):
    items = list(order.items.all())
    payments = list(order.payments.all().order_by("paid_at", "id"))
    total = order.total_amount()

    customer_text = sale_receipt_text(
        order=order,
        items=items,
        total=total,
        copy_label="CUSTOMER",
        payments=payments,
        width=LINE_WIDTH_80MM,
    )

    with transaction.atomic():
        PrintJob.objects.create(
            job_type=PrintJob.JOB_SALE_RECEIPT,
            copy_type=PrintJob.COPY_CUSTOMER,
            payload={"receipt_text": customer_text, "order_id": order.id, "order_number": order.order_number},
            target_device_id=target_device_id,
            priority=10,
        )


def enqueue_sale_copy(*, order, copy_type: str, target_device_id: str):
    if copy_type != PrintJob.COPY_CUSTOMER:
        return

    items = list(order.items.all())
    payments = list(order.payments.all().order_by("paid_at", "id"))
    total = order.total_amount()
    copy_label = "CUSTOMER"
    text = sale_receipt_text(
        order=order,
        items=items,
        total=total,
        copy_label=copy_label,
        payments=payments,
        width=LINE_WIDTH_80MM,
    )
    PrintJob.objects.create(
        job_type=PrintJob.JOB_SALE_RECEIPT,
        copy_type=copy_type,
        payload={"receipt_text": text, "order_id": order.id, "order_number": order.order_number},
        target_device_id=target_device_id,
        priority=10 if copy_type == PrintJob.COPY_CUSTOMER else 9,
    )


def enqueue_refund_receipt(*, order, target_device_id: str):
    items = list(order.items.all())
    payments = list(order.payments.all().order_by("paid_at", "id"))
    total = order.total_amount()

    customer_text = refund_receipt_text(
        order=order,
        items=items,
        total=total,
        copy_label="CUSTOMER",
        payments=payments,
        width=LINE_WIDTH_80MM,
    )

    with transaction.atomic():
        PrintJob.objects.create(
            job_type=PrintJob.JOB_REFUND_RECEIPT,
            copy_type=PrintJob.COPY_CUSTOMER,
            payload={"receipt_text": customer_text, "order_id": order.id, "order_number": order.order_number},
            target_device_id=target_device_id,
            priority=10,
        )


def enqueue_profit_report(*, order, target_device_id: str):
    items = list(order.items.all())
    total = order.total_amount()
    total_profit = order.total_profit()
    text = profit_report_text(
        order=order,
        items=items,
        total=total,
        total_profit=total_profit,
        width=LINE_WIDTH_80MM,
    )

    PrintJob.objects.create(
        job_type=PrintJob.JOB_PROFIT_REPORT,
        copy_type=PrintJob.COPY_PROFIT,
        payload={"receipt_text": text, "order_id": order.id, "order_number": order.order_number},
        target_device_id=target_device_id,
        priority=1,
    )


def enqueue_day_summary(*, date, target_device_id: str):
    orders = Order.objects.filter(
        status="paid",
        payments__paid_at__date=date,
    ).prefetch_related("payments").order_by("order_number")
    orders = orders.distinct()
    expenses = Expense.objects.filter(date=date).order_by("created_at", "id")
    text = day_summary_text(date=date, orders=orders, expenses=expenses, width=LINE_WIDTH_80MM)

    PrintJob.objects.create(
        job_type=PrintJob.JOB_DAY_SUMMARY,
        copy_type=PrintJob.COPY_PROFIT,
        payload={"receipt_text": text, "date": date.strftime("%Y-%m-%d")},
        target_device_id=target_device_id,
        priority=1,
    )


def enqueue_month_summary(*, year: int, month: int, target_device_id: str):
    orders = Order.objects.filter(
        status="paid",
        payments__paid_at__year=year,
        payments__paid_at__month=month,
    ).prefetch_related("payments").order_by("order_number")
    orders = orders.distinct()
    expenses = Expense.objects.filter(date__year=year, date__month=month).order_by("date", "created_at", "id")
    text = month_summary_text(year=year, month=month, orders=orders, expenses=expenses, width=LINE_WIDTH_80MM)

    PrintJob.objects.create(
        job_type=PrintJob.JOB_MONTH_SUMMARY,
        copy_type=PrintJob.COPY_PROFIT,
        payload={"receipt_text": text, "month": f"{year:04d}-{month:02d}"},
        target_device_id=target_device_id,
        priority=1,
    )
