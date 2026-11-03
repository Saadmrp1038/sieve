package main

import (
	"context"
	"net"
	"net/rpc"
	"os"
	sieve "sieve.client"
	"testing"
	"time"
)

func TestDeliveryOverlapCoordinatorCancellationUnblocksHeldRPC(t *testing.T) {
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile("server.yaml", []byte(`workload: metadata-labels-overlap
actions: []
coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	coordinator, err := NewTestCoordinator(ctx)
	if err != nil {
		t.Fatal(err)
	}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", coordinator); err != nil {
		t.Fatal(err)
	}
	a, b := net.Pipe()
	defer b.Close()
	go server.ServeConn(b)
	client := rpc.NewClient(a)
	defer client.Close()
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "call", InstanceID: "receiver"}
	var response sieve.Response
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &begin, &response); err != nil {
		t.Fatal(err)
	}
	read := begin
	read.Kind = "read"
	read.ResourceKey = "externalsecret/default/a"
	read.Object = `{"metadata":{"name":"a","namespace":"default","uid":"parent","generation":2,"resourceVersion":"20","labels":{"sieve.testing/state":"change"}},"spec":{"value":"two"}}`
	held := client.Go("TestCoordinator.NotifyTestCoverage", &read, new(sieve.Response), make(chan *rpc.Call, 1))
	var status overlapReply
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"held", 1000}, &status); err != nil {
		t.Fatal(err)
	}
	cancel()
	select {
	case result := <-held.Done:
		if result.Error == nil {
			t.Fatal("cancelled hold reported success")
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled coordinator stranded a worker")
	}
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"completed", 100}, &status); err == nil {
		t.Fatal("cancelled schedule reported completed")
	}
}
