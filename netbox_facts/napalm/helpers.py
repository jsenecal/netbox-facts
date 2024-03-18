import ipaddress
from typing import Optional


def ip_object(
    addr: str, version: Optional[int] = None
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """
    Converts a raw string to a valid IP address object. Optional version argument will detect that \
    object matches specified version.

    Motivation: the groups of the IP addreses may contain leading zeros. IPv6 addresses can \
    contain sometimes uppercase characters. E.g.: 2001:0dB8:85a3:0000:0000:8A2e:0370:7334 has \
    the same logical value as 2001:db8:85a3::8a2e:370:7334. However, their values as strings are \
    not the same.

    :param raw: the raw string containing the value of the IP Address
    :param version: insist on a specific IP address version.
    :type version: int, optional.
    :return: an ipaddress.IPv4Address or ipaddress.IPv6Address object)

    Example:

    .. code-block:: python

        >>> ip('2001:0dB8:85a3:0000:0000:8A2e:0370:7334')
        u'2001:db8:85a3::8a2e:370:7334'
    """
    # scope = ""
    if "%" in addr:
        addr = addr.split("%", 1)[0]
    addr_obj = ipaddress.ip_address(addr)
    if version and addr_obj.version != version:
        raise ValueError(f"{addr} is not an ipv{version} address")
    return addr_obj
