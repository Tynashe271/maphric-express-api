from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0004_adminactivity'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DeliverySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('delivery_fee', models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ('free_delivery_threshold', models.DecimalField(decimal_places=2, default=50, max_digits=8)),
                ('delivery_areas', models.CharField(default='Bradfield, Bulawayo', max_length=500)),
                ('estimated_minutes', models.PositiveIntegerField(default=55)),
                ('opening_hours', models.CharField(default='Monday to Sunday', max_length=250)),
                ('delivery_policy', models.TextField(blank=True, default='Same-day delivery is available within Bulawayo.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name_plural': 'delivery settings'},
        ),
    ]
