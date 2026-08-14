from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('VehicleListing', '0053_populate_vehiclelisting_from_vehicle'),
    ]

    operations = [
        # Fix facebook_profile: database has facebook_profile_id_id but should be facebook_profile_id
        migrations.RunSQL(
            sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "facebook_profile_id_id" TO "facebook_profile_id";',
            reverse_sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "facebook_profile_id" TO "facebook_profile_id_id";'
        ),
        # Fix custom_domain_profile
        migrations.RunSQL(
            sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "custom_domain_profile_id_id" TO "custom_domain_profile_id";',
            reverse_sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "custom_domain_profile_id" TO "custom_domain_profile_id_id";'
        ),
        # Fix custom_domain_url
        migrations.RunSQL(
            sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "custom_domain_url_id_id" TO "custom_domain_url_id";',
            reverse_sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "custom_domain_url_id" TO "custom_domain_url_id_id";'
        ),
        # Fix gumtree_profile
        migrations.RunSQL(
            sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "gumtree_profile_id_id" TO "gumtree_profile_id";',
            reverse_sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "gumtree_profile_id" TO "gumtree_profile_id_id";'
        ),
        # Fix gumtree_url
        migrations.RunSQL(
            sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "gumtree_url_id_id" TO "gumtree_url_id";',
            reverse_sql='ALTER TABLE "VehicleListing_vehiclelisting" RENAME COLUMN "gumtree_url_id" TO "gumtree_url_id_id";'
        ),
    ]
