from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Order, OrderItem, Payment, PrintJob, Product
from .receipt import month_summary_text


class MonthSummaryTests(TestCase):
    def _create_paid_order(self, *, order_number: int, paid_at, quantity="2", cost="30", selling="50"):
        order = Order.objects.create(order_number=order_number, status="paid", is_active=False, created_at=paid_at)
        OrderItem.objects.create(
            order=order,
            item_name=f"Item {order_number}",
            quantity=Decimal(quantity),
            selling_price=Decimal(selling),
            cost_price_snapshot=Decimal(cost),
        )
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal(quantity) * Decimal(selling),
            change_given=Decimal("0"),
            paid_at=paid_at,
        )
        return order

    def test_month_view_shows_only_selected_month_totals(self):
        april_paid_at = timezone.make_aware(datetime(2026, 4, 10, 12, 0))
        may_paid_at = timezone.make_aware(datetime(2026, 5, 2, 9, 30))

        self._create_paid_order(order_number=101, paid_at=april_paid_at)
        self._create_paid_order(order_number=102, paid_at=may_paid_at, quantity="1", cost="20", selling="35")

        response = self.client.get(reverse("month_view"), {"month": "2026-04"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "April 2026")
        self.assertContains(response, "#101")
        self.assertNotContains(response, "#102")
        self.assertEqual(response.context["total_sales"], Decimal("100"))
        self.assertEqual(response.context["total_profit"], Decimal("40"))

    def test_day_view_paginates_paid_orders_without_changing_totals(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 10, 12, 0))
        for order_number in range(200, 226):
            self._create_paid_order(order_number=order_number, paid_at=paid_at, quantity="1", cost="2", selling="5")

        response = self.client.get(reverse("day_view"), {"date": "2026-04-10"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_order_count"], 26)
        self.assertEqual(len(response.context["orders"]), 25)
        self.assertEqual(response.context["total_sales"], Decimal("130"))
        self.assertContains(response, "Page 1 of 2")

    def test_month_view_paginates_paid_orders_without_changing_totals(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 10, 12, 0))
        for order_number in range(300, 326):
            self._create_paid_order(order_number=order_number, paid_at=paid_at, quantity="1", cost="2", selling="5")

        response = self.client.get(reverse("month_view"), {"month": "2026-04", "page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_order_count"], 26)
        self.assertEqual(len(response.context["orders"]), 1)
        self.assertEqual(response.context["total_sales"], Decimal("130"))
        self.assertContains(response, "#325")
        self.assertContains(response, "Page 2 of 2")

    def test_print_month_summary_creates_month_summary_job(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 11, 10, 15))
        self._create_paid_order(order_number=103, paid_at=paid_at, quantity="3", cost="10", selling="25")

        session = self.client.session
        session["pos_device_id"] = "cashier-1"
        session.save()

        response = self.client.post(reverse("print_month_summary"), {"month": "2026-04"})

        self.assertEqual(response.status_code, 302)
        job = PrintJob.objects.get(job_type=PrintJob.JOB_MONTH_SUMMARY)
        self.assertEqual(job.target_device_id, "cashier-1")
        self.assertEqual(job.payload["month"], "2026-04")
        self.assertIn("MONTH SUMMARY (INTERNAL)", job.payload["receipt_text"])

    def test_month_summary_text_includes_profit_totals(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 12, 14, 45))
        order = self._create_paid_order(order_number=104, paid_at=paid_at, quantity="4", cost="5", selling="12")

        text = month_summary_text(year=2026, month=4, orders=[order])

        self.assertIn("Month", text)
        self.assertIn("2026-04", text)
        self.assertIn("Total Sales", text)
        self.assertIn("48", text)
        self.assertIn("Total Profit", text)
        self.assertIn("28", text)


class ProductSearchTests(TestCase):
    def test_pos_numeric_search_matches_product_code_only(self):
        order = Order.objects.create(order_number=1, status="unpaid")
        matching = Product.objects.create(code=1, name="Cement", cost_price=Decimal("10"), selling_price=Decimal("15"))
        Product.objects.create(code=2, name="1m LED 300N", cost_price=Decimal("20"), selling_price=Decimal("30"))

        response = self.client.get(
            reverse("pos_order", args=[order.id]),
            {"q": "1", "partial": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["products"]), [matching])
        self.assertContains(response, "Cement")
        self.assertNotContains(response, "1m LED 300N")


class OrderDeleteTests(TestCase):
    def test_unpaid_order_with_items_can_be_deleted(self):
        order = Order.objects.create(order_number=1, status="unpaid")
        OrderItem.objects.create(
            order=order,
            item_name="Mistake item",
            quantity=Decimal("2"),
            selling_price=Decimal("10"),
            cost_price_snapshot=Decimal("4"),
        )

        response = self.client.post(reverse("delete_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertFalse(OrderItem.objects.filter(order_id=order.id).exists())

    def test_paid_order_can_be_deleted_with_payment_records(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 10, 12, 0))
        order = Order.objects.create(order_number=2, status="paid", is_active=False, created_at=paid_at)
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal("10"),
            change_given=Decimal("0"),
            paid_at=paid_at,
        )

        response = self.client.post(reverse("delete_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertFalse(Payment.objects.filter(order_id=order.id).exists())

    def test_delete_order_removes_related_print_jobs(self):
        order = Order.objects.create(order_number=3, status="unpaid")
        job = PrintJob.objects.create(
            job_type=PrintJob.JOB_SALE_RECEIPT,
            copy_type=PrintJob.COPY_CUSTOMER,
            payload={"order_id": order.id, "order_number": order.order_number, "receipt_text": "test"},
        )
        other_job = PrintJob.objects.create(
            job_type=PrintJob.JOB_SALE_RECEIPT,
            copy_type=PrintJob.COPY_CUSTOMER,
            payload={"order_id": 999, "order_number": 999, "receipt_text": "test"},
        )

        response = self.client.post(reverse("delete_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertFalse(PrintJob.objects.filter(id=job.id).exists())
        self.assertTrue(PrintJob.objects.filter(id=other_job.id).exists())

    def test_delete_order_redirects_to_safe_next_url(self):
        order = Order.objects.create(order_number=4, status="paid", is_active=False)
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal("10"),
            change_given=Decimal("0"),
        )

        response = self.client.post(reverse("delete_order", args=[order.id]), {"next": reverse("reprint_orders")})

        self.assertRedirects(response, reverse("reprint_orders"))
