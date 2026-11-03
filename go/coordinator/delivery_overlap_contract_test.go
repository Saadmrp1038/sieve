package main

import (
	"fmt"
	"net/rpc"
	sieve "sieve.client"
	"strings"
	"testing"
	"time"
)

type overlapQuery struct {
	WaitFor       string
	TimeoutMillis int
}
type overlapReply struct {
	Mode, State, ResourceKey, Reconciler, InvocationID, InstanceID, UID, ResourceVersion string
	Generation                                                                           int64
}

func TestDeliveryOverlapHoldsOneIdentifiedReadUntilFullNextInputDelivery(t *testing.T) {
	client, output := coverageContract(t, `coverageResources: [externalsecret/default/a, externalsecret/default/b]
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`)
	call := func(r sieve.NotifyTestCoverageRequest) sieve.Response {
		t.Helper()
		var out sieve.Response
		if err := client.Call("TestCoordinator.NotifyTestCoverage", &r, &out); err != nil || !out.Ok {
			t.Fatalf("coverage: %v %+v", err, out)
		}
		return out
	}
	query := func(wait string) overlapReply {
		t.Helper()
		var out overlapReply
		if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{wait, 2000}, &out); err != nil {
			t.Fatal(err)
		}
		return out
	}
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "selected", InstanceID: "receiver-a"}
	call(begin)
	read := begin
	read.Kind = "read"
	read.ResourceKey = "externalsecret/default/a"
	read.Object = `{"metadata":{"name":"a","namespace":"default","uid":"parent-a","generation":2,"resourceVersion":"20","labels":{"sieve.testing/state":"change"}},"spec":{"value":"two"}}`
	held := client.Go("TestCoordinator.NotifyTestCoverage", &read, new(sieve.Response), make(chan *rpc.Call, 1))
	state := query("held")
	if state.State != "held" || state.InvocationID != "selected" || state.UID != "parent-a" || state.Generation != 2 {
		t.Fatalf("wrong held read: %+v", state)
	}
	// All nine other workers can run while the selected invocation is held.
	done := make(chan error, 9)
	for i := 0; i < 9; i++ {
		go func(i int) {
			var response sieve.Response
			r := begin
			r.InvocationID = fmt.Sprint(i)
			r.InstanceID = fmt.Sprint("receiver-", i)
			if err := client.Call("TestCoordinator.NotifyTestCoverage", &r, &response); err != nil {
				done <- err
				return
			}
			r.Kind = "read"
			r.ResourceKey = "externalsecret/default/b"
			r.Object = `{"metadata":{"name":"b"},"spec":{}}`
			if i%2 == 0 {
				r.ResourceKey = read.ResourceKey
				r.Object = read.Object
			}
			if err := client.Call("TestCoordinator.NotifyTestCoverage", &r, &response); err != nil {
				done <- err
				return
			}
			r.Kind = "end"
			done <- client.Call("TestCoordinator.NotifyTestCoverage", &r, &response)
		}(i)
	}
	for i := 0; i < 9; i++ {
		select {
		case err := <-done:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("unrelated workers blocked")
		}
	}
	newer := `{"metadata":{"name":"a","namespace":"default","uid":"parent-a","generation":3,"resourceVersion":"30","labels":{"sieve.testing/state":"remove"}},"spec":{"value":"final"}}`
	for _, wrong := range []sieve.NotifyTestCoverageRequest{
		{Kind: "receipt-begin", ResourceKey: read.ResourceKey, Object: newer, Operation: "Updated", Observer: "metadata-informer"},
		{Kind: "receipt-begin", ResourceKey: read.ResourceKey, Object: strings.Replace(newer, "parent-a", "different-uid", 1), Operation: "Updated", Observer: "informer"},
	} {
		token := call(wrong).Number
		wrong.Kind = "receipt-end"
		wrong.ReceiptID = token
		call(wrong)
		if query("status").State != "held" {
			t.Fatal("unrelated/partial receipt released the invocation")
		}
	}
	receipt := sieve.NotifyTestCoverageRequest{Kind: "receipt-begin", ResourceKey: read.ResourceKey, Object: newer, Operation: "Updated", Observer: "informer"}
	token := call(receipt).Number
	select {
	case <-held.Done:
		t.Fatal("released before full delivery")
	default:
	}
	if query("status").State != "held" {
		t.Fatal("receipt begin released the read")
	}
	receipt.Kind = "receipt-end"
	receipt.ReceiptID = token
	call(receipt)
	select {
	case result := <-held.Done:
		if result.Error != nil || !result.Reply.(*sieve.Response).Ok {
			t.Fatalf("held read failed: %v", result.Error)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("full receipt did not release read")
	}
	begin.Kind = "end"
	call(begin)
	if query("completed").State != "completed" {
		t.Fatal("matched invocation did not finish")
	}
	text := output.text()
	last := -1
	for _, marker := range []string{"[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\tselected", "[SIEVE-AFTER-CACHE-READ]\tGet\texternalsecret\tdefault\ta", "[SIEVE-DELIVERY-OVERLAP]\t", fmt.Sprintf("[SIEVE-BEFORE-HEAR]\t%d", token), fmt.Sprintf("[SIEVE-AFTER-HEAR]\t%d", token), "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\tselected"} {
		pos := strings.Index(text, marker)
		if pos <= last {
			t.Fatalf("missing/out-of-order %s in %s", marker, text)
		}
		last = pos
	}
}

