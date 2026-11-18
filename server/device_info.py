"""Bounded client-reported metadata, exposed only by administrator APIs."""
import ipaddress

from .store import DomainError


def validate_device_info(value):
    if not isinstance(value, dict):
        raise DomainError('format', '设备信息格式不正确')
    cleaned = {}
    for key, limit in (('computer_name', 128), ('system_user', 128),
                       ('os_version', 160), ('app_version', 64)):
        item = value.get(key, '')
        if not isinstance(item, str) or len(item) > limit or any(ord(c) < 32 for c in item):
            raise DomainError('format', '设备信息字段不正确')
        cleaned[key] = item.strip()
    addresses = value.get('lan_ips', [])
    if not isinstance(addresses, list) or len(addresses) > 16:
        raise DomainError('format', '内网地址数量超限')
    cleaned['lan_ips'] = []
    for item in addresses:
        try:
            if not isinstance(item, str) or len(item) > 64 or '%' in item:
                raise ValueError()
            address = ipaddress.ip_address(item)
            if (not address.is_private or address.is_loopback or address.is_link_local
                    or address.is_unspecified or address.is_multicast):
                raise ValueError()
        except ValueError:
            raise DomainError('format', '内网地址格式不正确') from None
        if str(address) not in cleaned['lan_ips']:
            cleaned['lan_ips'].append(str(address))
    return cleaned
