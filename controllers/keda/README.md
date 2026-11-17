# KEDA

This profile pins KEDA v2.21.0 at commit
`626ded5d783108bedf647e20bbc83f8e458e02e4`. It instruments the native
`ScaledObjectReconciler.Reconcile` entry point and keeps the release metrics
server, admission webhook, CRDs, RBAC, and certificate rotation behavior.
Only the operator image is replaced with the corresponding Sieve build.

The operator runs with ten native ScaledObject reconciliation workers through
`KEDA_SCALEDOBJECT_CTRL_MAX_RECONCILES=10`. The other KEDA controller settings
keep their release defaults.

## Target restoration workloads

The workloads create two synthetic Deployments with different initial replica
counts. A credential-free `kubernetes-workload` trigger and an HPA fixed at two
replicas make the scale result deterministic.

`restore-control-a` and `restore-control-b` each target one Deployment from the
start. They establish that deletion normally restores that Deployment to its
own original replica count.

`target-handoff` first targets Deployment A, then changes
`spec.scaleTargetRef.name` to Deployment B. The workload records the accepted
API update, the generated HPA target, the ScaledObject status, both Deployment
replica counts, and the operator Pod identity. It then deletes the ScaledObject
and compares Deployment B with B's independently recorded initial replica
count.

Run the profile through the public Sieve CLI with fresh output paths:

```console
sieve build -c keda -m all -r LOCAL_REGISTRY
sieve vanilla -c keda -w restore-control-a -r LOCAL_REGISTRY --output FRESH_PATH
sieve vanilla -c keda -w restore-control-b -r LOCAL_REGISTRY --output FRESH_PATH
sieve vanilla -c keda -w target-handoff -r LOCAL_REGISTRY --output FRESH_PATH
```

Each completed workload writes `target-handoff.json`, command receipts, and the
standard Sieve result tree before writing `FINISH-SIEVE-TEST`.
