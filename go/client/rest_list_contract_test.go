package sieve

import (
	"encoding/json"
	"fmt"
	"net"
	"net/rpc"
	"reflect"
	"runtime"
	"testing"
)

type restListContractServer struct {
	lists  chan []map[string]interface{}
	writes chan string
}

func (s *restListContractServer) NotifyTestAfterControllerList(request *NotifyTestAfterControllerListRequest, response *Response) error {
	var objects []map[string]interface{}
	if err := json.Unmarshal([]byte(request.ObjectList), &objects); err != nil {
		return fmt.Errorf("coordinator list contract requires an array: %w", err)
	}
	s.lists <- objects
	response.Ok = true
	return nil
}

func (s *restListContractServer) NotifyTestAfterControllerWrite(request *NotifyTestAfterControllerWriteRequest, response *Response) error {
	s.writes <- request.ResourceKey
	response.Ok = true
	return nil
}

func TestDirectRESTListUsesCoordinatorArrayContract(t *testing.T) {
	previousConfig, previousActions, previousClient := config, actions, rpcClient
	previousFrames, previousTriggers := annotatedReconcileFunctions, triggerDefinitionsByResourceKey
	defer func() {
		config, actions, rpcClient = previousConfig, previousActions, previousClient
		annotatedReconcileFunctions, triggerDefinitionsByResourceKey = previousFrames, previousTriggers
	}()

	frame := runtime.FuncForPC(reflect.ValueOf(TestDirectRESTListUsesCoordinatorArrayContract).Pointer()).Name()
	config = map[string]interface{}{}
	actions = map[string][]map[string]interface{}{}
	annotatedReconcileFunctions = map[string]interface{}{frame: true}
	triggerDefinitionsByResourceKey = map[string][]map[string]interface{}{
		"secret/default/sh.helm.release.v1.sieve-upgrade.v2": {{
			"condition":        map[string]interface{}{"conditionType": "onObjectCreate"},
			"observationPoint": map[string]interface{}{"when": "afterControllerWrite", "by": frame},
		}},
	}

	handler := &restListContractServer{
		lists:  make(chan []map[string]interface{}, 2),
		writes: make(chan string, 1),
	}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", handler); err != nil {
		t.Fatal(err)
	}
	local, remote := net.Pipe()
	go server.ServeConn(remote)
	rpcClient = rpc.NewClient(local)
	defer rpcClient.Close()

	emptyList := map[string]interface{}{
		"metadata": map[string]interface{}{"resourceVersion": "613"},
		"items":    nil,
	}
	NotifyTestAfterRestCall(1, "GET", "", "", "default", true, "secrets", "", "", emptyList, nil, nil)
	select {
	case objects := <-handler.lists:
		if len(objects) != 0 {
			t.Fatalf("empty List produced %d coordinator objects", len(objects))
		}
	default:
		t.Fatal("empty Kubernetes List did not reach the coordinator as an array")
	}

	existing := map[string]interface{}{
		"kind": "Secret",
		"metadata": map[string]interface{}{
			"name":      "sh.helm.release.v1.sieve-upgrade.v1",
			"namespace": "default",
		},
	}
	NotifyTestAfterRestCall(2, "GET", "", "", "default", true, "secrets", "", "", map[string]interface{}{
		"metadata": map[string]interface{}{"resourceVersion": "655"},
		"items":    []interface{}{existing},
	}, nil, nil)
	select {
	case objects := <-handler.lists:
		if len(objects) != 1 {
			t.Fatalf("nonempty List produced %d coordinator objects", len(objects))
		}
		metadata, ok := objects[0]["metadata"].(map[string]interface{})
		if !ok || metadata["name"] != "sh.helm.release.v1.sieve-upgrade.v1" {
			t.Fatalf("coordinator received the wrong List item: %#v", objects[0])
		}
	default:
		t.Fatal("nonempty Kubernetes List did not reach the coordinator as an array")
	}

	target := &testObject{Object: map[string]interface{}{
		"kind": "Secret",
		"metadata": map[string]interface{}{
			"name":      "sh.helm.release.v1.sieve-upgrade.v2",
			"namespace": "default",
		},
	}}
	NotifyTestAfterRestCall(3, "POST", "", "", "default", true, "secrets", "", "", target, nil, nil)
	select {
	case key := <-handler.writes:
		if key != "secret/default/sh.helm.release.v1.sieve-upgrade.v2" {
			t.Fatalf("later matching write used resource key %q", key)
		}
	default:
		t.Fatal("later matching write did not reach the coordinator")
	}
}
