from validators import login_required, validate
from .repository import get_users


@login_required
def authenticate(user):
    return validate(user)


def list_users(cursor):
    return get_users(cursor)
