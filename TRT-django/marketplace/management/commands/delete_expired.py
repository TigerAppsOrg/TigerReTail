from django.core.management.base import BaseCommand
from marketplace.models import (Item, ItemRequest)
from django.utils import timezone
from django.conf import settings

class Command(BaseCommand):
    help = 'Delete expired items'

    def handle(self, *args, **options):
        self.deleteExpired()

    def deleteExpired(self):
        expired_items = Item.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER,
            status = Item.AVAILABLE
        )
        print("Running delete expired task")
        print(f"Number of expired items: {len(expired_items)}")
        for item in expired_items:
            pass
            # send email notice
            # send_mail_activity(
            #     "Expired Item Removed",
            #     "Your expired item '"
            #     + item.name
            #     + "' has been removed.\nIf you would still like to sell your item, please feel free to relist it.",
            #     settings.EMAIL_NAME,
            #     [item.seller],
            #     fail_silently=True,
            # )
            # # notify the seller
            # notify(
            #     item.seller,
            #     "Your expired item '" + item.name + "' has been removed",
            #     reverse("list_items"),
            # )
            # delete the item
            item.delete()

        expired_item_requests = ItemRequest.objects.filter(
            deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
        )
        print(f"Number of expired requests: {len(expired_item_requests)}")
        for item_request in expired_item_requests:
            pass
            # send email notice
            # send_mail_activity(
            #     "Expired Item Request Removed",
            #     "Your expired item request '"
            #     + item_request.name
            #     + "' has been removed.\nIf you would still like to request the item, please feel free to make another request.",
            #     settings.EMAIL_NAME,
            #     [item_request.requester],
            #     fail_silently=True,
            # )
            # # notify the requester
            # notify(
            #     item_request.requester,
            #     "Your expired item request '" + item_request.name + "' has been removed",
            #     reverse("list_item_requests"),
            # )
            # delete the item request
            item_request.delete()



