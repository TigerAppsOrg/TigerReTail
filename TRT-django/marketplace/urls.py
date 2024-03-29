from django.urls import path
from . import views

urlpatterns = [
    path("", views.gallery, name="gallery"),
    path("items/get_relative/", views.getItemsRelative, name="get_items_relative"),
    path("items/list/", views.listItems, name="list_items"),
    path("items/new/", views.newItem, name="new_item"),
    path("items/<int:pk>/edit/", views.editItem, name="edit_item"),
    path("items/<int:pk>/delete/", views.deleteItem, name="delete_item"),
    path("items/<int:pk>/page/", views.pageItem, name="page_item"),
    path("purchases/list/", views.listPurchases, name="list_purchases"),
    path("purchases/new/", views.newPurchase, name="new_purchase"),
    path("purchases/<int:pk>/confirm/", views.confirmPurchase, name="confirm_purchase"),
    path("purchases/<int:pk>/cancel/", views.cancelPurchase, name="cancel_purchase"),
    path("sales/<int:pk>/accept/", views.acceptSale, name="accept_sale"),
    path("sales/<int:pk>/confirm/", views.confirmSale, name="confirm_sale"),
    path("sales/<int:pk>/cancel/", views.cancelSale, name="cancel_sale"),
    path("item_requests/list/", views.listItemRequests, name="list_item_requests"),
    path("item_requests/new/", views.newItemRequest, name="new_item_request"),
    path(
        "item_requests/<int:pk>/edit/",
        views.editItemRequest,
        name="edit_item_request",
    ),
    path(
        "item_requests/<int:pk>/delete/",
        views.deleteItemRequest,
        name="delete_item_request",
    ),
    path(
        "item_requests/<int:pk>/page/",
        views.pageItemRequest,
        name="page_item_request",
    ),
    path(
        "item_requests/browse/", views.browseItemRequests, name="browse_item_requests"
    ),
    path("item_requests/get_relative/", views.getItemRequestsRelative, name="get_item_requests_relative"),
    path("notifications/list/", views.listNotifications, name="list_notifications"),
    path("notifications/get/", views.getNotifications, name="get_notifications"),
    path("notifications/get_relative/", views.getNotificationsRelative, name="get_notifications_relative"),
    path("notifications/count/", views.countNotifications, name="count_notifications"),
    path("notifications/see/", views.seeNotifications, name="see_notifications"),
    path("account/activity/", views.accountActivity, name="account_activity"),
    path("account/edit/", views.editAccount, name="edit_account"),
    path("account/edit/stop_email_settings_reminder/", views.stopAccountEmailSettingsReminder, name="stop_email_settings_reminder"),
    path("account/login/", views.editAccount, name="login"),
    path("account/logout/", views.logout, name="logout"),
    path("account/email/verify/<str:token>/", views.verifyEmail, name="verify_email"),
    path("account/cycle/", views.cycleAccount, name="cycle_account"),
    path("flag_item/<int:pk>/", views.flagItem, name="flag_item"),
    path("flag_item_request/<int:pk>/", views.flagItemRequest, name="flag_item_request"),
    path("admin/flags/manage/", views.adminManageFlags, name="admin_manage_flags"),
    path("admin/flags/item/<int:pk>/delete/", views.adminDeleteItemFlag, name="admin_delete_item_flag"),
    path("admin/flags/item_request/<int:pk>/delete/", views.adminDeleteItemRequestFlag, name="admin_delete_item_request_flag"),
    path("admin/items/<int:pk>/delete/", views.adminDeleteItem, name="admin_delete_item"),
    path("admin/item_requests/<int:pk>/delete/", views.adminDeleteItemRequest, name="admin_delete_item_request"),
    path("faq/", views.faq, name="faq"),
    path("contact/", views.contact, name="contact"),
    path("demo/", views.demo, name="demo"),
    path("cas_selection/<str:quoted_url>/", views.casSelection, name="cas_selection"),
    path("cas_redirect/<str:quoted_cas_url>/<str:quoted_url>/", views.casRedirect, name="cas_redirect"),
    path("inbox/", views.inbox, name="inbox"),
]
