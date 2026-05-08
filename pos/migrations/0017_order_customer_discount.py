from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0016_product_stock_stockmovement_order_stock_deducted"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="customer_name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="order",
            name="discount_type",
            field=models.CharField(
                choices=[
                    ("none", "No discount"),
                    ("amount", "Amount"),
                    ("percent", "Percent"),
                ],
                default="none",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="discount_value",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
