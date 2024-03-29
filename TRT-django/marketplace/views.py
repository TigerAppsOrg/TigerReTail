from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib import messages
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import Window, F, prefetch_related_objects, Q
from django.db.models.functions import RowNumber
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from .models import (
    Account,
    Item,
    Transaction,
    ItemLog,
    TransactionLog,
    AlbumImage,
    ItemRequest,
    ItemRequestLog,
    Message,
    Notification,
    Category,
    ItemFlag,
    ItemRequestFlag,
)
from .forms import AccountForm, ItemForm, ItemRequestForm, ItemFlagForm, ItemRequestFlagForm
from utils import CASClient
from datetime import timedelta

from django.core.exceptions import PermissionDenied
import secrets
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
import json

from sys import stderr

from background_task import background
from datetime import date, datetime

from io import BytesIO
from PIL import Image

from urllib.parse import quote_plus, unquote_plus

# ----------------------------------------------------------------------

# helper method to log an error to server stderr


def logError(account, exception):
    print(
        "[" + str(timezone.now()) + "] " + account.username + ": " + str(exception),
        file=stderr,
    )


# ----------------------------------------------------------------------

# helper method to log an item action


def logItemAction(item, account, log):
    ItemLog(item=item, account=account, datetime=timezone.now(), log=log).save()


# ----------------------------------------------------------------------

# helper method to log a transaction action


def logTransactionAction(transaction, account, log):
    TransactionLog(
        transaction=transaction,
        account=account,
        datetime=timezone.now(),
        log=log,
    ).save()


# ----------------------------------------------------------------------

# helper method to log an item_request action


def logItemRequestAction(item_request, account, log):
    ItemRequestLog(
        item_request=item_request,
        account=account,
        datetime=timezone.now(),
        log=log,
    ).save()


# ----------------------------------------------------------------------

# helper method to send email about activity, conditionally on account settings


def send_mail_activity(subject, body, senderEmail, receiverAccounts, fail_silently):
    send_mail(
        subject,
        body + "\n\nYou can change your email notification settings here: https://retail.tigerapps.org/account/edit/",
        senderEmail,
        [receiverAccount.email for receiverAccount in receiverAccounts if receiverAccount.email_activity],
        fail_silently=fail_silently,
    )


# ----------------------------------------------------------------------

# helper method to send new notification email delayed and sparsely
# namely, if the notification is seen by the time to send, will not send


@background(schedule=300)
def notifyEmailSparsely(pk, email, url):
    notification = Notification.objects.get(pk=pk)
    if not notification.seen:
        send_mail(
            "Unread Notification(s) on Tiger ReTail",
            "You have new notification(s) waiting to be read.\n" + url + "\n\nYou can change your email notification settings here: https://retail.tigerapps.org/account/edit/",
            settings.EMAIL_NAME,
            [email],
            fail_silently=True,
        )


# ----------------------------------------------------------------------

# helper method to send notification


def notify(account, text, url, sparse=False, timeout=timedelta(minutes=5)):
    
    # if sparse and recent unseen notification with same text already exists, do nothing
    if sparse:
        if Notification.objects.filter(account=account, text=text, seen=False).exists():
            duplicates = Notification.objects.filter(
                account=account, text=text, seen=False
            )
            recent = duplicates.order_by("-datetime").first()
            if timezone.now() < recent.datetime + timeout:
                return

    # otherwise, should notify and schedule an email if first unseen notification
    should_email = not Notification.objects.filter(account=account, seen=False).exists()
    notification = Notification(
        account=account,
        datetime=timezone.now(),
        text=text,
        seen=False,
        url=url,
    )
    notification.save()

    # if the oldest unseen notification is the one just created,
    # then delay-sparse send an email (delayed to allow user to see notification and prevent the email)
    if should_email and account.email_unread_notification:
        notifyEmailSparsely(
            pk=notification.pk,
            email=account.email,
            url=url,
        )

# ----------------------------------------------------------------------

# notify and email a notice about item expiration being passed
@background
def expiredItemNotice(pk):
    # check if the item still exists
    if not Item.objects.filter(pk=pk).exists():
        return

    # check if the item has expired
    item = Item.objects.filter(pk=pk).first()
    if item.deadline >= timezone.now().date():
        return

    # send notices
    send_mail_activity(
        "Your Posted Item has Expired",
        "Your posted item '"
        + item.name
        + "' has expired.\nPlease edit your item deadline if you would like to prevent your item from being removed.\nItems are removed "
        + str(settings.EXPIRATION_BUFFER)
        + " after their deadlines.",
        settings.EMAIL_NAME,
        [item.seller],
        fail_silently=True,
    )
    notify(
        item.seller,
        "Your item '"
        + item.name
        + "' has expired. Please edit its deadline if you do not want it removed.",
        reverse("list_items"),
    )


# ----------------------------------------------------------------------
# notify and email a notice about item request expiration being passed
@background
def expiredItemRequestNotice(pk):
    # check if the item request still exists
    if not ItemRequest.objects.filter(pk=pk).exists():
        return

    # check if the item request has expired
    item_request = ItemRequest.objects.filter(pk=pk).first()
    if item_request.deadline >= timezone.now().date():
        return

    # send notices
    send_mail_activity(
        "Your Posted Item Request has Expired",
        "Your posted item request for '"
        + item_request.name
        + "' has expired.\nPlease edit your item request deadline if you would like to prevent your item request from being removed.\nItem requests are removed "
        + str(settings.EXPIRATION_BUFFER)
        + " after their deadlines.",
        settings.EMAIL_NAME,
        [item_request.requester],
        fail_silently=True,
    )
    notify(
        item_request.requester,
        "Your item request '"
        + item_request.name
        + "' has expired. Please edit its deadline if you do not want it removed.",
        reverse("list_item_requests"),
    )


# ----------------------------------------------------------------------

# custom authentication_required decorator for protected views

# if user can be authenticated, call view as normal
# otherwise, redirect to CAS login page

# will ensure that an account with associated username exists


def authentication_required(view_function):
    def wrapper(request, *args, **kwargs):

        # if username in session, can call view as normal
        if "username" in request.session:
            if not Account.objects.filter(username=request.session.get("username")).exists():
                return redirect("logout")
            return view_function(request, *args, **kwargs)

        # if request contains a ticket, try validating it
        if "ticket" in request.GET:
            cas_url = unquote_plus(request.GET["quotedcasurl"])
            netid = CASClient.validate(
                cas_url, request.build_absolute_uri(), request.GET["ticket"]
            ).strip()

            if netid is not None:
                # store authenticated username,
                # check that associated Account exists, else create one,
                # call view as normal
                netid = netid.lower()
                username = settings.CAS_USERNAME_PREFIXES[settings.CAS_URLS.index(cas_url)] + netid
                request.session["username"] = username
                if not Account.objects.filter(username=username).exists():
                    Account(
                        username=username,
                        name=netid,
                        email=netid + settings.CAS_EMAIL_DOMAINS[settings.CAS_URLS.index(cas_url)],
                    ).save()
                # SPECIAL CASE: if the username is an ADMIN_USERNAME
                if username in settings.ADMIN_USERNAMES:
                    # create all the alternate accounts
                    # and assign the first one as active
                    for suffix in settings.ALT_ACCOUNT_SUFFIXES:
                        if not Account.objects.filter(
                            username=username + suffix
                        ).exists():
                            Account(
                                username=username + suffix,
                                name=netid + suffix,
                                email=netid + settings.CAS_EMAIL_DOMAINS[settings.CAS_URLS.index(cas_url)],
                            ).save()
                    request.session["username"] = (
                        username + settings.ALT_ACCOUNT_SUFFIXES[0]
                    )

                return view_function(request, *args, **kwargs)

        # user could NOT be authenticated, so redirect to CAS login
        # before redirection, store POST details for use once redirected back
        if request.method == "POST":
            request.session[request.path] = request.POST
        return redirect("cas_selection", quote_plus(request.build_absolute_uri()))

    return wrapper


# ----------------------------------------------------------------------

# admin_required decorator for protected views

# ensures that user is logged in and also an admin


