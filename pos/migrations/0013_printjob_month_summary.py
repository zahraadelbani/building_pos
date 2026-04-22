from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0012_product_selling_price_alter_printjob_job_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="printjob",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("SALE_RECEIPT", "Sale receipt"),
                    ("REFUND_RECEIPT", "Refund receipt"),
                    ("PROFIT_REPORT", "Profit report"),
                    ("DAY_SUMMARY", "Day summary"),
                    ("MONTH_SUMMARY", "Month summary"),
                ],
                max_length=20,
            ),
        ),
    ]
