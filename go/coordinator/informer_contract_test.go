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

func TestMetadataInformerTriggersDoNotCountFullObjectReceipts(t *testing.T) {
	for _, when := range []string{"beforeControllerRecv", "afterControllerRecv"} {
		t.Run(when, func(t *testing.T) { testMetadataInformerTrigger(t, when) })
	}
}

func testMetadataInformerTrigger(t *testing.T, when string) {
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	plan := `workload: repair
actions:
- actionType: pauseController
  pauseAt: beforeControllerRead
  pauseScope: secret/default/example
  trigger:
    definitions:
    - triggerName: start
      condition:
        conditionType: onObjectCreate
        resourceKey: secret/default/example
        occurrence: 2
      observationPoint:
        when: afterControllerRecv
        by: metadata-informer
    expression: start
- actionType: resumeController
  pauseAt: beforeControllerRead
  pauseScope: secret/default/example
  trigger:
    definitions:
    - triggerName: finish
      condition:
        conditionType: onObjectDelete
        resourceKey: secret/default/example
        occurrence: 1
      observationPoint:
        when: afterControllerRecv
        by: metadata-informer
    expression: finish
`
	if err := os.WriteFile("server.yaml", []byte(strings.ReplaceAll(plan, "afterControllerRecv", when)), 0600); err != nil {
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
		case completed := <-result.Done:
			if completed.Error != nil || !response.Ok {
				t.Fatalf("%s: %v, response %+v", method, completed.Error, response)
			}
		case <-time.After(2 * time.Second):
			t.Fatalf("%s did not finish", method)
		}
	}
	receive := func(operation, observer string) {
		for _, method := range []string{"NotifyTestBeforeControllerRecv", "NotifyTestAfterControllerRecv"} {
			call(method, struct{ OperationType, ResourceKey, Object, ObservedBy string }{
				operation, "secret/default/example", `{"metadata":{"name":"example","namespace":"default"}}`, observer,
			})
		}
	}
	read := struct{ OperationType, ResourceKey, ResourceType string }{"Get", "secret/default/example", "secret"}
	// Legacy full-object receipts keep their existing observer. Neither they
	// nor the first metadata create may satisfy occurrence two of that stream.
	receive("Added", "")
	receive("Added", "informer")
	receive("Added", "metadata-informer")
	call("NotifyTestBeforeControllerReadPause", read)
	call("NotifyTestAfterControllerReadPause", read)
	receive("Added", "metadata-informer")
	var response struct{ Ok bool }
	held := client.Go("TestCoordinator.NotifyTestBeforeControllerReadPause", read, &response, nil)
	select {
	case <-held.Done:
		t.Fatal("metadata trigger did not hold the matching read")
	case <-time.After(100 * time.Millisecond):
	}
	receive("Deleted", "metadata-informer")
	select {
	case result := <-held.Done:
		if result.Error != nil || !response.Ok {
			t.Fatalf("held read failed: %v", result.Error)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("metadata deletion did not release the held read")
	}
	call("NotifyTestAfterControllerReadPause", read)
}