def admin_required(view_function):
    def wrapper(request, *args, **kwargs):

        # if username in session, check admin
        if "username" in request.session:
            if not Account.objects.filter(username=request.session.get("username")).exists():
                return redirect("logout")
            if request.session["username"] in [username + suffix for username in settings.ADMIN_USERNAMES for suffix in settings.ALT_ACCOUNT_SUFFIXES]:
                return view_function(request, *args, **kwargs)

            else:
                messages.warning(request, "Forbidden, need admin permission.")
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

        # if request contains a ticket, try validating it and call view if admin
        if "ticket" in request.GET:
            cas_url = unquote_plus(request.GET["quotedcasurl"])
            netid = CASClient.validate(
                cas_url, request.build_absolute_uri(), request.GET["ticket"]
            ).strip()

            if netid is not None:
                # store authenticated username,
                # check that associated Account exists, else create one,
                # call view as normal only if admin
                netid = netid.lower()
                username = settings.CAS_USERNAME_PREFIXES[settings.CAS_URLS.index(cas_url)] + netid
                request.session["username"] = username
                if not Account.objects.filter(username=username).exists():
                    Account(
                        username=username,
                        name=netid,
                        email=netid + settings.CAS_EMAIL_DOMAINS[settings.CAS_URLS.index(cas_url)],
                    ).save()
                # SPECIAL CASE: if the username is an ADMIN_USERNAME
                if username in settings.ADMIN_USERNAMES:
                    # create all the alternate accounts
                    # and assign the first one as active
                    for suffix in settings.ALT_ACCOUNT_SUFFIXES:
                        if not Account.objects.filter(
                            username=username + suffix
                        ).exists():
                            Account(
                                username=username + suffix,
                                name=netid + suffix,
                                email=netid + settings.CAS_EMAIL_DOMAINS[settings.CAS_URLS.index(cas_url)],
                            ).save()
                    request.session["username"] = (
                        username + settings.ALT_ACCOUNT_SUFFIXES[0]
                    )
                    return view_function(request, *args, **kwargs)
                # view forbidden if not an admin
                else:
                    messages.warning(request, "Forbidden, need admin permission.")
                    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

        # user could NOT be authenticated because ticket missing or invalid, so redirect to CAS login
        # before redirection, store POST details for use once redirected back
        if request.method == "POST":
            request.session[request.path] = request.POST
        return redirect("cas_selection", quote_plus(request.build_absolute_uri()))

    return wrapper


# ----------------------------------------------------------------------

# image gallery


def gallery(request):
    return render(request, "marketplace/gallery.html", {})

# ----------------------------------------------------------------------

# get AVAILABLE items for image gallery with the following relative GET options:
# [REQUIRED] count >= 1 (if n < count items fit the criteria, then only those n items returned)      
# [REQUIRED] direction (forward/backward)
# [REQUIRED] base_item_pk (if -1, then will collect items from beginning/end based on direction)
# [OPTIONAL] search_string (used to index the items by name and description prior to retrieval)
# [OPTIONAL] condition_indexes ("condition_index,condition_index,...")
# [OPTIONAL] category_pks ("category_pk,category_pk,...")

# if base_item_pk == -1 and no items yet exist, then returns empty list

# items are returned sorted in order specified

# returns:
# {
#    "items": [
#        {
#           "pk",
#           "name",
#           "deadline",
#           "price",
#           "negotiable",
#           "condition_index",
#           "description",
#           "image", (url)
#           "album", (list of urls)
#        },
#        {
#           "pk",
#           "name",
#           "deadline",
#           "price",
#           "negotiable",
#           "condition_index",
#           "description",
#           "image", (url)
#           "album", (list of urls)
#        },
#        ...
#    ]
# }


def getItemsRelative(request):
    try:
        count = int(request.GET['count'])
        direction = request.GET['direction']
        base_item_pk = int(request.GET['base_item_pk'])
    except:
        return HttpResponse(status=400)

    if count < 1 or base_item_pk < -1 or (base_item_pk != -1 and not Item.objects.filter(pk=base_item_pk).exists()) or direction not in ['forward', 'backward']:
        return HttpResponse(status=400)

    search_string = ""
    condition_indexes = []
    categories = []
    sort_type = ""

    if "search_string" in request.GET:
        search_string = request.GET["search_string"]

    if "condition_indexes" in request.GET:
        try:
            condition_indexes = [int(pk) for pk in request.GET["condition_indexes"].split(",") if pk]
        except:
            return HttpResponse(status=400)

    if "category_pks" in request.GET:
        try:
            categories = Category.objects.filter(pk__in=[int(pk) for pk in request.GET["category_pks"].split(",") if pk])
        except:
            return HttpResponse(status=400)

    # filter items that meet conditions and categories criteria
    items = Item.objects.filter(status=Item.AVAILABLE)

    if condition_indexes:
        items = items.filter(condition__in=condition_indexes)

    if categories:
        items = items.filter(categories__in=categories)
        items = Item.objects.filter(pk__in=items) # get rid of duplicate rows (can happen because of filtering on m2m categories table)

    # sort items by price or date if requested
    order_by = ""
    if "sort_type" in request.GET:
        sort_type = request.GET["sort_type"]

        # items = sortMyItems(sort_type, items)

        if sort_type == "price_hightolow":
            order_by = [F("price").asc(), F("pk").asc()]
        elif sort_type == "price_lowtohigh":
            order_by=[F("price").desc(), F("pk").asc()]
        elif sort_type == "date_oldtorec":
            order_by=[F("posted_date").desc(), F("pk").asc()]
        elif sort_type == "date_rectoold":
            order_by=[F("posted_date").asc(), F("pk").asc()]

    if order_by != "":
        items = items.annotate(
            row=Window(
                expression=RowNumber(),
                order_by=order_by, 
            )
        )
    
    # default sort
    else:
        # annotate items by search string rank
        items = items.annotate(rank=SearchRank(SearchVector("name", "description"), SearchQuery(search_string), cover_density=True))

        # annotate items by row number after sorting by search string rank (so no comparison issues with equal ranks)
        items = items.annotate(
            row=Window(
                expression=RowNumber(),
                order_by=[F("rank").asc(), F("pk").asc()], # also order by unique pk to make tie-breaks consistent
            )
        )

    # get the correct slice of items
    if base_item_pk == -1:
        items = items.order_by('row' if direction == 'forward' else '-row')[:count]
    else:
        # filter out items based on base_item
        sign = ">" if direction == "forward" else "<"
        order = "ASC" if direction == "forward" else "DESC"

        # django does not allow filtering after window function, so will use raw SQL
        sql, params = items.query.sql_with_params()
        items = Item.objects.raw("""
                SELECT * FROM ({}) AS items_with_rows
                WHERE row {} (
                    SELECT row FROM ({}) AS base_item_row
                    WHERE id = %s
                )
                ORDER BY row {}
                LIMIT %s
            """.format(sql, sign, sql, order), # must order results here, since cannot order RawQuerySet
            [*params, *params, base_item_pk, count],
        )

    prefetch_related_objects(items, "album") # only 1 query to get all album objects

    return JsonResponse(
        {
            "items": [
                {
                    "pk": item.pk,
                    "name": item.name,
                    "posted_date": item.posted_date.astimezone().strftime("%b. %-d, %Y, %-I:%M %p") + " ET",
                    "deadline": item.deadline.strftime("%b. %-d, %Y"),
                    "price": item.price,
                    "negotiable": item.negotiable,
                    "condition_index": item.condition,
                    "description": item.description,
                    "image": item.image.url,
                    "album": [albumimage.image.url for albumimage in item.album.all()],
                    "contact": item.seller.contact,
                    "email": item.seller.email,
                } for item in items
            ]
        }
    )


# ----------------------------------------------------------------------

# personal items page


@authentication_required
def listItems(request):
    account = Account.objects.get(username=request.session.get("username"))
    items = account.item_set.all()
    context = {"items": items}
    return render(request, "marketplace/list_items.html", context)


# ----------------------------------------------------------------------

# new item form
# GET requests get a blank form
# POST requests get a form with error feedback, else new item created
# and redirected to list items page


