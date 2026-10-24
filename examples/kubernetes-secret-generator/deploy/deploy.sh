#!/bin/sh
set -eu
kubectl apply -f crds/
kubectl apply -f rbac.yaml
kubectl apply -f operator.yaml
