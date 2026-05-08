from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0014_expense"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="expense",
            name="vendor",
        ),
    ]