@authentication_required
def newItem(request):
    account = Account.objects.get(username=request.session.get("username"))

    # populate the Django model form and validate data
    if request.method == "POST":
        item_form = ItemForm(request.POST, request.FILES)
        if item_form.is_valid():
            # create new item, but do not save yet until changes made
            item = item_form.save(commit=False)
            item.seller = account
            item.posted_date = timezone.now()
            item.status = Item.AVAILABLE
            if item.image.size > settings.MAX_IMAGE_SIZE:
                messages.error(
                    request,
                    "Could not save lead image, since it is > 10MB."
                )
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

            # resize lead image
            image_pil = Image.open(item.image)
            if image_pil.mode != "RGB":
                image_pil = image_pil.convert("RGB")
            image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

            image_io = BytesIO()
            image_pil.save(image_io, format='JPEG')
            image_file = InMemoryUploadedFile(image_io, None, item.name + '_lead.jpg', 'image/jpeg', None, None)
            item.image = image_file

            try:
                item.save()
                # save the m2m fields, which did not yet bc of commit=False
                item_form.save_m2m()
                logItemAction(item, account, "created")

                # create linked album images from uploaded files
                album = request.FILES.getlist("album")
                for i in range(len(album)):
                    if i >= settings.ALBUM_LIMIT:
                        break
                    try:
                        if album[i].size > settings.MAX_IMAGE_SIZE:
                            messages.error(
                                request,
                                "Could not save album image, since it is > 10MB."
                            )
                            return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
                        
                        image_pil = Image.open(album[i])
                        if image_pil.mode != "RGB":
                            image_pil = image_pil.convert("RGB")
                        image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

                        image_io = BytesIO()
                        image_pil.save(image_io, format='JPEG')
                        image_file = InMemoryUploadedFile(image_io, None, item.name + '_' + str(i) + '.jpg', 'image/jpeg', None, None)
                        AlbumImage(image=image_file, item=item).save()
                    except Exception as e:
                        messages.error(
                            request,
                            "Could not save album image. Check that your album images are each < 10MB and proper image files.",
                        )
                        logError(account, e)

                messages.success(request, "New item posted.")
                # send confirmation email
                send_mail_activity(
                    "Item Posted",
                    "You have posted your new item '" + item.name + "' for sale!\n"
                    + request.build_absolute_uri(reverse("list_items")),
                    settings.EMAIL_NAME,
                    [account],
                    fail_silently=True,
                )
                # schedule expiration notice
                expiredItemNotice(
                    item.pk,
                    schedule=timezone.make_aware(
                        datetime(
                            item.deadline.year, item.deadline.month, item.deadline.day
                        )
                    )
                    + timedelta(days=1),
                )

                return redirect("list_items")
            except Exception as e:
                messages.error(
                    request,
                    "Could not post item. Check that your lead image is < 10MB and a proper image file.",
                )
                logError(account, e)

    # did not receive form data via POST, so send a blank form
    else:
        item_form = ItemForm()

    context = {"item_form": item_form}
    return render(request, "marketplace/new_item.html", context)


# ----------------------------------------------------------------------

# edit item form
# GET requests given pre-populated item form
# POST requests given form with error feedback, else item edited


