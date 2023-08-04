import requests
import rich
from django.core.management.base import BaseCommand
from netbox_facts import settings
from netbox_facts.models import MACVendor


class Command(BaseCommand):
    help = "Synchronize OUI database with IEEE"

    def __init__(self):
        self.prefixes = {}
        self.oui_source = settings.get("OUI_SOURCE", "http://standards-oui.ieee.org/oui.txt")
        self.sync_timeout = settings.get("OUI_SYNC_TIMEOUT", 5)
        super().__init__()

    def fetch_prefixes(self, verbosity: int):
        """Fetch the prefixes from the url defined in the settings,
        usually, the IEEE website."""
        request = requests.get(self.oui_source, timeout=self.sync_timeout)
        for line in request.text.splitlines():
            if not line:
                continue
            if "(base 16)" in line:
                if verbosity == 2:
                    rich.print(f"[blue]Parsing[/blue] {line}")
                prefix, vendor_name = (i.strip() for i in line.split("(base 16)", 1))
                self.prefixes[prefix] = vendor_name
        if verbosity == 2:
            rich.print(f"[green]Retrieved[/green] {len(self.prefixes)} MAC prefixes")

        return self.prefixes

    def handle(self, *args, **options):
        verbosity = int(options["verbosity"])
        self.fetch_prefixes(verbosity)
        parsed_prefixes = len(self.prefixes)
        created_prefixes = 0
        for prefix, vendor_name in self.prefixes.items():
            defaults = {"name": vendor_name}
            mac_vendor, created = MACVendor.objects.get_or_create(mac_prefix=prefix, defaults=defaults)
            if created:
                created_prefixes += 1
                if verbosity > 0:
                    rich.print(f"[green]Created[/green] {mac_vendor}")
            elif verbosity == 2:
                rich.print(f"[blue]Found[/blue] {mac_vendor}")
        if verbosity > 0:
            rich.print("\n")
            rich.print(f"[blue]Parsed[/blue] {parsed_prefixes} MAC vendors")
            rich.print(f"[green]Created[/green] {created_prefixes} MAC vendors")
