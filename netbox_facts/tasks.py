from netbox_facts import models
import django_rq
from pprint import pprint


def arp(*args, **kwargs):
    print("it works")
    pprint(args)
    pprint(kwargs)