@authentication_required
def editItem(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    # if the item does not belong to this account, permission denied
    item = Item.objects.get(pk=pk)
    if item.seller != account:
        raise PermissionDenied

    # if item is frozen or complete, do not allow editing
    if item.status != item.AVAILABLE:
        messages.warning(request, "Cannot edit an item in the unavailable state.")
        return redirect("list_items")

    # populate the Django model form and validate data
    if request.method == "POST":
        old_image_name = item.image.name
        old_deadline = item.deadline
        item_form = ItemForm(request.POST, request.FILES, instance=item)
        if item_form.is_valid():
            try:
                if item.image.size > settings.MAX_IMAGE_SIZE:
                    messages.error(
                        request,
                        "Could not save lead image, since it is > 10MB."
                    )
                    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

                if item.image.name != old_image_name:
                    # delete old image
                    default_storage.delete(old_image_name)

                    # resize lead image
                    image_pil = Image.open(item.image)
                    if image_pil.mode != "RGB":
                        image_pil = image_pil.convert("RGB")
                    image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

                    image_io = BytesIO()
                    image_pil.save(image_io, format='JPEG')
                    image_file = InMemoryUploadedFile(image_io, None, item.name + '_lead.jpg', 'image/jpeg', None, None)
                    item.image = image_file

                # save changes to item
                item_form.save()

                logItemAction(item, account, "edited")

                if "replace" in request.POST:
                    # delete old album images
                    item.album.all().delete()

                if request.FILES.getlist("album"):
                    # insert album images (up to the count limit)
                    num_already = len(item.album.all())
                    album = request.FILES.getlist("album")
                    for i in range(len(album)):
                        if i + num_already >= settings.ALBUM_LIMIT:
                            break
                        try:
                            if album[i].size > settings.MAX_IMAGE_SIZE:
                                messages.error(
                                    request,
                                    "Could not save album image, since it is > 10MB."
                                )
                                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
                            
                            image_pil = Image.open(album[i])
                            if image_pil.mode != "RGB":
                                image_pil = image_pil.convert("RGB")
                            image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

                            image_io = BytesIO()
                            image_pil.save(image_io, format='JPEG')
                            image_file = InMemoryUploadedFile(image_io, None, item.name + '_' + str(i+num_already) + '.jpg', 'image/jpeg', None, None)
                            AlbumImage(image=image_file, item=item).save()
                        except Exception as e:
                            messages.error(
                                request,
                                "Could not save album image. Check that your album images are each < 10MB and proper image files.",
                            )
                            logError(account, e)

                messages.success(request, "Item updated.")

                if item.deadline != old_deadline:
                    # schedule expiration notice
                    expiredItemNotice(
                        item.pk,
                        schedule=timezone.make_aware(
                            datetime(
                                item.deadline.year,
                                item.deadline.month,
                                item.deadline.day,
                            )
                        )
                        + timedelta(days=1),
                    )
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

            except Exception as e:
                messages.error(
                    request,
                    "Could not edit item. Check that your lead image is < 10MB and a proper image file.",
                )
                logError(account, e)
                return redirect("edit_item", pk)

    # did not receive form data via POST, so send stored item form
    else:
        item_form = ItemForm(instance=item)
    context = {"item": item, "item_form": item_form}
    return render(request, "marketplace/edit_item.html", context)


# ----------------------------------------------------------------------

# delete item


@authentication_required
def deleteItem(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    # if the item does not belong to this account, permission denied
    item = Item.objects.get(pk=pk)
    if item.seller != account:
        raise PermissionDenied

    # if item is frozen or complete, do not allow deleting
    if item.status != item.AVAILABLE:
        messages.warning(request, "Cannot delete an item in the unavailable state.")
        return redirect("list_items")

    item_name = item.name
    item.delete()
    messages.success(request, "Item deleted.")
    # send confirmation email
    send_mail_activity(
        "Item Deleted",
        "You have removed your item '" + item_name + "' from sale.\n"
        + request.build_absolute_uri((reverse("list_items"))),
        settings.EMAIL_NAME,
        [account],
        fail_silently=True,
    )
    return redirect("list_items")


# ----------------------------------------------------------------------

# item page


def pageItem(request, pk):
    try:
        item = Item.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)

    context = {"item": item}
    return render(request, "marketplace/page_item.html", context)


# ----------------------------------------------------------------------

# personal purchases page


@authentication_required
def listPurchases(request):
    account = Account.objects.get(username=request.session.get("username"))

    # get purchases for which user is the buyer
    purchases = Transaction.objects.filter(buyer=account)
    context = {"purchases": purchases}
    return render(request, "marketplace/list_purchases.html", context)


# ----------------------------------------------------------------------

# buyer makes new purchase
# POST request with pk of item to purchase will create new transaction


@authentication_required
def newPurchase(request):
    account = Account.objects.get(username=request.session.get("username"))

    # check for possible GET instead of POST, caused by CAS redirect for login upon trying to purchase an item
    if request.method == "GET":
        if request.path in request.session:
            request.POST = request.session.pop(request.path)
        else:
            # GET request with no prev POST data in session is an error
            return HttpResponse(status=400)

    # item must be available for a transaction to associate with
    pk = request.POST["pk"]
    item = Item.objects.get(pk=pk)
    if item.status != Item.AVAILABLE:
        # rejected
        messages.warning(request, "Item unavailable for purchase.")
        return redirect("gallery")

    # buyer must not be the seller of this item
    if item.seller == account:
        # rejected
        messages.warning(request, "Cannot purchase an item you are selling.")
        return redirect("gallery")

    # freeze item
    item.status = Item.FROZEN
    item.save()
    logItemAction(item, account, "froze")

    purchase = Transaction(item=item, buyer=account, status=Transaction.INITIATED)
    purchase.save()
    logTransactionAction(purchase, account, "created")
    messages.success(request, "Purchase started!")

    # send confirmation email
    send_mail_activity(
        "Purchase Requested",
        "You have requested to purchase the item '" + item.name + "' from " + item.seller.name + ".\n"
        + request.build_absolute_uri(reverse("list_purchases")),
        settings.EMAIL_NAME,
        [account],
        fail_silently=True,
    )
    # send notification email to seller
    send_mail_activity(
        "Sale Requested by a Buyer",
        "Your item '" + item.name + "' has been requested for sale by " + account.name + "!\n"
        + request.build_absolute_uri(reverse("list_items")),
        settings.EMAIL_NAME,
        [item.seller],
        fail_silently=True,
    )
    # notify the seller
    notify(
        item.seller,
        account.name + " has requested to purchase '" + item.name + "'",
        request.build_absolute_uri(reverse("list_items")),
    )

    return redirect("list_purchases")


# ----------------------------------------------------------------------

# confirm purchase
# buyer confirms purchase


@authentication_required
def confirmPurchase(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    purchase = Transaction.objects.get(pk=pk)

    # must be the buyer of this transaction
    if purchase.buyer != account:
        # rejected
        raise PermissionDenied

    # transaction cannot be INITIATED, S_PENDING, COMPLETE, or CANCELLED
    if purchase.status == Transaction.INITIATED:
        messages.warning(request, "Cannot confirm - awaiting seller acknowledgement.")
    elif purchase.status == Transaction.S_PENDING:
        messages.warning(request, "Already confirmed - awaiting seller confirmation.")
    elif purchase.status == Transaction.COMPLETE:
        messages.warning(
            request, "Already confirmed - purchase has already been completed."
        )
    elif purchase.status == Transaction.CANCELLED:
        messages.warning(
            request, "Cannot confirm - purchase has already been cancelled."
        )
    # elif ACKNOWLEDGED, move to S_PENDING
    elif purchase.status == Transaction.ACKNOWLEDGED:
        purchase.status = Transaction.S_PENDING
        purchase.save()
        logTransactionAction(purchase, account, "confirmed")
        messages.success(request, "Purchase confirmed, awaiting seller confirmation.")
        # send confirmation emails
        send_mail_activity(
            "Purchase Confirmed",
            "You have confirmed your purchase of '" + purchase.item.name + "' from " + purchase.item.seller.name + ".\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Sale Awaiting Confirmation",
            "Your sale of '" + purchase.item.name + "' has been confirmed by " + account.name + " and awaits your confirmation.\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [purchase.item.seller],
            fail_silently=True,
        )
        # notify the seller
        notify(
            purchase.item.seller,
            account.name
            + " has confirmed the purchase of '"
            + purchase.item.name
            + "' and awaits your confirmation",
            request.build_absolute_uri(reverse("list_items")),
        )

    # elif B_PENDING, move to COMPLETE and move item to COMPLETE as well
    elif purchase.status == Transaction.B_PENDING:
        item = purchase.item
        item.status = Item.COMPLETE
        item.save()
        logItemAction(item, account, "completed")
        purchase.status = Transaction.COMPLETE
        purchase.save()
        logTransactionAction(purchase, account, "confirmed and completed")
        messages.success(request, "Purchase confirmed by both parties and completed.")
        # send confirmation emails
        send_mail_activity(
            "Purchase Completed",
            "Your purchase of '" + item.name + "' from " + item.seller.name + " has been completed.\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Sale Completed",
            "Your sale of '" + item.name + "' has been confirmed by " + account.name + " and is completed.\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [item.seller],
            fail_silently=True,
        )
        # notify the seller
        notify(
            item.seller,
            account.name + " has confirmed and completed the purchase of '" + item.name + "'",
            request.build_absolute_uri(reverse("list_items")),
        )

    return redirect("list_purchases")


# ----------------------------------------------------------------------

# cancel purchase
# buyer cancels purchase


@authentication_required
def cancelPurchase(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    purchase = Transaction.objects.get(pk=pk)

    # must be the buyer of this transaction
    if purchase.buyer != account:
        # rejected
        raise PermissionDenied

    # transaction cannot be COMPLETE or CANCELLED
    if purchase.status == Transaction.COMPLETE:
        messages.warning(
            request, "Cannot cancel a purchase which has already been completed."
        )
    elif purchase.status == Transaction.CANCELLED:
        messages.warning(request, "Already cancelled.")
    # move to CANCELLED and move item to AVAILABLE
    else:
        item = purchase.item
        item.status = Item.AVAILABLE
        item.save()
        logItemAction(item, account, "unfroze")
        purchase.status = Transaction.CANCELLED
        purchase.save()
        logTransactionAction(purchase, account, "cancelled")
        messages.success(request, "Purchase cancelled.")
        # send confirmation emails
        send_mail_activity(
            "Purchase Cancelled",
            "You have cancelled your purchase of '" + item.name + "' from " + item.seller.name + ".\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Sale Cancelled by Buyer",
            "Your sale of '" + item.name + "' has been cancelled by " + account.name + ".\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [item.seller],
            fail_silently=True,
        )
        # notify the seller
        notify(
            item.seller,
            account.name + " has cancelled the purchase of '" + item.name + "'",
            request.build_absolute_uri(reverse("list_items")),
        )

    return redirect("list_purchases")


# ----------------------------------------------------------------------

# seller accepts sale


@authentication_required
def acceptSale(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    sale = Transaction.objects.get(pk=pk)

    # must be the seller of this transaction
    if sale.item.seller != account:
        # rejected
        raise PermissionDenied

    # transaction must be INITIATED
    if sale.status == Transaction.INITIATED:
        sale.status = Transaction.ACKNOWLEDGED
        sale.save()
        logTransactionAction(sale, account, "acknowledged")
        messages.success(request, "Sale acknowledged.")
        # open contact between buyer and seller
        account.contacts.add(sale.buyer)  # (m2m goes both ways)
        # send confirmation email
        send_mail_activity(
            "Sale Accepted",
            "You have accepted a sale request for '" + sale.item.name + "' from " + sale.buyer.name + ".\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Purchase Request Accepted by Seller",
            "Your purchase request for '" + sale.item.name + "' has been accepted by " + account.name + ".\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [sale.buyer],
            fail_silently=True,
        )
        # notify the buyer
        notify(
            sale.buyer,
            account.name + " has accepted your purchase request for '" + sale.item.name + "'",
            request.build_absolute_uri(reverse("list_purchases")),
        )

    else:
        messages.warning(request, "Cannot acknowledge - sale not in INITIATED state.")

    return redirect("list_items")


# ----------------------------------------------------------------------

# seller confirms sale


@authentication_required
def confirmSale(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    sale = Transaction.objects.get(pk=pk)

    # must be the seller of this transaction
    if sale.item.seller != account:
        # rejected
        raise PermissionDenied

    # transaction cannot be INITIATED, B_PENDING, COMPLETE, or CANCELLED
    if sale.status == Transaction.INITIATED:
        messages.warning(request, "Cannot confirm - acknowledge the sale first.")
    elif sale.status == Transaction.B_PENDING:
        messages.warning(request, "Already confirmed - awaiting buyer confirmation.")
    elif sale.status == Transaction.COMPLETE:
        messages.warning(
            request, "Already confirmed - sale has already been completed."
        )
    elif sale.status == Transaction.CANCELLED:
        messages.warning(request, "Cannot confirm - sale has already been cancelled.")
    # elif ACKNOWLEDGED, move to B_PENDING
    elif sale.status == Transaction.ACKNOWLEDGED:
        sale.status = Transaction.B_PENDING
        sale.save()
        logTransactionAction(sale, account, "confirmed")
        messages.success(request, "Sale confirmed, awaiting buyer confirmation.")
        # send confirmation email
        send_mail_activity(
            "Sale Confirmed",
            "You have confirmed your sale of '" + sale.item.name + "' to " + sale.buyer.name + ".\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Purchase Awaiting Confirmation",
            "Your purchase of '" + sale.item.name + "' has been confirmed by " + account.name + " and awaits your confirmation\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [sale.buyer],
            fail_silently=True,
        )
        # notify the buyer
        notify(
            sale.buyer,
            account.name
            + " has confirmed your purchase of '"
            + sale.item.name
            + "' and awaits your confirmation",
            request.build_absolute_uri(reverse("list_purchases")),
        )

    # elif S_PENDING, move to COMPLETE and move item to COMPLETE as well
    elif sale.status == Transaction.S_PENDING:
        item = sale.item
        item.status = Item.COMPLETE
        item.save()
        logItemAction(item, account, "completed")
        sale.status = Transaction.COMPLETE
        sale.save()
        logTransactionAction(sale, account, "confirmed and completed")
        messages.success(request, "Sale confirmed by both parties and completed.")
        # send confirmation email
        send_mail_activity(
            "Sale Completed",
            "You have confirmed and completed your sale of '" + sale.item.name + "' to " + sale.buyer.name + ".\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Purchase Completed",
            "Your purchase of '" + sale.item.name + "' has been confirmed by " + account.name + " and is completed.\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [sale.buyer],
            fail_silently=True,
        )
        # notify the buyer
        notify(
            sale.buyer,
            account.name
            + " has confirmed and completed your purchase of '"
            + sale.item.name + "'",
            request.build_absolute_uri(reverse("list_purchases")),
        )

    return redirect("list_items")


# ----------------------------------------------------------------------

# seller cancels sale


@authentication_required
def cancelSale(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    sale = Transaction.objects.get(pk=pk)

    # must be the seller of this transaction
    if sale.item.seller != account:
        # rejected
        raise PermissionDenied

    # transaction cannot be COMPLETE or CANCELLED
    if sale.status == Transaction.COMPLETE:
        messages.warning(
            request, "Cannot cancel a sale which has already been completed."
        )
    elif sale.status == Transaction.CANCELLED:
        messages.warning(request, "Already cancelled.")
    # move to CANCELLED and move item to AVAILABLE
    else:
        item = sale.item
        item.status = Item.AVAILABLE
        item.save()
        logItemAction(item, account, "unfrozen")
        sale.status = Transaction.CANCELLED
        sale.save()
        logTransactionAction(sale, account, "cancelled")
        messages.success(request, "Sale cancelled.")
        # send confirmation email
        send_mail_activity(
            "Sale Cancelled",
            "You have cancelled your sale of '" + sale.item.name + "' to " + sale.buyer.name + ".\n"
            + request.build_absolute_uri(reverse("list_items")),
            settings.EMAIL_NAME,
            [account],
            fail_silently=True,
        )
        send_mail_activity(
            "Purchase Cancelled by Seller",
            "Your purchase of '" + sale.item.name + "' has been cancelled by " + account.name + ".\n"
            + request.build_absolute_uri(reverse("list_purchases")),
            settings.EMAIL_NAME,
            [sale.buyer],
            fail_silently=True,
        )
        # notify the buyer
        notify(
            sale.buyer,
            account.name + " has cancelled your purchase of '" + sale.item.name + "'",
            request.build_absolute_uri(reverse("list_purchases")),
        )

    return redirect("list_items")


# ----------------------------------------------------------------------

# personal item_requests page


@authentication_required
def listItemRequests(request):
    account = Account.objects.get(username=request.session.get("username"))
    item_requests = account.itemrequest_set.all()
    context = {"item_requests": item_requests}
    return render(request, "marketplace/list_item_requests.html", context)


# ----------------------------------------------------------------------

# new item_request form
# GET requests get a blank form
# POST requests get a form with error feedback, else new item_request created
# and redirected to list item_requests page


@authentication_required
def newItemRequest(request):
    account = Account.objects.get(username=request.session.get("username"))

    # populate the Django model form and validate data
    if request.method == "POST":
        item_request_form = ItemRequestForm(request.POST, request.FILES)
        if item_request_form.is_valid():
            # create new item_request, but do not save yet until changes made
            item_request = item_request_form.save(commit=False)
            item_request.requester = account
            item_request.posted_date = timezone.now()
            if item_request.image.size > settings.MAX_IMAGE_SIZE:
                messages.error(
                    request,
                    "Could not save lead image, since it is > 10MB."
                )
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

            # resize lead image
            image_pil = Image.open(item_request.image)
            if image_pil.mode != "RGB":
                image_pil = image_pil.convert("RGB")
            image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

            image_io = BytesIO()
            image_pil.save(image_io, format='JPEG')
            image_file = InMemoryUploadedFile(image_io, None, item_request.name + '_lead.jpg', 'image/jpeg', None, None)
            item_request.image = image_file

            try:
                item_request.save()
                # save the m2m fields, which did not yet bc of commit=False
                item_request_form.save_m2m()

                logItemRequestAction(item_request, account, "created")
                messages.success(request, "New item request posted.")
                # send confirmation email
                send_mail_activity(
                    "Item Request Posted",
                    "You have posted a new item request for '" + item_request.name + "'!\n"
                    + request.build_absolute_uri(reverse("list_item_requests")),
                    settings.EMAIL_NAME,
                    [account],
                    fail_silently=True,
                )
                # schedule expiration notice
                expiredItemRequestNotice(
                    item_request.pk,
                    schedule=timezone.make_aware(
                        datetime(
                            item_request.deadline.year,
                            item_request.deadline.month,
                            item_request.deadline.day,
                        )
                    )
                    + timedelta(days=1),
                )
                return redirect("list_item_requests")
            except Exception as e:
                messages.error(
                    request,
                    "Could not post item request. Check that your reference image is < 10MB and a proper image file.",
                )
                logError(account, e)

    # did not receive form data via POST, so send a blank form
    else:
        item_request_form = ItemRequestForm()

    context = {"item_request_form": item_request_form}
    return render(request, "marketplace/new_item_request.html", context)


# ----------------------------------------------------------------------

# edit item_request form
# GET requests given pre-populated item_request form
# POST requests given form with error feedback, else item_request edited


@authentication_required
def editItemRequest(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    # if the item_request does not belong to this account, permission denied
    item_request = ItemRequest.objects.get(pk=pk)
    if item_request.requester != account:
        raise PermissionDenied

    # populate the Django model form and validate data
    if request.method == "POST":
        old_image_name = item_request.image.name
        old_deadline = item_request.deadline
        item_request_form = ItemRequestForm(
            request.POST, request.FILES, instance=item_request
        )
        if item_request_form.is_valid():
            try:
                if item_request.image.size > settings.MAX_IMAGE_SIZE:
                    messages.error(
                        request,
                        "Could not save lead image, since it is > 10MB."
                    )
                    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))

                if item_request.image.name != old_image_name:
                    # delete old image
                    default_storage.delete(old_image_name)

                    # resize lead image
                    image_pil = Image.open(item_request.image)
                    if image_pil.mode != "RGB":
                        image_pil = image_pil.convert("RGB")
                    image_pil.thumbnail(settings.MAX_IMAGE_SHAPE)

                    image_io = BytesIO()
                    image_pil.save(image_io, format='JPEG')
                    image_file = InMemoryUploadedFile(image_io, None, item_request.name + '_lead.jpg', 'image/jpeg', None, None)
                    item_request.image = image_file

                # save changes to item_request
                item_request_form.save()

                logItemRequestAction(item_request, account, "edited")
                messages.success(request, "Item request updated.")
                if item_request.deadline != old_deadline:
                    # schedule expiration notice
                    expiredItemRequestNotice(
                        item_request.pk,
                        schedule=timezone.make_aware(
                            datetime(
                                item_request.deadline.year,
                                item_request.deadline.month,
                                item_request.deadline.day,
                            )
                        )
                        + timedelta(days=1),
                    )
                return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
            except Exception as e:
                messages.error(
                    request,
                    "Could not edit item request. Check that your reference image is < 10MB and a proper image file.",
                )
                logError(account, e)
                return redirect("edit_item_request", pk)

    # did not receive form data via POST, so send stored item_request form
    else:
        item_request_form = ItemRequestForm(instance=item_request)
    context = {"item_request": item_request, "item_request_form": item_request_form}
    return render(request, "marketplace/edit_item_request.html", context)


# ----------------------------------------------------------------------

# delete item_request


@authentication_required
def deleteItemRequest(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    # if the item_request does not belong to this account, permission denied
    item_request = ItemRequest.objects.get(pk=pk)
    if item_request.requester != account:
        raise PermissionDenied

    item_request_name = item_request.name
    item_request.delete()
    messages.success(request, "Item request deleted.")
    # send confirmation email
    send_mail_activity(
        "Item Request Deleted",
        "You have removed your item request for '" + item_request_name + "'.\n"
        + request.build_absolute_uri((reverse("list_item_requests"))),
        settings.EMAIL_NAME,
        [account],
        fail_silently=True,
    )
    return redirect("list_item_requests")


# ----------------------------------------------------------------------

# item_request page


def pageItemRequest(request, pk):
    try:
        item_request = ItemRequest.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)

    context = {"item_request": item_request}
    return render(request, "marketplace/page_item_request.html", context)


# ----------------------------------------------------------------------

# item request gallery


def browseItemRequests(request):
    return render(request, "marketplace/browse_item_requests.html", {})


# ----------------------------------------------------------------------

# get item requests for image gallery with the following relative GET options:
# [REQUIRED] count >= 1 (if n < count item requests fit the criteria, then only those n item requests returned)      
# [REQUIRED] direction (forward/backward)
# [REQUIRED] base_item_request_pk (if -1, then will collect item requests from beginning/end based on direction)
# [OPTIONAL] search_string (used to index the item requests by name and description prior to retrieval)
# [OPTIONAL] condition_indexes ("condition_index,condition_index,...")
# [OPTIONAL] category_pks ("category_pk,category_pk,...")

# if base_item_request_pk == -1 and no item requests yet exist, then returns empty list

# item requests are returned sorted in order specified

# returns:
# {
#    "item_requests": [
#        {
#           "pk",
#           "name",
#           "deadline",
#           "price",
#           "negotiable",
#           "condition_index",
#           "description",
#           "image", (url)
#        },
#        {
#           "pk",
#           "name",
#           "deadline",
#           "price",
#           "negotiable",
#           "condition_index",
#           "description",
#           "image", (url)
#        },
#        ...
#    ]
# }


def getItemRequestsRelative(request):
    try:
        count = int(request.GET['count'])
        direction = request.GET['direction']
        base_item_request_pk = int(request.GET['base_item_request_pk'])
    except:
        return HttpResponse(status=400)

    if count < 1 or base_item_request_pk < -1 or (base_item_request_pk != -1 and not ItemRequest.objects.filter(pk=base_item_request_pk).exists()) or direction not in ['forward', 'backward']:
        return HttpResponse(status=400)

    search_string = ""
    condition_indexes = []
    categories = []
    sort_type = ""

    if "search_string" in request.GET:
        search_string = request.GET["search_string"]

    if "condition_indexes" in request.GET:
        try:
            condition_indexes = [int(pk) for pk in request.GET["condition_indexes"].split(",") if pk]
        except:
            return HttpResponse(status=400)

    if "category_pks" in request.GET:
        try:
            categories = Category.objects.filter(pk__in=[int(pk) for pk in request.GET["category_pks"].split(",") if pk])
        except:
            return HttpResponse(status=400)

    # filter item requests that meet conditions and categories criteria
    item_requests = ItemRequest.objects.all()

    if condition_indexes:
        item_requests = item_requests.filter(condition__in=condition_indexes)

    if categories:
        item_requests = item_requests.filter(categories__in=categories)
        item_requests = ItemRequest.objects.filter(pk__in=item_requests) # get rid of duplicate rows (can happen because of filtering on m2m categories table)

    # sort items by price or date if requested
    order_by = ""
    if "sort_type" in request.GET:
        sort_type = request.GET["sort_type"]

        # items = sortMyItems(sort_type, items)

        if sort_type == "price_hightolow":
            order_by = [F("price").asc(), F("pk").asc()]
        elif sort_type == "price_lowtohigh":
            order_by=[F("price").desc(), F("pk").asc()]
        elif sort_type == "date_oldtorec":
            order_by=[F("posted_date").desc(), F("pk").asc()]
        elif sort_type == "date_rectoold":
            order_by=[F("posted_date").asc(), F("pk").asc()]

    if order_by != "":
        item_requests = item_requests.annotate(
            row=Window(
                expression=RowNumber(),
                order_by=order_by, 
            )
        )

    # default sort
    else:
        # annotate items by search string rank
        item_requests = item_requests.annotate(rank=SearchRank(SearchVector("name", "description"), SearchQuery(search_string), cover_density=True))

        # annotate items by row number after sorting by search string rank (so no comparison issues with equal ranks)
        item_requests = item_requests.annotate(
            row=Window(
                expression=RowNumber(),
                order_by=[F("rank").asc(), F("pk").asc()], # also order by unique pk to make tie-breaks consistent
            )
        )

    # get the correct slice of item requests
    if base_item_request_pk == -1:
        item_requests = item_requests.order_by('row' if direction == 'forward' else '-row')[:count]
    else:
        # filter out item requests based on base_item_request
        sign = ">" if direction == "forward" else "<"
        order = "ASC" if direction == "forward" else "DESC"

        # django does not allow filtering after window function, so will use raw SQL
        sql, params = item_requests.query.sql_with_params()
        item_requests = ItemRequest.objects.raw("""
                SELECT * FROM ({}) AS item_requests_with_rows
                WHERE row {} (
                    SELECT row FROM ({}) AS base_item_request_row
                    WHERE id = %s
                )
                ORDER BY row {}
                LIMIT %s
            """.format(sql, sign, sql, order), # must order results here, since cannot order RawQuerySet
            [*params, *params, base_item_request_pk, count],
        )

    return JsonResponse(
        {
            "item_requests": [
                {
                    "pk": item_request.pk,
                    "name": item_request.name,
                    "posted_date": item_request.posted_date.astimezone().strftime("%b. %-d, %Y, %-I:%M %p") + " ET",
                    "deadline": item_request.deadline.strftime("%b. %-d, %Y"),
                    "price": item_request.price,
                    "negotiable": item_request.negotiable,
                    "condition_index": item_request.condition,
                    "description": item_request.description,
                    "image": item_request.image.url,
                    "contact": item_request.requester.contact,
                    "email": item_request.requester.email,
                } for item_request in item_requests
            ]
        }
    )


# ----------------------------------------------------------------------

# notifications page


@authentication_required
def listNotifications(request):
    account = Account.objects.get(username=request.session.get("username"))
    notifications = account.notifications.all().order_by("-datetime")
    context = {"notifications": notifications}
    return render(request, "marketplace/list_notifications.html", context)


# ----------------------------------------------------------------------

# count unseen notifications


@authentication_required
def countNotifications(request):
    account = Account.objects.get(username=request.session.get("username"))
    count = account.notifications.filter(seen=False).count()
    return JsonResponse({"count": count})


# ----------------------------------------------------------------------

# sees notifications in GET param "notifications=pk,pk,..."
# otherwise, see all notifications


@authentication_required
def seeNotifications(request):
    account = Account.objects.get(username=request.session.get("username"))

    if "notifications" in request.GET:
        notification_pks = [int(pk) for pk in request.GET["notifications"].split(",") if pk]
        notifications = account.notifications.filter(pk__in=notification_pks)
    else:
        notifications = account.notifications.all()

    notifications.update(seen=True)
    return HttpResponse(status=200)


# ----------------------------------------------------------------------

# get all notifications


@authentication_required
def getNotifications(request):
    account = Account.objects.get(username=request.session.get("username"))
    notifications = account.notifications.all().order_by("-datetime")
    return JsonResponse(
        {
            "notifications": list(
                notifications.values_list("datetime", "text", "seen", "url")
            )
        }
    )


# ----------------------------------------------------------------------

# get notifications with the following relative GET options:
# count >= 1 (if n < count notifications fit the criteria, then only those n notifications returned)      
# direction (forward/backward)
# base_notification_pk (if -1, then will collect notifications from beginning/end based on direction)

# if base_notification_pk == -1 and no notifications yet exist, then returns empty list

# notifications are returned sorted in order specified

# returns:
# {
#    "notifications": [["pk", "datetime", "text", "seen", "url"], ["pk", "datetime", "text", "seen", "url"], ]
# }


@authentication_required
def getNotificationsRelative(request):
    account = Account.objects.get(username=request.session.get("username"))
    try:
        count = int(request.GET['count'])
        direction = request.GET['direction']
        base_notification_pk = int(request.GET['base_notification_pk'])
    except:
        return HttpResponse(status=400)

    if count < 1 or base_notification_pk < -1 or (base_notification_pk != -1 and not account.notifications.filter(pk=base_notification_pk).exists()) or direction not in ['forward', 'backward']:
        return HttpResponse(status=400)

    # annotate notifications by row number after sorting by datetime (so no comparison issues with equal datetimes)
    notifications = account.notifications.annotate(
        row=Window(
            expression=RowNumber(),
            order_by=[F("datetime").asc(), F("pk").asc()], # also order by unique pk to make tie-breaks consistent
        )
    )
    # retrieve the notifications to return
    if base_notification_pk == -1:
        notifications = notifications.order_by('row' if direction == 'forward' else '-row')[:count]
    else:
        # filter out notifications based on base_notification
        sign = ">" if direction == "forward" else "<"
        order = "ASC" if direction == "forward" else "DESC"

        # django does not allow filtering after window function, so will use raw SQL
        sql, params = notifications.query.sql_with_params()
        notifications = account.notifications.raw("""
                SELECT * FROM ({}) AS notifications_with_rows
                WHERE row {} (
                    SELECT row FROM ({}) AS base_notification_row
                    WHERE id = %s
                )
                ORDER BY row {}
                LIMIT %s
            """.format(sql, sign, sql, order), # must order results here, since cannot order RawQuerySet
            [*params, *params, base_notification_pk, count],
        )

    return JsonResponse(
        {
            "notifications": [
                [
                    notification.pk, 
                    notification.datetime, 
                    notification.text, 
                    notification.seen, 
                    notification.url
                ] for notification in notifications
            ]
        }
    )


# ----------------------------------------------------------------------

# get messages sent to and received from account pk


@authentication_required
def getMessages(request, pk):
    account = Account.objects.get(username=request.session.get("username"))
    try:
        contact = Account.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)
    sent = account.sent_messages.filter(receiver=contact).order_by("datetime")
    received = account.received_messages.filter(sender=contact).order_by("datetime")
    return JsonResponse(
        {
            "sent": list(sent.values_list("datetime", "text")),
            "received": list(received.values_list("datetime", "text")),
        }
    )


# ----------------------------------------------------------------------

# get messages sent to and received from account pk with the following relative GET options:
# count >= 1 (if n < count messages fit the criteria, then only those n messages returned)      
# direction (forward/backward)
# base_message_pk (if -1, then will collect messages from beginning/end based on direction)

# if base_message_pk == -1 and no messages yet exist, then returns empty list

# returns:
# {
#    "sent":     [["pk", "datetime", "text"], ["pk", "datetime", "text"], ]
#    "received": [["pk", "datetime", "text"], ["pk", "datetime", "text"], ]
#    "last_message_pk": pk or -1 if no messages returned
# }


@authentication_required
def getMessagesRelative(request, pk):
    account = Account.objects.get(username=request.session.get("username"))
    try:
        contact = Account.objects.get(pk=pk)
        count = int(request.GET['count'])
        direction = request.GET['direction']
        base_message_pk = int(request.GET['base_message_pk'])
    except:
        return HttpResponse(status=400)

    if count < 1 or base_message_pk < -1 or (base_message_pk != -1 and (not account.sent_messages.filter(pk=base_message_pk, receiver=contact).exists()) and (not account.received_messages.filter(pk=base_message_pk, sender=contact).exists())) or direction not in ['forward', 'backward']:
        return HttpResponse(status=400)

    # filter only messages between account and contact
    messages = Message.objects.filter((Q(sender=account) & Q(receiver=contact)) | (Q(sender=contact) & Q(receiver=account)))

    # annotate messages by row number after sorting by datetime (so no comparison issues with equal datetimes)
    messages = messages.annotate(
        row=Window(
            expression=RowNumber(),
            order_by=[F("datetime").asc(), F("pk").asc()], # also order by unique pk to make tie-breaks consistent
        )
    )

    # get the correct slice of messages
    if base_message_pk == -1:
        messages = messages.order_by('row' if direction == 'forward' else '-row')[:count]
    else:
        # filter out messages based on base_message
        sign = ">" if direction == "forward" else "<"
        order = "ASC" if direction == "forward" else "DESC"

        # django does not allow filtering after window function, so will use raw SQL
        sql, params = messages.query.sql_with_params()
        messages = Message.objects.raw("""
                SELECT * FROM ({}) AS messages_with_rows
                WHERE row {} (
                    SELECT row FROM ({}) AS base_message_row
                    WHERE id = %s
                )
                ORDER BY row {}
                LIMIT %s
            """.format(sql, sign, sql, order), # must order results here, since cannot order RawQuerySet
            [*params, *params, base_message_pk, count],
        )

    # separate into sent and received lists
    sent = []
    received = []
    last_message_pk = -1
    for message in messages:
        if message.sender == account:
            sent.append([message.pk, message.datetime, message.text])
        if message.receiver == account:
            received.append([message.pk, message.datetime, message.text])
        last_message_pk = message.pk
    return JsonResponse(
        {
            "sent": sent,
            "received": received,
            "last_message_pk": last_message_pk,
        }
    )


# ----------------------------------------------------------------------

# flag item form
# GET requests get a blank form
# POST requests get a form with error feedback, else new item flag created
# and redirected to gallery page


@authentication_required
def flagItem(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    try:
        item = Item.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)

    # populate the Django model form and validate data
    if request.method == "POST":
        item_flag_form = ItemFlagForm(request.POST)
        if item_flag_form.is_valid():
            # create new item flag, but do not save yet until changes made
            item_flag = item_flag_form.save(commit=False)
            item_flag.reporter = account
            item_flag.item = item
            item_flag.save()
            
            # email admin a report notice
            send_mail(
                "Item Flagged",
                "Item Flagged\n\nSeller: {}\nName: {}\nPrice: {}\nDescription: {}\n\nReporter: {}\nExplanation: {}\n{}".format(item.seller.username, item.name, item.price, item.description, account.username, item_flag.text, request.build_absolute_uri(reverse("admin_manage_flags"))),
                settings.EMAIL_NAME,
                settings.ADMIN_EMAILS,
                fail_silently=True,
            )

            messages.success(request, "Thank you for your help. Item reported to admin.")
            return redirect("gallery")
    # did not receive form data via POST, so send a blank form
    else:
        item_flag_form = ItemFlagForm()

    context = {"item_flag_form": item_flag_form, "item": item}
    return render(request, "marketplace/flag_item.html", context)


# ----------------------------------------------------------------------

# flag item request form
# GET requests get a blank form
# POST requests get a form with error feedback, else new item request flag created
# and redirected to browse_item_requests page


@authentication_required
def flagItemRequest(request, pk):
    account = Account.objects.get(username=request.session.get("username"))

    try:
        item_request = ItemRequest.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)

    # populate the Django model form and validate data
    if request.method == "POST":
        item_request_flag_form = ItemRequestFlagForm(request.POST)
        if item_request_flag_form.is_valid():
            # create new item_request flag, but do not save yet until changes made
            item_request_flag = item_request_flag_form.save(commit=False)
            item_request_flag.reporter = account
            item_request_flag.item_request = item_request
            item_request_flag.save()
            
            # email admin a report notice
            send_mail(
                "Item Request Flagged",
                "Item Request Flagged\n\nRequester: {}\nName: {}\nPrice: {}\nDescription: {}\n\nReporter: {}\nExplanation: {}\n{}".format(item_request.requester.username, item_request.name, item_request.price, item_request.description, account.username, item_request_flag.text, request.build_absolute_uri(reverse("admin_manage_flags"))),
                settings.EMAIL_NAME,
                settings.ADMIN_EMAILS,
                fail_silently=True,
            )

            messages.success(request, "Thank you for your help. Item request reported to admin.")
            return redirect("browse_item_requests")
    # did not receive form data via POST, so send a blank form
    else:
        item_request_flag_form = ItemRequestFlagForm()

    context = {"item_request_flag_form": item_request_flag_form, "item_request": item_request}
    return render(request, "marketplace/flag_item_request.html", context)


# ----------------------------------------------------------------------

# admin manage flags page


@admin_required
def adminManageFlags(request):
    item_flags = ItemFlag.objects.all()
    item_request_flags = ItemRequestFlag.objects.all()
    context = {"item_flags": item_flags, "item_request_flags": item_request_flags}
    return render(request, "marketplace/admin_manage_flags.html", context)


# ----------------------------------------------------------------------

# admin delete item flag


@admin_required
def adminDeleteItemFlag(request, pk):
    try:
        item_flag = ItemFlag.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)
    item_flag.delete()
    messages.success(request, "Item flag deleted.")
    return redirect('admin_manage_flags')


# ----------------------------------------------------------------------

# admin delete item request flag


@admin_required
def adminDeleteItemRequestFlag(request, pk):
    try:
        item_request_flag = ItemRequestFlag.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)
    item_request_flag.delete()
    messages.success(request, "Item request flag deleted.")
    return redirect('admin_manage_flags')


# ----------------------------------------------------------------------

# admin delete item


@admin_required
def adminDeleteItem(request, pk):
    try:
        item = Item.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)
    item.delete()
    messages.success(request, "Item deleted.")
    return redirect('admin_manage_flags')


