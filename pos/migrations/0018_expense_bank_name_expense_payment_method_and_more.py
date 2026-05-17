from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0017_order_customer_discount"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="bank_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="expense",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("cash", "Cash"),
                    ("wave", "Wave"),
                    ("bank", "Bank"),
                    ("aps", "APS"),
                ],
                default="cash",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(fields=["payment_method"], name="pos_expense_payment_0adaff_idx"),
        ),
    ]
