from django.core.management.base import BaseCommand
from marketplace.models import Item, ItemRequest, AlbumImage
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage

"""
Django management sub-command used to delete expired item
listings and item requests (and their associated images from S3).

To run this command: `python manage.py delete_expired`
"""

class Command(BaseCommand):
    help = 'Deletes expired items and item requests'

    def handle(self, *args, **options):
        self.__deleteExpiredItems()
        self.__deleteExpiredItemRequests()

    def __deleteExpiredItems(self):
        expired_items = Item.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER,
            status = Item.AVAILABLE
        )

        for item in expired_items:
            # delete lead image from S3
            default_storage.delete(item.image.name)

            # delete album images
            self.__deleteAlbumImages(item.id)

            # delete the item
            item.delete()

    def __deleteExpiredItemRequests(self):
        expired_item_requests = ItemRequest.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
        )

        for item_request in expired_item_requests:
            # delete lead image from S3
            default_storage.delete(item_request.image.name)

            # delete the item request
            item_request.delete()

    def __deleteAlbumImages(self, item_id):
        album_images = AlbumImage.objects.filter(item=item_id)
        for image in album_images:
            # delete album image from S3
            default_storage.delete(image.image.name)

            # delete album image from table
            image.delete()



