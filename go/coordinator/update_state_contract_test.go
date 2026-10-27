package main

import (
	"context"
	"fmt"
	"net"
	"net/rpc"
	"os"
	"strings"
	"testing"
	"time"
)

func TestUpdateStateWaitsForBothCacheRepresentations(t *testing.T) {
	plan, err := os.ReadFile("../../fixtures/plans/independent-state.yaml")
	if err != nil {
		t.Fatal(err)
	}
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile("server.yaml", plan, 0600); err != nil {
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
	local, remote := net.Pipe()
	defer remote.Close()
	go server.ServeConn(remote)
	client := rpc.NewClient(local)
	defer client.Close()
	call := func(method string, request interface{}) {
		t.Helper()
		var response struct{ Ok bool }
		result := client.Go("TestCoordinator."+method, request, &response, nil)
		select {
		case r := <-result.Done:
			if r.Error != nil || !response.Ok {
				t.Fatalf("%s: %v", method, r.Error)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("%s did not finish", method)
		}
	}
	api := func(operation, phase string) {
		call("NotifyTestBeforeAPIServerRecv", struct{ APIServerHostname, OperationType, ResourceKey, Object string }{
			"kind-control-plane", operation, "secret/default/example", fmt.Sprintf(`{"labels":{"sieve.testing/state":%q},"Data":{"marker":"unrelated"}}`, phase),
		})
	}
	receive := func(observer, phase string) {
		call("NotifyTestAfterControllerRecv", struct{ OperationType, ResourceKey, Object, ObservedBy string }{
			"Updated", "secret/default/example", fmt.Sprintf(`{"metadata":{"name":"example","namespace":"default","labels":{"sieve.testing/state":%q}},"data":{"marker":"other fields may differ"}}`, phase), observer,
		})
	}
	api("ADDED", "A")
	api("MODIFIED", "B")
	var response struct{ Ok bool }
	read := struct{ OperationType, ResourceKey, ResourceType string }{"Get", "secret/default/example", "secret"}
	held := client.Go("TestCoordinator.NotifyTestBeforeControllerReadPause", read, &response, nil)
	assertHeld := func() {
		t.Helper()
		select {
		case <-held.Done:
			t.Fatal("read returned before both caches reached C")
		case <-time.After(50 * time.Millisecond):
		}
	}
	assertHeld()
	receive("metadata-informer", "B")
	receive("informer", "B")
	assertHeld()
	receive("metadata-informer", "C")
	assertHeld()
	receive("informer", "C")
	select {
	case r := <-held.Done:
		if r.Error != nil || !response.Ok {
			t.Fatalf("read failed: %v", r.Error)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("later state did not release the read")
	}
	call("NotifyTestAfterControllerReadPause", read)
}

func TestConvertedStateRejectsInvalidShapes(t *testing.T) {
	t.Chdir(t.TempDir())
	plan := `workload: validation
actions:
- actionType: resumeController
  pauseAt: beforeControllerRead
  pauseScope: secret/default/example
  trigger:
    definitions:
    - triggerName: later
      condition:
        conditionType: onObjectUpdateTo
        resourceKey: secret/default/example
        occurrence: 1
        stateMatch: '{"metadata":"invalid"}'
        convertStateToAPIForm: true
      observationPoint:
        when: afterAPIServerRecv
        by: kind-control-plane
    expression: later
`
	for _, state := range []string{`{"metadata":"invalid"}`, `{"":"invalid"}`, `{"spec":{"":1}}`, `{"items":[{"":1}]}`} {
		encoded := strings.Replace(plan, `{"metadata":"invalid"}`, state, 1)
		if err := os.WriteFile("server.yaml", []byte(encoded), 0600); err != nil {
			t.Fatal(err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		_, err := NewTestCoordinator(ctx)
		cancel()
		if err == nil || !strings.Contains(err.Error(), "stateMatch") {
			t.Fatalf("expected a field-specific conversion error, got %v", err)
		}
	}
}
