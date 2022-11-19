from django.core.management.base import BaseCommand
from marketplace.models import Item, ItemRequest
from django.utils import timezone
from django.conf import settings

class Command(BaseCommand):
    help = 'Delete expired items'

    def handle(self, *args, **options):
        print("Running delete expired task")
        self.deleteExpired()

    def deleteExpired(self):
        expired_items = Item.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER,
            status = Item.AVAILABLE
        )
        print(f"Number of expired items: {len(expired_items)}")
        for item in expired_items:
            # delete the item
            item.delete()

        expired_item_requests = ItemRequest.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
        )
        print(f"Number of expired requests: {len(expired_item_requests)}")
        for item_request in expired_item_requests:
            # delete the item request
            item_request.delete()



