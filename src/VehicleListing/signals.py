"""
S3 cleanup trigger: whenever a VehicleListingImage slot is deleted (a listing
was permanently removed, or a relist dropped a photo from the set), check
whether the HostedImage it pointed at is now orphaned and garbage-collect its
S3 objects if so.

Deliberately NOT triggered by VehicleListing.status becoming "sold" — sold
listings can be reactivated (see utils.reactivate_listing) without a fresh
scrape, and deleting their images at that point would mean serving a broken
photo until the next relist re-downloads everything. Only an actual row
delete (VehicleListing.objects.delete() / queryset.delete(), which cascades
to image_slots) counts as "permanently removed" here.
"""
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import VehicleListingImage


@receiver(post_delete, sender=VehicleListingImage)
def enqueue_hosted_image_gc(sender, instance, **kwargs):
    hosted_image_id = instance.hosted_image_id
    if not hosted_image_id:
        return
    from .tasks import gc_hosted_image_task
    transaction.on_commit(lambda: gc_hosted_image_task.delay(hosted_image_id))
