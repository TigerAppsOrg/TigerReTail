from django.core.management.base import BaseCommand
from marketplace.models import Item, ItemRequest, AlbumImage
from django.utils import timezone
from django.conf import settings
from django.core.files.storage import default_storage

class Command(BaseCommand):
    help = 'Deletes expired items and item requests'

    def handle(self, *args, **options):
        self.deleteExpiredItems()
        self.deleteExpiredItemRequests()

    def deleteExpiredItems(self):
        print("--- Running delete expired task")

        expired_items = Item.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER,
            status = Item.AVAILABLE
        )
        print(f"Number of expired items: {len(expired_items)}")

        for item in expired_items:
            print(f"Deleting item: {item.name}")

            # delete lead image from S3
            print(f"Deleting its image: {item.image.name}")
            default_storage.delete(item.image.name)

            # delete album images
            self.deleteAlbumImages(item.id)

            # delete the item
            item.delete()

    def deleteExpiredItemRequests(self):
        print("--- Running delete expired item requests task")

        expired_item_requests = ItemRequest.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
        )
        print(f"Number of expired requests: {len(expired_item_requests)}")

        for item_request in expired_item_requests:
            print(f"Deleting item request: {item_request.name}")

            # delete lead image from S3
            default_storage.delete(item_request.image.name)
            print(f"Deleting its image: {item_request.image.name}")

             # delete the item request
            item_request.delete()

    def deleteAlbumImages(self, item_id):
        album_images = AlbumImage.objects.filter(item=item_id)
        for image in album_images:
            print(f"Deleting its image: {image.image.name}")

            # delete from S3
            default_storage.delete(image.image.name)

            # delete from table
            image.delete()



