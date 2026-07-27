from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0001_initial')]
    operations = [
        migrations.AddField(model_name='order', name='payment_method', field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name='order', name='payment_status', field=models.CharField(default='unpaid', max_length=30)),
        migrations.AddField(model_name='order', name='paynow_poll_url', field=models.URLField(blank=True, max_length=500)),
        migrations.AddField(model_name='order', name='paynow_reference', field=models.CharField(blank=True, max_length=100)),
    ]
