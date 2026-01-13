from django.db import transaction

from .models import PrintJob
from .receipt import LINE_WIDTH_80MM, profit_report_text, refund_receipt_text, sale_receipt_text


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
    store_text = sale_receipt_text(
        order=order,
        items=items,
        total=total,
        copy_label="STORE",
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
        PrintJob.objects.create(
            job_type=PrintJob.JOB_SALE_RECEIPT,
            copy_type=PrintJob.COPY_STORE,
            payload={"receipt_text": store_text, "order_id": order.id, "order_number": order.order_number},
            target_device_id=target_device_id,
            priority=9,
        )


def enqueue_sale_copy(*, order, copy_type: str, target_device_id: str):
    items = list(order.items.all())
    payments = list(order.payments.all().order_by("paid_at", "id"))
    total = order.total_amount()
    copy_label = "CUSTOMER" if copy_type == PrintJob.COPY_CUSTOMER else "STORE"
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
    store_text = refund_receipt_text(
        order=order,
        items=items,
        total=total,
        copy_label="STORE",
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
        PrintJob.objects.create(
            job_type=PrintJob.JOB_REFUND_RECEIPT,
            copy_type=PrintJob.COPY_STORE,
            payload={"receipt_text": store_text, "order_id": order.id, "order_number": order.order_number},
            target_device_id=target_device_id,
            priority=9,
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