# ----------------------------------------------------------------------

# admin delete item request


@admin_required
def adminDeleteItemRequest(request, pk):
    try:
        item_request = ItemRequest.objects.get(pk=pk)
    except:
        return HttpResponse(status=400)
    item_request.delete()
    messages.success(request, "Item request deleted.")
    return redirect('admin_manage_flags')


# ----------------------------------------------------------------------

# account activity page


@authentication_required
def accountActivity(request):
    account = Account.objects.get(username=request.session.get("username"))

    own_activity_item = account.item_logs.order_by("-datetime")
    own_activity_transaction = account.transaction_logs.order_by("-datetime")
    own_activity_item_request = account.item_request_logs.order_by("-datetime")

    item_activity = ItemLog.objects.filter(item__seller=account).order_by("-datetime")
    transaction_activity = TransactionLog.objects.filter(
        transaction__buyer=account
    ).order_by("-datetime")
    item_request_activity = ItemRequestLog.objects.filter(
        item_request__requester=account
    ).order_by("-datetime")

    context = {
        "own_activity_transaction": own_activity_transaction,
        "own_activity_item": own_activity_item,
        "own_activity_item_request": own_activity_item_request,
        "item_activity": item_activity,
        "transaction_activity": transaction_activity,
        "item_request_activity": item_request_activity,
    }
    return render(request, "marketplace/account_activity.html", context)


