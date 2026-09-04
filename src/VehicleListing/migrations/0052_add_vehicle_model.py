# Add Vehicle model and ForeignKey to VehicleListing

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0051_add_missing_columns'),
    ]

    operations = [
        # Create Vehicle model (table already exists in database)
        migrations.CreateModel(
            name='Vehicle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('vin', models.CharField(blank=True, max_length=17, null=True)),
                ('make', models.CharField(blank=True, max_length=100, null=True)),
                ('model', models.CharField(blank=True, max_length=100, null=True)),
                ('year', models.CharField(blank=True, max_length=255, null=True)),
                ('mileage', models.IntegerField(blank=True, null=True)),
                ('transmission', models.CharField(blank=True, max_length=255, null=True)),
                ('fuel_type', models.CharField(blank=True, max_length=255, null=True)),
                ('body_type', models.CharField(blank=True, max_length=255, null=True)),
                ('color', models.CharField(blank=True, max_length=255, null=True)),
                ('variant', models.CharField(blank=True, max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'VehicleListing_vehicle',
            },
        ),
        # Add ForeignKey from VehicleListing to Vehicle (column already exists as vehicle_id)
        migrations.AddField(
            model_name='vehiclelisting',
            name='vehicle',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='VehicleListing.vehicle'),
        ),
    ]
