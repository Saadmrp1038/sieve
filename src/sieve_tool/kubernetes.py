"""Synchronous SDK calls with socket and wall-clock deadlines, without retries."""

import kubernetes
from sieve_tool.process import limit, request_timeout, wall_timeout


class BoundedAPI:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        value = getattr(self._client, name)
        if not callable(value):
            return value

        def call(*args, **kwargs):
            if kwargs.get("async_req"):
                raise ValueError("Bounded Kubernetes calls must be synchronous")
            kwargs.setdefault("_request_timeout", request_timeout())
            with wall_timeout("Kubernetes " + name, limit("request")):
                return value(*args, **kwargs)

        return call


def load_config():
    with wall_timeout("load Kubernetes configuration", limit("request")):
        kubernetes.config.load_kube_config()


def api(api_type):
    configuration = kubernetes.client.Configuration.get_default_copy()
    configuration.retries = 0
    return BoundedAPI(api_type(api_client=kubernetes.client.ApiClient(configuration)))