# ----------------------------------------------------------------------

# account settings page


@authentication_required
def editAccount(request):
    account = Account.objects.get(username=request.session.get("username"))
    old_email = account.email

    # populate the Django model form and validate data
    if request.method == "POST":
        account_form = AccountForm(request.POST, instance=account)
        if account_form.is_valid():
            new_email = account_form.cleaned_data["email"]
            account_form.save()
            # do not save new email yet
            if new_email != old_email:
                account = Account.objects.get(username=request.session.get("username"))
                account.email = old_email
                account.save()

                # store new_email and random token into cache
                # for email verification
                token = secrets.token_hex(32)
                cache.set(token, [account.username, new_email], 900)

                # send verification email
                send_mail(
                    "Tiger ReTail Email Verification",
                    "Please visit the following link to verify your email.\n"
                    + "If you did not make this request, you can safely ignore this message.\n"
                    + request.build_absolute_uri(reverse("verify_email", args=[token])),
                    settings.EMAIL_NAME,
                    [new_email],
                    fail_silently=True,
                )
                messages.info(request, "Verification email sent.")

            messages.success(request, "Account updated.")

    # did not receive form data via POST, so send stored account form
    else:
        account_form = AccountForm(instance=account)

    context = {"account_form": account_form}
    return render(request, "marketplace/edit_account.html", context)


