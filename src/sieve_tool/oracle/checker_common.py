"""Shared resource classification and unchanged alarm text."""

from sieve_tool.events import parse_key


def kind_native_objects(key: str) -> bool:
    rtype, ns, name = parse_key(key)
    if rtype == "endpoints" and name == "kubernetes":
        return True
    elif rtype == "secret" and name.startswith("default-token-") and len(name) == 19:
        return True
    elif rtype == "serviceaccount" and name == "default":
        return True
    elif rtype == "service" and name == "kubernetes":
        return True
    elif rtype == "endpointslice" and name == "kubernetes":
        return True
    return False


def generate_alarm(sub_alarm: str, msg: str) -> str:
    return sub_alarm + " " + msg
