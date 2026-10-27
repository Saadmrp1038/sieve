# Secret Generator upstream check

Checked October 26, 2026. This investigation concerns recovery after an owned
Secret is deleted or changed while its StringSecret parent remains unchanged.

## Reports and fixes

Reviewed the titles and bodies of all 122 public issue/PR records returned by
the upstream issue API: 60 issues and 62 pull requests, open and closed. Also
searched issue/PR discussions for recreation, deletion, StringSecret,
regeneration, reconciliation and watches. No matching report or fix was found.
This does not establish that nobody has previously encountered the behavior.

[Issue #93, “Secrets out of sync”](https://github.com/mittwald/kubernetes-secret-generator/issues/93)
is different: its author closed it after discovering that the Secret was not
owned by the generator. Our workload establishes correct ownership before
changing the child. The existing
[owner-reference permissions issue #86](https://github.com/mittwald/kubernetes-secret-generator/issues/86)
is also distinct: it prevents initial creation in the affected environment.

## Versions

| Target | Resolved source | Relationship to our experiment |
| --- | --- | --- |
| Latest published GitHub release, [v3.4.1](https://github.com/mittwald/kubernetes-secret-generator/releases/tag/v3.4.1) | `1bf94b50ad1504926a5478d2618fc57dfb37b735` | Published August 28, 2025; the independent container reproduction targets this release. |
| Newest upstream tag, [v3.5.0](https://github.com/mittwald/kubernetes-secret-generator/tree/v3.5.0) | `f7e44605aa7980c305f0e600de1613e3c0e57efd` | Already the original experiment's exact operator pin; also the current `master` tip. |

The repository's chart at v3.5.0 still declares application version v3.4.1.
The configured public registry returned no manifest for its v3.5.0 image tag;
the v3.4.1 image was available. Release-page version and newest source tag must
therefore be reported separately.

The StringSecret controller watches only StringSecret changes in both
[v3.4.1](https://github.com/mittwald/kubernetes-secret-generator/blob/v3.4.1/pkg/controller/crd/stringsecret/stringsecret_controller.go)
and
[v3.5.0](https://github.com/mittwald/kubernetes-secret-generator/blob/v3.5.0/pkg/controller/crd/stringsecret/stringsecret_controller.go).
Its reconcile function already creates a missing Secret when invoked. The
latest change to this controller fixes a returned random-generation error,
not its watch registration. No newer child-watch repair was found.

## Independent release reproduction

The reproduction uses the unmodified public image
`quay.io/mittwald/kubernetes-secret-generator:v3.4.1`, pinned by registry manifest
digest `sha256:465b8e6d046208b1334024b31c6448a150669e5c38083a23eb59341c19ef511b`.
Its OCI configuration digest is
`sha256:1d4babfed148e83421831036966e06cb02985ef533754b789865188ad5f9cf5f`.

The operator runs on a separate, uniquely named kind cluster using the verified
vanilla Kubernetes 1.24.10 node image. The release's CRDs, RBAC and deployment
are retained with hashes. Deployment changes select the versioned image,
use `imagePullPolicy: Never` after loading that exact image, and remove a stale
`kubernetes-secret-generator` command override so the image's `/manager`
entrypoint runs. There is no Sieve
coordinator, controller instrumentation or injected fault.

Each case creates a separate StringSecret with an `initial` marker, a generated
16-character hexadecimal password, and `forceRegenerate: true`. Initial
convergence and ownership/status references must pass before testing. After
15 seconds of settling, either delete the owned Secret with its UID as a
precondition or replace its marker using a UID-guarded JSON patch.

Observe recovery for 120 seconds while requiring the parent's UID, generation
and spec to remain unchanged. Record the accepted mutation, resource samples,
operator readiness and restart counts. A separate diagnostic parent update
occurs only after this measurement, and never changes the original verdict.
It tests whether explicitly triggering the parent reconciler restores a
consistent Secret and status reference.

The independent run completed, and its saved evidence was checked separately:

| Mutation | Attempts | Result during the unchanged-parent window | Subsequent parent-update control |
| --- | --- | --- | --- |
| Delete the owned Secret | 2 | Secret absent throughout both 120-second observation windows | Both recreated a new Secret with correct ownership and status. |
| Replace the Secret's marker | 2 | Wrong marker persisted throughout both 120-second observation windows | Both restored consistency with the updated parent spec. |

All mutation acknowledgments identified the expected Secret UID. The parent's
UID, generation and spec stayed unchanged during every measurement. The
operator remained Ready with the same pod UID and container restart count.
No operator code or credentials were changed. The separate test cluster was
removed after collecting evidence.

This reproduces missing unchanged-parent recovery in the latest published
release, independently of Sieve's instrumentation and fault machinery. The
passing parent-update controls support the missing-trigger explanation:
reconciliation can restore the child when the parent change explicitly queues it.
The separate annotation-based Secret controller does not provide that trigger
for CR-created Secrets.

Local evidence is retained in `runs/secret-generator-release341-repro-vm/`:
`summary.json`, independently checked `reviewed.json`, the complete reproduction
script, original and deployed manifests, public issue/PR research, API command
logs, resource samples, operator logs and cleanup results. Two cached YAML
files gained an end-of-file newline; their parsed content is unchanged and the
normalization is recorded alongside the original Git blob identities.

Two earlier setup failures are retained separately. The first incorrectly
compared an image configuration digest with Docker's manifest-based image ID;
the corrected check verifies both identities and the pulled repository digest.
The second used the release's stale executable override. Neither attempt ran
a repair case, and neither is counted as an operator repair failure.

## Minimal deletion reproduction

On a cluster with this release deployed, create:

```yaml
apiVersion: secretgenerator.mittwald.de/v1alpha1
kind: StringSecret
metadata:
  name: repair-demo
spec:
  data:
    marker: initial
  fields:
    - fieldName: password
      encoding: hex
      length: "16"
  forceRegenerate: true
```

After initial Secret creation and status convergence:

```sh
kubectl delete secret repair-demo
kubectl get stringsecret repair-demo -o yaml
kubectl get secret repair-demo
```

Leave the parent unchanged and observe that the Secret is not recreated. Our
measurements sampled this state for 120 seconds, twice. After that measurement,
the following distinct diagnostic queues the parent and restores the Secret:

```sh
kubectl patch stringsecret repair-demo --type=merge \
  -p '{"spec":{"data":{"marker":"parent-triggered"}}}'
kubectl get secret repair-demo
```

## Interpretation

Failure to restore a declared child is a credible reconciliation-bug candidate.
A README that describes parent-triggered updates does not establish that
missing self-healing is intentional. Deletion and drift are potentially two
symptoms of one missing-trigger defect; do not count them as two distinct bugs.
The bounded observation establishes failure within its window, rather than
proving that recovery could never occur. Novelty and maintainer acceptance
remain separate from reproducing the behavior.
