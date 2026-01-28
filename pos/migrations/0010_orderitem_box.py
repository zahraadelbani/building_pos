from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0009_printjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="box",
            field=models.CharField(
                blank=True,
                help_text="Optional box/pack label shown on receipts when provided",
                max_length=50,
                null=True,
            ),
        ),
    ]

