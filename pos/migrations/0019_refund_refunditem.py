from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0018_expense_bank_name_expense_payment_method_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Refund",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "method",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("bank", "Bank Transfer"),
                            ("wave", "Wave"),
                            ("aps", "APS"),
                        ],
                        max_length=20,
                    ),
                ),
                ("bank_name", models.CharField(blank=True, max_length=100)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("profit_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("refunded_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("note", models.TextField(blank=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="refunds",
                        to="pos.order",
                    ),
                ),
            ],
            options={
                "ordering": ["-refunded_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="RefundItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item_name", models.CharField(max_length=255)),
                ("box", models.CharField(blank=True, max_length=50, null=True)),
                ("quantity", models.DecimalField(decimal_places=2, max_digits=10)),
                ("selling_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("cost_price_snapshot", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("profit_amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                (
                    "order_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="refund_items",
                        to="pos.orderitem",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="refund_items",
                        to="pos.product",
                    ),
                ),
                (
                    "refund",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="pos.refund",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="refund",
            index=models.Index(fields=["order", "-refunded_at"], name="pos_refund_order_i_5c7ab8_idx"),
        ),
        migrations.AddIndex(
            model_name="refund",
            index=models.Index(fields=["method", "-refunded_at"], name="pos_refund_method__417afd_idx"),
        ),
        migrations.AddIndex(
            model_name="refund",
            index=models.Index(fields=["refunded_at"], name="pos_refund_refunde_0cc4bd_idx"),
        ),
    ]
