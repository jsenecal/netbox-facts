import logging

import uuid
from django.utils import timezone
from django_rq import get_queue
from rq.registry import ScheduledJobRegistry

from dcim.choices import DeviceStatusChoices
from extras.choices import JobResultStatusChoices

logger = logging.getLogger(f"netbox_config_backup")

