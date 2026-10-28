package main

import (
	"context"
	"net"
	"net/rpc"
	"os"
	"strings"
	"testing"
	"time"
)

func TestWriteOccurrenceWithoutStateDiffCountsMatchingOperations(t *testing.T) {
	testWriteOccurrence(t, "onObjectUpdate", "StatusUpdate", false)
}

func TestGeneratedNameWritePredicates(t *testing.T) {
	for condition, operation := range map[string]string{"onObjectCreate": "Create", "onObjectUpdate": "Patch", "onObjectDelete": "Delete"} {
		t.Run(condition, func(t *testing.T) { testWriteOccurrence(t, condition, operation, true) })
	}
}

func testWriteOccurrence(t *testing.T, condition, operation string, pattern bool) {
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	plan := `workload: write-occurrence
actions:
- actionType: pauseController
  pauseAt: beforeControllerRead
  pauseScope: certificate/default/example
  trigger:
    definitions:
    - triggerName: write
      condition:
        conditionType: onObjectUpdate
        resourceKey: certificate/default/example
        occurrence: 2
      observationPoint:
        when: afterControllerWrite
        by: example.Readiness
    expression: write
- actionType: resumeController
  pauseAt: beforeControllerRead
  pauseScope: certificate/default/example
  trigger:
    definitions:
    - triggerName: release
      condition:
        conditionType: onObjectCreate
        resourceKey: issuer/default/release
        occurrence: 1
      observationPoint:
        when: afterControllerRecv
        by: informer
    expression: release
`
	plan = strings.Replace(plan, "conditionType: onObjectUpdate", "conditionType: "+condition, 1)
	if pattern {
		plan = strings.Replace(plan, "resourceKey: certificate/default/example", "resourceKey: secret/default/example-*\n        resourceKeyPattern: true", 1)
	}
	if err := os.WriteFile("server.yaml", []byte(plan), 0600); err != nil {
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
	call := func(method string, request any) {
		t.Helper()
		var response struct{ Ok bool }
		pending := client.Go("TestCoordinator."+method, request, &response, nil)
		select {
		case result := <-pending.Done:
			if result.Error != nil || !response.Ok {
				t.Fatalf("%s: %v", method, result.Error)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("%s did not finish", method)
		}
	}
	write := struct{ WriteType, ResourceKey, ReconcileFun, Object string }{"StatusUpdate", "certificate/default/example", "example.Readiness", `{"metadata":{"name":"example","namespace":"default"},"status":{"phase":"ready"}}`}
	read := struct{ OperationType, ResourceKey, ResourceType string }{"Get", "certificate/default/example", "certificate"}
	release := func() {
		call("NotifyTestAfterControllerRecv", struct{ OperationType, ResourceKey, Object, ObservedBy string }{"Added", "issuer/default/release", `{"metadata":{"name":"release","namespace":"default"}}`, "informer"})
	}
	write.WriteType = operation
	if pattern {
		write.ResourceKey = "secret/other/example-random"
		call("NotifyTestAfterControllerWrite", write)
		write.ResourceKey = "secret/default/unrelated-random"
		call("NotifyTestAfterControllerWrite", write)
		write.ResourceKey = "secret/default/example-first"
	}
	call("NotifyTestAfterControllerWrite", write)
	var firstResponse struct{ Ok bool }
	first := client.Go("TestCoordinator.NotifyTestBeforeControllerReadPause", read, &firstResponse, nil)
	select {
	case result := <-first.Done:
		if result.Error != nil || !firstResponse.Ok {
			t.Fatal(result.Error)
		}
	case <-time.After(200 * time.Millisecond):
		release()
		t.Fatal("occurrence two paused the read after only one matching write")
	}
	call("NotifyTestAfterControllerReadPause", read)
	if pattern {
		write.ResourceKey = "secret/default/example-second"
	}
	call("NotifyTestAfterControllerWrite", write)
	var response struct{ Ok bool }
	held := client.Go("TestCoordinator.NotifyTestBeforeControllerReadPause", read, &response, nil)
	select {
	case <-held.Done:
		t.Fatal("second write did not hold the read")
	case <-time.After(100 * time.Millisecond):
	}
	release()
	select {
	case result := <-held.Done:
		if result.Error != nil || !response.Ok {
			t.Fatal(result.Error)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("read was not released")
	}
	call("NotifyTestAfterControllerReadPause", read)
}
