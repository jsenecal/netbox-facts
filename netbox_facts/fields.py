from django.core.exceptions import ValidationError
from dcim.fields import mac_unix_expanded_uppercase, MACAddressField
from netaddr import AddrFormatError, EUI

__all__ = ["MACPrefixField"]


class MACPrefixField(MACAddressField):
    """PostgreSQL MAC Address Prefix field. Identical to MACAddressField except it does not store the last 3 bytes."""

    description = (
        "PostgreSQL MAC Address Prefix field. Identical to MACAddressField except it does not store the last 3 bytes."
    )

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            value = value.replace(" ", "")
        try:
            eui = EUI(value, version=48, dialect=mac_unix_expanded_uppercase)
            return EUI(int(eui) & ~0x0000FFFFFF, version=48)
        except AddrFormatError as exc:
            raise ValidationError(f"Invalid MAC address Prefix format: {value}") from exc
