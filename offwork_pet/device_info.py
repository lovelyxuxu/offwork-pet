"""Local device hints for the private administrator console; no network probes."""
import getpass
import ipaddress
import platform

from PySide6.QtNetwork import QNetworkInterface

from . import __version__


def collect_device_info():
    def safe_text(callback, limit):
        try:
            return ''.join(c for c in callback() if ord(c) >= 32).strip()[:limit]
        except (OSError, RuntimeError, KeyError):
            return ''

    addresses = set()
    for item in QNetworkInterface.allAddresses():
        try:
            address = ipaddress.ip_address(item.toString().split('%')[0])
        except ValueError:
            continue
        if (address.is_private and not address.is_loopback and not address.is_link_local
                and not address.is_unspecified and not address.is_multicast):
            addresses.add(str(address))
    return {
        'computer_name': safe_text(platform.node, 128),
        'system_user': safe_text(getpass.getuser, 128),
        'lan_ips': sorted(addresses)[:16],
        'os_version': safe_text(platform.platform, 160),
        'app_version': __version__[:64],
    }
