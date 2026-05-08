from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0013_printjob_month_summary"),
    ]

    operations = [
        migrations.CreateModel(
            name="Expense",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(default=django.utils.timezone.localdate)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("rent", "Rent"),
                            ("utilities", "Utilities"),
                            ("supplies", "Supplies"),
                            ("salaries", "Salaries"),
                            ("transport", "Transport"),
                            ("repairs", "Repairs"),
                            ("other", "Other"),
                        ],
                        default="other",
                        max_length=20,
                    ),
                ),
                ("description", models.CharField(max_length=255)),
                ("vendor", models.CharField(blank=True, max_length=255)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["-date", "-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(fields=["date"], name="pos_expense_date_467b66_idx"),
        ),
        migrations.AddIndex(
            model_name="expense",
            index=models.Index(fields=["category"], name="pos_expense_categor_0d041d_idx"),
        ),
    ]
