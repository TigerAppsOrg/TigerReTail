# custom context processors to include common template context data
from .models import Account, Item, Transaction, Category
from django.conf import settings
from urllib.parse import quote_plus


def account(request):
    account = None
    if "username" in request.session:
        account = Account.objects.get(username=request.session.get("username"))
    return {"account": account}


def item(request):
    return {"Item": Item}


def transaction(request):
    return {"Transaction": Transaction}


def admin(request):
    return {
        "admin": "username" in request.session
        and request.session.get("username")
        in [
            netid + suffix
            for netid in settings.ADMIN_NETIDS
            for suffix in settings.ALT_ACCOUNT_SUFFIXES
        ]
    }

def categories(request):
    return {"categories": Category.objects.all()}

def quoted_cas_infos(request):
    return {"quoted_cas_infos": [(settings.CAS_NAMES[i], quote_plus(settings.CAS_URLS[i])) for i in range(len(settings.CAS_NAMES))]}