# ----------------------------------------------------------------------

# set account.remind_set_email_settings to False


@authentication_required
def stopAccountEmailSettingsReminder(request):
    account = Account.objects.get(username=request.session.get("username"))
    account.remind_set_email_settings = False
    account.save()
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ----------------------------------------------------------------------


@authentication_required
def verifyEmail(request, token):
    account = Account.objects.get(username=request.session.get("username"))
    if cache.get(token):
        username, new_email = cache.get(token)
        if username != account.username:
            messages.warning(
                request,
                "Permission denied. Ensure you are logged in with the correct account.",
            )
        else:
            account.email = new_email
            account.save()
            messages.success(request, "Email verified.")
    else:
        messages.warning(request, "Verification link has expired.")
    return redirect("edit_account")


# ----------------------------------------------------------------------


@admin_required
def cycleAccount(request):
    username = request.session.get("username")

    usernames = settings.ADMIN_USERNAMES
    suffixes = settings.ALT_ACCOUNT_SUFFIXES

    # set the session username to the next username+suffix
    done = False
    for i in range(len(usernames)):
        if done:
            break
        for j in range(len(suffixes)):
            if username == usernames[i] + suffixes[j]:
                request.session["username"] = (
                    usernames[i] + suffixes[(j + 1) % len(suffixes)]
                )
                done = True
                break

    messages.success(request, "Cycled to alternate account.")
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))


