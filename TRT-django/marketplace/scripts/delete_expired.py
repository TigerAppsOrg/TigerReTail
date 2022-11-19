
from .models import Item
from django.utils import timezone
from django.conf import settings

def deleteExpired():
    expired_items = Item.objects.filter(
        deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
    )
    print("Delete expired task called")
    print(f"Number of expired items: {len(expired_items)}")
    # for item in expired_items:
    #     if item.status == Item.AVAILABLE:
    #         # send email notice
    #         send_mail_activity(
    #             "Expired Item Removed",
    #             "Your expired item '"
    #             + item.name
    #             + "' has been removed.\nIf you would still like to sell your item, please feel free to relist it.",
    #             settings.EMAIL_NAME,
    #             [item.seller],
    #             fail_silently=True,
    #         )
    #         # notify the seller
    #         notify(
    #             item.seller,
    #             "Your expired item '" + item.name + "' has been removed",
    #             reverse("list_items"),
    #         )
    #         # delete the item
    #         item.delete()

    # expired_item_requests = ItemRequest.objects.filter(
    #     deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
    # )
    # for item_request in expired_item_requests:
    #     # send email notice
    #     send_mail_activity(
    #         "Expired Item Request Removed",
    #         "Your expired item request '"
    #         + item_request.name
    #         + "' has been removed.\nIf you would still like to request the item, please feel free to make another request.",
    #         settings.EMAIL_NAME,
    #         [item_request.requester],
    #         fail_silently=True,
    #     )
    #     # notify the requester
    #     notify(
    #         item_request.requester,
    #         "Your expired item request '" + item_request.name + "' has been removed",
    #         reverse("list_item_requests"),
    #     )
    #     # delete the item request
    #     item_request.delete()

if __name__ == "__main__":
    deleteExpired()