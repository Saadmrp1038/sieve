"""Kubernetes List responses are valid Sieve REST-read observations."""

import json

from sieve_tool.trace import Marker, decode_line


def rest_list_record(payload):
    fields = [
        Marker.REST_READ,
        "0",
        "List",
        "example.Reconcile",
        "NoError",
        "configmap",
        "default",
        "",
        json.dumps(payload),
        "SIEVE-V2:example-invocation",
    ]
    return "fixture.go:1: " + "\t".join(fields) + "\n"


def test_rest_list_accepts_kubernetes_list_envelopes():
    empty = decode_line(
        rest_list_record({"metadata": {"resourceVersion": "1"}, "items": None}),
        "source.log",
        0,
    )
    assert empty.event.key_to_obj == {}
    assert empty.event.key_set == set()

    item = {"metadata": {"namespace": "default", "name": "one"}}
    populated = decode_line(
        rest_list_record({"metadata": {"resourceVersion": "2"}, "items": [item]}),
        "source.log",
        0,
    )
    assert populated.event.key_to_obj == {"configmap/default/one": item}
    assert populated.event.key_set == {"configmap/default/one"}
