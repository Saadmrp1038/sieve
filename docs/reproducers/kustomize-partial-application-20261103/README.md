This bundle reproduces the staged-application inventory scenario independently
of Sieve. It preserves the three exact source archives from the sealed native
campaign. The new standalone orchestration has received static validation only;
it has not been executed. The previous campaign ran one passing vanilla control
and reproduced orphaning in two fresh vanilla cases. See `observed-results.json`.

Use a disposable cluster with unmodified kustomize-controller v1.10.1 already
running as `Deployment/default/kustomize-controller`, container `manager`, with
native `--concurrent=10`. Install its Kustomization CRD and the source-controller
v1.10.0 GitRepository, OCIRepository, Bucket and ExternalArtifact CRDs. The native
controller watches these APIs even when only GitRepository is used. The tested
environment used Kubernetes v1.36.4. It supports ValidatingAdmissionPolicy v1 and
allows the controller to manage synthetic Namespaces and ConfigMaps.

Keep source-controller absent for this fixture: the script supplies synthetic
GitRepository artifact status pointing at an in-cluster HTTP service. A running
source-controller could overwrite that status with a failure to fetch the
deliberately invalid Git URL. No Git server, credentials, git push, image build,
Flux CLI or Sieve installation is needed. Python 3.8+ and kubectl are required;
BusyBox 1.37.0 must be available to the cluster. No cluster is created or reset by
the script, and controller configuration is never patched.

For a future explicitly authorized execution, choose the disposable kubecontext
and a fresh output directory:

```sh
python3 repro.py --context DISPOSABLE_CONTEXT --output /tmp/partial-apply-case-1
```

The control uses the same script, source bytes, resources and policy. Its binding
uses `Audit` rather than `Deny`. Use a separate fresh disposable cluster, or
remove the prior synthetic resources before running it:

```sh
python3 repro.py --context DISPOSABLE_CONTROL_CONTEXT \
  --output /tmp/partial-apply-control-1 --control
```

These commands have not been run as part of preparing this bundle. The previous
execution budget is exhausted; this bundle does not authorize more executions.

The script performs this sequence:

1. Verify archive hashes, ten workers, and unused synthetic names.
2. Serve `artifacts/*.tgz` using a ConfigMap, Service and BusyBox HTTP Deployment.
3. Publish revision 1 and reconcile an inventoried anchor ConfigMap.
4. Install a policy matching CREATE/UPDATE only of
   `ConfigMap/default/sieve-partial-blocked`. Independently check admission using
   a server-side dry-run. Publish revision 2, which adds a Namespace and that
   ConfigMap. The first native stage creates the Namespace; the later stage's
   ConfigMap dry-run is rejected in the failing case.
5. Publish revision 3, containing only the anchor. Wait for Ready at that
   revision before removing the admission policy. This avoids repairing inventory
   through an intervening successful retry of revision 2.
6. Request and observe three more acknowledged revision-3 reconciliations.
7. Delete the parent with `prune: true`, `deletionPolicy: Delete`, and suspension
   disabled. Observe parent absence for at least five seconds, then inspect the
   original Namespace UID and deletion timestamp.

Expected corrected behavior: successfully applied early-stage resources remain
tracked and are deleted when removed from the source or when their parent is
finalized. Previously observed v1.10.1 behavior: the rejected case omits the
Namespace from inventory and leaves it Active with the same UID and no deletion
timestamp after both cleanup opportunities. The tracked anchor is removed.

`result.json` records command arguments, payloads, API responses, observations and
controller logs. Exit code 42 means the full orphaning predicate was observed;
0 means all three tracking/cleanup checks passed; another nonzero exit means an
unexpected outcome or setup failure requiring examination. No automatic cleanup
or retry occurs, so an error preserves the available evidence. A partial API
snapshot during a normal reconciliation is not sufficient to establish the bug.

The bundle contains human-readable source revisions in `revisions/`. The
`objects.json` resource definitions come from the accepted inputs of the sealed
case, with the source ConfigMap's binary data supplied from the bundled archives.
`SHA256SUMS` covers every file in this bundle. From this directory, verify it
without executing the reproducer:

```sh
shasum -a 256 -c SHA256SUMS
```

After collecting evidence from any future authorized run, these explicit
commands remove only the fixed synthetic resources. Use the same disposable
context as that run; the Namespace can contain resources only if that cluster
was modified separately:

```sh
kubectl --context DISPOSABLE_CONTEXT -n default delete kustomization sieve-partial --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT delete validatingadmissionpolicybinding sieve-partial-deny --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT delete validatingadmissionpolicy sieve-partial-deny --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT -n default delete gitrepository sieve-partial --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT -n default delete deployment,service,configmap sieve-partial-source --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT -n default delete configmap sieve-partial-anchor sieve-partial-blocked --ignore-not-found
kubectl --context DISPOSABLE_CONTEXT delete namespace sieve-partial-created --ignore-not-found
```

The failure mechanism has upstream prior art in
[PR #426's inventory discussion](https://github.com/fluxcd/kustomize-controller/pull/426#issuecomment-928271551)
and [fluxcd/pkg #956](https://github.com/fluxcd/pkg/issues/956#issuecomment-3755147511).
This bundle supplies a concrete counterexample to cleanup after changing the
source to exclude the partially created resource. It is not presented as a
previously unknown failure mechanism.
