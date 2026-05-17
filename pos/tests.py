from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Expense, Order, OrderItem, Payment, PrintJob, Product, StockMovement
from .receipt import day_summary_text, month_summary_text
from .receipt import sale_receipt_text


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
        self.assertEqual(response.context["total_expenses"], Decimal("0"))
        self.assertEqual(response.context["net_profit"], Decimal("40"))

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
        self.assertIn("Gross Profit", text)
        self.assertIn("28", text)

    def test_day_view_subtracts_expenses_from_net_profit(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 14, 11, 0))
        order = self._create_paid_order(order_number=105, paid_at=paid_at, quantity="2", cost="20", selling="50")
        Expense.objects.create(
            date=paid_at.date(),
            category=Expense.CATEGORY_UTILITIES,
            description="Electricity",
            amount=Decimal("15"),
        )
        Expense.objects.create(
            date=paid_at.date(),
            category=Expense.CATEGORY_TRANSPORT,
            description="Delivery fuel",
            amount=Decimal("5"),
        )

        response = self.client.get(reverse("day_view"), {"date": "2026-04-14"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_profit"], Decimal("60"))
        self.assertEqual(response.context["total_expenses"], Decimal("20"))
        self.assertEqual(response.context["net_profit"], Decimal("40"))
        self.assertContains(response, "Net Profit")
        self.assertContains(response, "Electricity")

        text = day_summary_text(date=paid_at.date(), orders=[order], expenses=Expense.objects.all())
        self.assertIn("Total Expenses", text)
        self.assertIn("Net Profit", text)
        self.assertIn("40", text)

    def test_day_summary_text_includes_wave_payment_breakdown(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 14, 12, 0))
        order = self._create_paid_order(order_number=107, paid_at=paid_at, quantity="2", cost="20", selling="50")
        order.payments.all().delete()
        Payment.objects.create(
            order=order,
            method="wave",
            amount_received=Decimal("100"),
            change_given=Decimal("0"),
            paid_at=paid_at,
        )

        text = day_summary_text(date=paid_at.date(), orders=[order])

        self.assertIn("Payment Breakdown", text)
        self.assertIn("Wave", text)
        self.assertIn("100", text)
        self.assertNotIn("Bank", text)
        self.assertNotIn("APS", text)
        self.assertIn("Gross Profit", text)
        self.assertIn("Net Profit", text)

    def test_day_view_shows_cash_and_wave_payment_totals_and_order_method(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 14, 13, 0))
        cash_order = self._create_paid_order(order_number=109, paid_at=paid_at, quantity="1", cost="10", selling="25")
        wave_order = self._create_paid_order(order_number=110, paid_at=paid_at, quantity="1", cost="10", selling="40")
        wave_order.payments.all().delete()
        Payment.objects.create(
            order=wave_order,
            method="wave",
            amount_received=Decimal("40"),
            paid_at=paid_at,
        )

        response = self.client.get(reverse("day_view"), {"date": "2026-04-14"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_cash"], Decimal("25"))
        self.assertEqual(response.context["total_wave"], Decimal("40"))
        self.assertContains(response, "Cash 25")
        self.assertContains(response, "Wave 40")

    def test_month_view_subtracts_expenses_from_net_profit(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 15, 10, 0))
        self._create_paid_order(order_number=106, paid_at=paid_at, quantity="3", cost="10", selling="30")
        Expense.objects.create(
            date=paid_at.date(),
            category=Expense.CATEGORY_RENT,
            description="Shop rent",
            amount=Decimal("25"),
        )
        Expense.objects.create(
            date=datetime(2026, 5, 1).date(),
            category=Expense.CATEGORY_REPAIRS,
            description="May repair",
            amount=Decimal("99"),
        )

        response = self.client.get(reverse("month_view"), {"month": "2026-04"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_profit"], Decimal("60"))
        self.assertEqual(response.context["total_expenses"], Decimal("25"))
        self.assertEqual(response.context["net_profit"], Decimal("35"))

    def test_month_summary_text_includes_wave_payment_breakdown(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 16, 12, 0))
        order = self._create_paid_order(order_number=108, paid_at=paid_at, quantity="1", cost="10", selling="45")
        order.payments.all().delete()
        Payment.objects.create(
            order=order,
            method="wave",
            amount_received=Decimal("45"),
            change_given=Decimal("0"),
            paid_at=paid_at,
        )

        text = month_summary_text(year=2026, month=4, orders=[order])

        self.assertIn("Payment Breakdown", text)
        self.assertIn("Wave", text)
        self.assertIn("45", text)
        self.assertNotIn("Bank", text)
        self.assertNotIn("APS", text)

    def test_month_view_shows_cash_and_wave_payment_totals_and_order_method(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 17, 13, 0))
        cash_order = self._create_paid_order(order_number=111, paid_at=paid_at, quantity="1", cost="10", selling="30")
        wave_order = self._create_paid_order(order_number=112, paid_at=paid_at, quantity="1", cost="10", selling="55")
        wave_order.payments.all().delete()
        Payment.objects.create(
            order=wave_order,
            method="wave",
            amount_received=Decimal("55"),
            paid_at=paid_at,
        )

        response = self.client.get(reverse("month_view"), {"month": "2026-04"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_cash"], Decimal("30"))
        self.assertEqual(response.context["total_wave"], Decimal("55"))
        self.assertContains(response, "Cash 30")
        self.assertContains(response, "Wave 55")


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

    def test_pos_search_orders_products_by_code(self):
        order = Order.objects.create(order_number=2, status="unpaid")
        first = Product.objects.create(
            code=1,
            name="Cement 50kg",
            cost_price=Decimal("10"),
            selling_price=Decimal("15"),
            stock_quantity=Decimal("5"),
        )
        third = Product.objects.create(
            code=3,
            name="Cement grey",
            cost_price=Decimal("12"),
            selling_price=Decimal("18"),
            stock_quantity=Decimal("5"),
        )
        second = Product.objects.create(
            code=2,
            name="Cement white",
            cost_price=Decimal("11"),
            selling_price=Decimal("16"),
            stock_quantity=Decimal("5"),
        )

        response = self.client.get(
            reverse("pos_order", args=[order.id]),
            {"q": "Cement", "partial": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["products"]), [first, second, third])


class CustomItemTests(TestCase):
    def test_custom_item_is_added_to_order_without_creating_product(self):
        order = Order.objects.create(order_number=10, status="unpaid")

        response = self.client.post(
            reverse("add_custom_item_to_order", args=[order.id]),
            {
                "item_name": "Delivery charge",
                "quantity": "1",
                "selling_price": "25",
                "cost_price": "5",
                "box": "A1",
            },
        )

        self.assertRedirects(response, reverse("pos_order", args=[order.id]))
        item = OrderItem.objects.get(order=order)
        self.assertIsNone(item.product)
        self.assertEqual(item.item_name, "Delivery charge")
        self.assertEqual(item.quantity, Decimal("1"))
        self.assertEqual(item.selling_price, Decimal("25"))
        self.assertEqual(item.cost_price_snapshot, Decimal("5"))
        self.assertEqual(item.box, "A1")
        self.assertFalse(Product.objects.exists())

    def test_custom_item_counts_in_day_and_month_totals(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 13, 9, 0))
        order = Order.objects.create(order_number=11, status="paid", is_active=False, created_at=paid_at)
        OrderItem.objects.create(
            order=order,
            product=None,
            item_name="One-off fitting",
            quantity=Decimal("2"),
            selling_price=Decimal("40"),
            cost_price_snapshot=Decimal("15"),
        )
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal("80"),
            change_given=Decimal("0"),
            paid_at=paid_at,
        )

        day_response = self.client.get(reverse("day_view"), {"date": "2026-04-13"})
        month_response = self.client.get(reverse("month_view"), {"month": "2026-04"})

        self.assertEqual(day_response.context["total_sales"], Decimal("80"))
        self.assertEqual(day_response.context["total_profit"], Decimal("50"))
        self.assertEqual(month_response.context["total_sales"], Decimal("80"))
        self.assertEqual(month_response.context["total_profit"], Decimal("50"))


class DiscountAndCustomerTests(TestCase):
    def _create_order_with_item(self):
        order = Order.objects.create(order_number=30, status="unpaid")
        OrderItem.objects.create(
            order=order,
            item_name="Cement",
            quantity=Decimal("2"),
            selling_price=Decimal("100"),
            cost_price_snapshot=Decimal("60"),
        )
        return order

    def test_amount_discount_reduces_total_and_profit(self):
        order = self._create_order_with_item()
        order.discount_type = Order.DISCOUNT_AMOUNT
        order.discount_value = Decimal("30")
        order.save()

        self.assertEqual(order.subtotal_amount(), Decimal("200"))
        self.assertEqual(order.discount_amount(), Decimal("30"))
        self.assertEqual(order.total_amount(), Decimal("170"))
        self.assertEqual(order.total_profit(), Decimal("50"))

    def test_percent_discount_reduces_total(self):
        order = self._create_order_with_item()
        order.discount_type = Order.DISCOUNT_PERCENT
        order.discount_value = Decimal("10")
        order.save()

        self.assertEqual(order.discount_amount(), Decimal("20"))
        self.assertEqual(order.total_amount(), Decimal("180"))

    def test_update_order_details_saves_customer_and_discount(self):
        order = self._create_order_with_item()

        response = self.client.post(
            reverse("update_order_details", args=[order.id]),
            {
                "customer_name": "Fatou Jallow",
                "discount_type": Order.DISCOUNT_PERCENT,
                "discount_value": "5",
            },
        )

        self.assertRedirects(response, reverse("pos_order", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.customer_name, "Fatou Jallow")
        self.assertEqual(order.discount_type, Order.DISCOUNT_PERCENT)
        self.assertEqual(order.discount_value, Decimal("5.00"))
        self.assertEqual(order.total_amount(), Decimal("190"))

    def test_receipt_includes_customer_and_discount(self):
        order = self._create_order_with_item()
        order.customer_name = "Fatou Jallow"
        order.discount_type = Order.DISCOUNT_AMOUNT
        order.discount_value = Decimal("25")
        order.save()

        text = sale_receipt_text(
            order=order,
            items=order.items.all(),
            total=order.total_amount(),
            copy_label="CUSTOMER",
        )

        self.assertIn("Customer", text)
        self.assertIn("Fatou Jallow", text)
        self.assertIn("SUBTOTAL", text)
        self.assertIn("DISCOUNT", text)
        self.assertIn("175", text)

    def test_mark_paid_uses_discounted_total(self):
        order = self._create_order_with_item()
        order.discount_type = Order.DISCOUNT_AMOUNT
        order.discount_value = Decimal("50")
        order.save()

        response = self.client.post(reverse("mark_order_paid", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(order=order)
        self.assertEqual(payment.amount_received, Decimal("150"))

    def test_mark_paid_can_record_wave_payment(self):
        order = self._create_order_with_item()

        response = self.client.post(
            reverse("mark_order_paid", args=[order.id]),
            {"method": "wave", "amount_received": "200"},
        )

        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(order=order)
        self.assertEqual(payment.method, "wave")
        self.assertEqual(payment.amount_received, Decimal("200"))

    def test_wave_payment_requires_exact_amount(self):
        order = self._create_order_with_item()

        response = self.client.post(
            reverse("mark_order_paid", args=[order.id]),
            {"method": "wave", "amount_received": "150"},
        )

        self.assertRedirects(response, reverse("pos_order", args=[order.id]))
        self.assertFalse(Payment.objects.filter(order=order).exists())


class StockLogicTests(TestCase):
    def test_product_with_no_stock_can_be_added_to_order(self):
        order = Order.objects.create(order_number=19, status="unpaid")
        product = Product.objects.create(
            code=19,
            name="Blocks",
            cost_price=Decimal("5"),
            selling_price=Decimal("8"),
            stock_quantity=Decimal("0"),
        )

        response = self.client.post(
            reverse("add_product_to_order", args=[order.id, product.id]),
            {"quantity": "1", "selling_price": "8"},
        )

        self.assertRedirects(response, reverse("pos_order", args=[order.id]))
        item = OrderItem.objects.get(order=order)
        self.assertEqual(item.product, product)
        self.assertEqual(item.quantity, Decimal("1.00"))

    def test_product_order_can_exceed_available_stock_when_added(self):
        order = Order.objects.create(order_number=20, status="unpaid")
        product = Product.objects.create(
            code=20,
            name="Cement",
            cost_price=Decimal("50"),
            selling_price=Decimal("75"),
            stock_quantity=Decimal("5"),
        )

        response = self.client.post(
            reverse("add_product_to_order", args=[order.id, product.id]),
            {"quantity": "6", "selling_price": "75"},
        )

        self.assertRedirects(response, reverse("pos_order", args=[order.id]))
        self.assertTrue(OrderItem.objects.filter(order=order, product=product).exists())
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("5.00"))

    def test_mark_paid_deducts_product_stock_and_records_movement(self):
        order = Order.objects.create(order_number=21, status="unpaid")
        product = Product.objects.create(
            code=21,
            name="Steel",
            cost_price=Decimal("100"),
            selling_price=Decimal("150"),
            stock_quantity=Decimal("10"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            item_name=product.name,
            quantity=Decimal("3"),
            selling_price=Decimal("150"),
            cost_price_snapshot=Decimal("100"),
        )

        response = self.client.post(reverse("mark_order_paid", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("pos_home"))
        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("7.00"))
        self.assertIsNotNone(order.stock_deducted_at)
        movement = StockMovement.objects.get(product=product)
        self.assertEqual(movement.movement_type, StockMovement.TYPE_SALE)
        self.assertEqual(movement.quantity_delta, Decimal("-3.00"))

    def test_mark_paid_allows_negative_stock(self):
        order = Order.objects.create(order_number=22, status="unpaid")
        product = Product.objects.create(
            code=22,
            name="Pipe",
            cost_price=Decimal("10"),
            selling_price=Decimal("20"),
            stock_quantity=Decimal("2"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            item_name=product.name,
            quantity=Decimal("3"),
            selling_price=Decimal("20"),
            cost_price_snapshot=Decimal("10"),
        )

        response = self.client.post(reverse("mark_order_paid", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("pos_home"))
        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("-1.00"))
        self.assertEqual(order.status, "paid")
        movement = StockMovement.objects.get(product=product)
        self.assertEqual(movement.quantity_delta, Decimal("-3.00"))

    def test_refund_restores_product_stock(self):
        order = Order.objects.create(order_number=23, status="unpaid")
        product = Product.objects.create(
            code=23,
            name="Paint",
            cost_price=Decimal("25"),
            selling_price=Decimal("40"),
            stock_quantity=Decimal("8"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            item_name=product.name,
            quantity=Decimal("2"),
            selling_price=Decimal("40"),
            cost_price_snapshot=Decimal("25"),
        )
        self.client.post(reverse("mark_order_paid", args=[order.id]))

        response = self.client.post(reverse("refund_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("pos_home"))
        product.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("8.00"))
        self.assertEqual(order.status, "refunded")
        self.assertIsNone(order.stock_deducted_at)
        self.assertEqual(StockMovement.objects.filter(product=product).count(), 2)

    def test_custom_item_payment_does_not_create_stock_movement(self):
        order = Order.objects.create(order_number=24, status="unpaid")
        OrderItem.objects.create(
            order=order,
            product=None,
            item_name="Delivery",
            quantity=Decimal("1"),
            selling_price=Decimal("10"),
            cost_price_snapshot=Decimal("0"),
        )

        response = self.client.post(reverse("mark_order_paid", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("pos_home"))
        self.assertFalse(StockMovement.objects.exists())

    def test_manual_stock_adjustment_updates_stock_and_records_reason(self):
        product = Product.objects.create(
            code=25,
            name="Sand",
            cost_price=Decimal("5"),
            selling_price=Decimal("8"),
            stock_quantity=Decimal("4"),
        )

        response = self.client.post(
            reverse("adjust_product_stock", args=[product.id]),
            {
                "adjustment_type": "restock",
                "quantity": "6",
                "reason": "New delivery",
                "occurred_at": "2026-04-20",
            },
        )

        self.assertRedirects(response, reverse("products"))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("10.00"))
        movement = StockMovement.objects.get(product=product)
        self.assertEqual(movement.movement_type, StockMovement.TYPE_RESTOCK)
        self.assertEqual(movement.quantity_delta, Decimal("6.00"))
        self.assertEqual(movement.reason, "New delivery")

    def test_stock_movements_page_filters_by_product_type_and_date(self):
        cement = Product.objects.create(
            code=26,
            name="Cement bags",
            cost_price=Decimal("50"),
            selling_price=Decimal("75"),
            stock_quantity=Decimal("0"),
        )
        steel = Product.objects.create(
            code=27,
            name="Steel rods",
            cost_price=Decimal("100"),
            selling_price=Decimal("130"),
            stock_quantity=Decimal("0"),
        )
        StockMovement.objects.create(
            product=cement,
            movement_type=StockMovement.TYPE_RESTOCK,
            quantity_delta=Decimal("10"),
            reason="Cement delivery",
            occurred_at=timezone.make_aware(datetime(2026, 4, 21, 9, 0)),
        )
        StockMovement.objects.create(
            product=steel,
            movement_type=StockMovement.TYPE_ADJUSTMENT,
            quantity_delta=Decimal("-2"),
            reason="Steel damage",
            occurred_at=timezone.make_aware(datetime(2026, 4, 22, 9, 0)),
        )

        response = self.client.get(
            reverse("stock_movements"),
            {
                "product": str(cement.id),
                "type": StockMovement.TYPE_RESTOCK,
                "date": "2026-04-21",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cement delivery")
        self.assertNotContains(response, "Steel damage")
        self.assertEqual(response.context["total_movement_count"], 1)
        self.assertEqual(response.context["total_in"], Decimal("10"))
        self.assertEqual(response.context["total_out"], Decimal("0"))


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

    def test_deleting_paid_order_restores_deducted_stock(self):
        paid_at = timezone.make_aware(datetime(2026, 4, 10, 12, 0))
        order = Order.objects.create(order_number=20, status="unpaid", is_active=True, created_at=paid_at)
        product = Product.objects.create(
            code=20,
            name="Cement",
            cost_price=Decimal("50"),
            selling_price=Decimal("80"),
            stock_quantity=Decimal("10"),
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            item_name=product.name,
            quantity=Decimal("3"),
            selling_price=Decimal("80"),
            cost_price_snapshot=Decimal("50"),
        )
        self.client.post(reverse("mark_order_paid", args=[order.id]))
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("7.00"))

        response = self.client.post(reverse("delete_order", args=[order.id]))

        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, Decimal("10.00"))
        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertFalse(StockMovement.objects.filter(product=product).exists())

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


class OrderViewTests(TestCase):
    def test_reprint_page_links_to_read_only_order_view(self):
        order = Order.objects.create(order_number=5, status="paid", is_active=False)
        OrderItem.objects.create(
            order=order,
            item_name="Cement",
            quantity=Decimal("2"),
            selling_price=Decimal("100"),
            cost_price_snapshot=Decimal("70"),
        )
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal("200"),
            change_given=Decimal("0"),
        )

        response = self.client.get(reverse("reprint_orders"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View")
        self.assertContains(response, reverse("view_order", args=[order.id]))

    def test_order_view_shows_order_without_creating_print_job(self):
        order = Order.objects.create(order_number=6, status="paid", is_active=False)
        OrderItem.objects.create(
            order=order,
            item_name="Steel",
            quantity=Decimal("3"),
            selling_price=Decimal("250"),
            cost_price_snapshot=Decimal("180"),
        )
        Payment.objects.create(
            order=order,
            method="cash",
            amount_received=Decimal("750"),
            change_given=Decimal("0"),
        )

        response = self.client.get(reverse("view_order", args=[order.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order #6")
        self.assertContains(response, "Steel")
        self.assertContains(response, "750")
        self.assertFalse(PrintJob.objects.exists())