# ----------------------------------------------------------------------

# faq page
def faq(request):
    return render(request, "marketplace/faq.html", {})


# ----------------------------------------------------------------------

# contact us page


def contact(request):
    return render(request, "marketplace/contact.html", {})


# ----------------------------------------------------------------------

# demo of dynamic items page


def demo(request):
    return render(request, "marketplace/demo.html", {})


# ----------------------------------------------------------------------

def logout(request):
    if not Account.objects.filter(username=request.session.get("username")).exists():
        request.session.pop("username", default=None)
        return redirect("gallery")
    account = Account.objects.get(username=request.session.get("username"))
    request.session.pop("username", default=None)
    cas_url = settings.CAS_URLS[settings.CAS_EMAIL_DOMAINS.index(account.email[account.email.index('@'):])]
    return redirect(
        CASClient.getLogoutUrl(cas_url, request.build_absolute_uri(reverse("gallery")))
    )


# ----------------------------------------------------------------------


def casSelection(request, quoted_url):
    return render(request, "marketplace/cas_selection.html", {"quoted_url": quoted_url})


# ----------------------------------------------------------------------


def casRedirect(request, quoted_cas_url, quoted_url):
    return redirect(CASClient.getLoginUrl(unquote_plus(quoted_cas_url), unquote_plus(quoted_url) + ("?" if unquote_plus(quoted_url).find('?') == -1 else "&") + "quotedcasurl=" + unquote_plus(quoted_cas_url)))


# ----------------------------------------------------------------------
# repeated server maintenance
# delete expired items/item_requests past timedelta specified in settings
# (and no ongoing transaction)
@background()
def deleteExpired():
    expired_items = Item.objects.filter(
        deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
    )
    for item in expired_items:
        if item.status == Item.AVAILABLE:
            # send email notice
            send_mail_activity(
                "Expired Item Removed",
                "Your expired item '"
                + item.name
                + "' has been removed.\nIf you would still like to sell your item, please feel free to relist it.",
                settings.EMAIL_NAME,
                [item.seller],
                fail_silently=True,
            )
            # notify the seller
            notify(
                item.seller,
                "Your expired item '" + item.name + "' has been removed",
                reverse("list_items"),
            )
            # delete the item
            item.delete()

    expired_item_requests = ItemRequest.objects.filter(
        deadline__lt=timezone.now() - settings.EXPIRATION_BUFFER
    )
    for item_request in expired_item_requests:
        # send email notice
        send_mail_activity(
            "Expired Item Request Removed",
            "Your expired item request '"
            + item_request.name
            + "' has been removed.\nIf you would still like to request the item, please feel free to make another request.",
            settings.EMAIL_NAME,
            [item_request.requester],
            fail_silently=True,
        )
        # notify the requester
        notify(
            item_request.requester,
            "Your expired item request '" + item_request.name + "' has been removed",
            reverse("list_item_requests"),
        )
        # delete the item request
        item_request.delete()


# ----------------------------------------------------------------------
# messaging system page
@authentication_required
def inbox(request):
    return render(request, "marketplace/inbox.html")
