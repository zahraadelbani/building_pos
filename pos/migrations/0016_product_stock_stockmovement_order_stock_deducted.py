from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0015_remove_expense_vendor"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="stock_deducted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="low_stock_threshold",
            field=models.DecimalField(
                decimal_places=2,
                default=5,
                help_text="Show low-stock warning at or below this quantity",
                max_digits=12,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="stock_quantity",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Current quantity available for sale",
                max_digits=12,
            ),
        ),
        migrations.CreateModel(
            name="StockMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("restock", "Restock"),
                            ("adjustment", "Adjustment"),
                            ("sale", "Sale"),
                            ("refund", "Refund"),
                        ],
                        max_length=20,
                    ),
                ),
                ("quantity_delta", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.CharField(max_length=255)),
                ("note", models.TextField(blank=True)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="stock_movements",
                        to="pos.order",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stock_movements",
                        to="pos.product",
                    ),
                ),
            ],
            options={
                "ordering": ["-occurred_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["product", "-occurred_at"], name="pos_stockmo_product_5d4f33_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["movement_type", "-occurred_at"], name="pos_stockmo_movemen_bb9bfb_idx"),
        ),
    ]