func TestDeliveryOverlapDeadlineReleasesReadAndInvalidatesAttempt(t *testing.T) {
	client, output := coverageContract(t, `coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 1
`)
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "held", InstanceID: "receiver"}
	var response sieve.Response
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &begin, &response); err != nil {
		t.Fatal(err)
	}
	read := begin
	read.Kind = "read"
	read.ResourceKey = "externalsecret/default/a"
	read.Object = `{"metadata":{"name":"a","namespace":"default","uid":"uid","generation":2,"resourceVersion":"2","labels":{"sieve.testing/state":"change"}},"spec":{"value":"two"}}`
	held := client.Go("TestCoordinator.NotifyTestCoverage", &read, new(sieve.Response), make(chan *rpc.Call, 1))
	var status overlapReply
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"held", 500}, &status); err != nil {
		t.Fatal(err)
	}
	select {
	case result := <-held.Done:
		if result.Error == nil {
			t.Fatal("expired hold reported success")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("expired hold stranded a worker")
	}
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"completed", 500}, &status); err == nil {
		t.Fatal("expired attempt was completed")
	}
	if !strings.Contains(output.text(), "[SIEVE-TRACE-ERROR] delivery overlap: held invocation deadline exceeded") {
		t.Fatal("deadline diagnostic missing")
	}
}

func TestDeliveryOverlapDoesNotHoldMetadataOnlyReads(t *testing.T) {
	client, _ := coverageContract(t, `coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`)
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "partial", InstanceID: "receiver"}
	var response sieve.Response
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &begin, &response); err != nil {
		t.Fatal(err)
	}
	read := begin
	read.Kind = "read"
	read.ResourceKey = "externalsecret/default/a"
	read.Object = `{"metadata":{"name":"a","namespace":"default","uid":"parent","generation":2,"resourceVersion":"20","labels":{"sieve.testing/state":"change"}}}`
	call := client.Go("TestCoordinator.NotifyTestCoverage", &read, new(sieve.Response), make(chan *rpc.Call, 1))
	select {
	case result := <-call.Done:
		if result.Error != nil {
			t.Fatal(result.Error)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("metadata-only read was held")
	}
	var status overlapReply
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"status", 100}, &status); err != nil {
		t.Fatal(err)
	}
	if status.State != "armed" {
		t.Fatalf("metadata read armed a hold: %+v", status)
	}
	begin.Kind = "end"
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &begin, &response); err != nil {
		t.Fatal(err)
	}
}

func TestDeliveryOverlapObserverErrorImmediatelyReleasesAndInvalidatesHeldRPC(t *testing.T) {
	client, output := coverageContract(t, `coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`)
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "selected", InstanceID: "receiver"}
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
	invalid := sieve.NotifyTestCoverageRequest{Kind: "receipt-end", ResourceKey: read.ResourceKey, Object: read.Object, ReceiptID: 99, Operation: "Updated", Observer: "informer"}
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &invalid, &response); err == nil {
		t.Fatal("unmatched receipt was accepted")
	}
	select {
	case result := <-held.Done:
		if result.Error == nil || !strings.Contains(result.Error.Error(), "unmatched receipt end") {
			t.Fatalf("observer failure did not reach held worker: %v", result.Error)
		}
	case <-time.After(time.Second):
		t.Fatal("observer failure stranded a held worker")
	}
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"completed", 100}, &status); err == nil || !strings.Contains(err.Error(), "unmatched receipt end") {
		t.Fatalf("invalid attempt did not retain observer failure: %v", err)
	}
	newer := sieve.NotifyTestCoverageRequest{Kind: "receipt-begin", ResourceKey: read.ResourceKey, Object: `{"metadata":{"name":"a","namespace":"default","uid":"parent","generation":3,"resourceVersion":"30","labels":{"sieve.testing/state":"remove"}},"spec":{"value":"final"}}`, Operation: "Updated", Observer: "informer"}
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &newer, &response); err != nil {
		t.Fatal(err)
	}
	newer.Kind, newer.ReceiptID = "receipt-end", response.Number
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &newer, &response); err != nil {
		t.Fatal(err)
	}
	begin.Kind = "end"
	if err := client.Call("TestCoordinator.NotifyTestCoverage", &begin, &response); err != nil {
		t.Fatal(err)
	}
	if err := client.Call("TestCoordinator.DeliveryOverlap", overlapQuery{"status", 100}, &status); err == nil {
		t.Fatal("later valid events revived the invalid attempt")
	}
	if !strings.Contains(output.text(), "[SIEVE-TRACE-ERROR] test coverage: unmatched receipt end") {
		t.Fatal("original observer alarm was not retained")
	}
}
