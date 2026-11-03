package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/rpc"
	"os"
	"strings"
	"testing"

	sieve "sieve.client"
)

func coverageContract(t *testing.T, scope string) (*rpc.Client, *synchronizedLog) {
	t.Helper()
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile("server.yaml", []byte("workload: metadata-labels-overlap\nactions: []\n"+scope), 0600); err != nil {
		t.Fatal(err)
	}
	output := &synchronizedLog{}
	previous := log.Writer()
	log.SetOutput(output)
	t.Cleanup(func() { log.SetOutput(previous) })
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	coordinator, err := NewTestCoordinator(ctx)
	if err != nil {
		t.Fatal(err)
	}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", coordinator); err != nil {
		t.Fatal(err)
	}
	local, remote := net.Pipe()
	go server.ServeConn(remote)
	client := rpc.NewClient(local)
	t.Cleanup(func() { client.Close(); remote.Close() })
	return client, output
}

func TestTestModeCoverageRPCOrdersScopedDeliveryAndIdentifiedInvocations(t *testing.T) {
	client, output := coverageContract(t, "coverageResources: [externalsecret/default/a]\n")
	call := func(request sieve.NotifyTestCoverageRequest) sieve.Response {
		t.Helper()
		var response sieve.Response
		if err := client.Call("TestCoordinator.NotifyTestCoverage", &request, &response); err != nil || !response.Ok {
			t.Fatalf("request=%+v response=%+v error=%v", request, response, err)
		}
		return response
	}
	begin := sieve.NotifyTestCoverageRequest{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "a", InstanceID: "instance-a"}
	call(begin)
	other := begin
	other.InvocationID = "b"
	other.InstanceID = "instance-b"
	call(other)
	old := `{"metadata":{"name":"a","namespace":"default","uid":"parent-a","generation":1}}`
	newer := `{"metadata":{"name":"a","namespace":"default","uid":"parent-a","generation":2}}`
	read := begin
	read.Kind = "read"
	read.ResourceKey = "externalsecret/default/a"
	read.Object = old
	call(read)
	receipt := sieve.NotifyTestCoverageRequest{Kind: "receipt-begin", ResourceKey: "externalsecret/default/a", Object: newer, Operation: "Updated", Observer: "informer"}
	response := call(receipt)
	if response.Number <= 0 {
		t.Fatal("missing receipt identity")
	}
	receipt.Kind = "receipt-end"
	receipt.ReceiptID = response.Number
	call(receipt)
	other.Kind = "end"
	call(other)
	begin.Kind = "end"
	call(begin)
	text := output.text()
	last := -1
	for _, marker := range []string{
		"[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\ta\tSIEVE-V2:instance-a",
		"[SIEVE-AFTER-CACHE-READ]\tGet\texternalsecret\tdefault\ta\toperator.Reconcile\tNoError\t" + old + "\tSIEVE-V2:a",
		fmt.Sprintf("[SIEVE-BEFORE-HEAR]\t%d\tUpdated\texternalsecret\t%s", response.Number, newer),
		fmt.Sprintf("[SIEVE-AFTER-HEAR]\t%d", response.Number),
		"[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\ta\tSIEVE-V2:instance-a",
	} {
		position := strings.Index(text, marker)
		if position <= last {
			t.Fatalf("missing/out-of-order %q in %s", marker, text)
		}
		last = position
	}
}

func TestTestCoverageRejectsUnmatchedOrOutOfScopeEvidence(t *testing.T) {
	for _, request := range []sieve.NotifyTestCoverageRequest{
		{Kind: "read", Frame: "operator.Reconcile", InvocationID: "missing", InstanceID: "instance", ResourceKey: "externalsecret/default/a", Object: `{}`},
		{Kind: "end", Frame: "operator.Reconcile", InvocationID: "missing", InstanceID: "instance"},
		{Kind: "begin", Frame: "operator.Reconcile", InvocationID: "missing"},
		{Kind: "receipt-begin", ResourceKey: "externalsecret/default/other", Object: `{}`, Observer: "informer", Operation: "Updated"},
		{Kind: "receipt-end", ResourceKey: "externalsecret/default/a", Object: `{}`, Observer: "informer", Operation: "Updated", ReceiptID: 99},
		{Kind: "receipt-begin", ResourceKey: "externalsecret/default/a", Object: `null`, Observer: "informer", Operation: "Updated"},
		{Kind: "receipt-begin", ResourceKey: "externalsecret/default/a", Object: `{}`, Observer: "other", Operation: "Updated"},
	} {
		t.Run(request.Kind+request.ResourceKey, func(t *testing.T) {
			client, output := coverageContract(t, "coverageResources: [externalsecret/default/a]\n")
			var response sieve.Response
			if err := client.Call("TestCoordinator.NotifyTestCoverage", &request, &response); err == nil || response.Ok {
				t.Fatal("bad coverage evidence was accepted")
			}
			if !strings.Contains(output.text(), "[SIEVE-TRACE-ERROR]") {
				t.Fatal("rejection lost its diagnostic")
			}
		})
	}
}
