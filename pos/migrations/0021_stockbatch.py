from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def create_opening_batches(apps, schema_editor):
    Product = apps.get_model("pos", "Product")
    StockBatch = apps.get_model("pos", "StockBatch")
    for product in Product.objects.filter(stock_quantity__gt=0).iterator():
        StockBatch.objects.create(
            product=product,
            initial_quantity=product.stock_quantity,
            remaining_quantity=product.stock_quantity,
            cost_price=product.cost_price,
            selling_price=product.selling_price,
            received_at=product.created_at,
        )


class Migration(migrations.Migration):
    dependencies = [("pos", "0020_rename_pos_refund_order_i_5c7ab8_idx_pos_refund_order_i_a7bd5d_idx_and_more")]

    operations = [
        migrations.CreateModel(
            name="StockBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("initial_quantity", models.DecimalField(decimal_places=2, max_digits=12)),
                ("remaining_quantity", models.DecimalField(decimal_places=2, max_digits=12)),
                ("cost_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("selling_price", models.DecimalField(decimal_places=2, max_digits=10)),
                ("received_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stock_batches", to="pos.product")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(
            model_name="stockbatch",
            index=models.Index(fields=["product", "id"], name="pos_stockba_product_491931_idx"),
        ),
        migrations.RunPython(create_opening_batches, migrations.RunPython.noop),
    ]
