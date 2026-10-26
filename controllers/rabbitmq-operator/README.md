# RabbitMQ managed-child repair

The existing pinned operator remains at commit
`4f13b9a942ad34fece0171d2174aa0264b10e947`. Its historical lifecycle workloads
are unchanged.

Two additional workloads exercise the client-facing `rabbitmq-cluster` Service:

- `delete-child` deletes the Service and requires a replacement with a new UID.
- `drift-child` replaces its selector with a selector that matches no broker,
  then requires restoration of `app.kubernetes.io/name: rabbitmq-cluster`.

Both retain the original RabbitmqCluster UID, generation and spec, require a
controller owner reference to that parent, and wait for ready AMQP endpoints.
The pinned operator watches owned Services and sets the selector on reconcile.
The checks cover Kubernetes Service recovery; they do not test message delivery.

Invoke them through the normal Sieve workload command, for example:

```sh
uv run --locked sieve vanilla -c rabbitmq-operator -w delete-child -r YOUR_IMAGES
```

Each wait defaults to 300 seconds. Direct workload invocations accept
`--repair-timeout SECONDS`. The command saves `repair-observations.json` on both
success and failure, including the accepted mutation and observed parent,
Service and endpoints. Only successful repair writes `FINISH-SIEVE-TEST`.

Start with a no-fault run. Only a converged workload is eligible for two learned
baselines and fault testing. Use fresh result and oracle directories; report
unreachable schedules or incomplete injections separately from operator failures.
See the [milestone plan](../../docs/rfe/managed-child-repair/doc.md) for the
evaluation contract.
