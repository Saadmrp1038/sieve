package sieve

import (
	"net"
	"net/rpc"
	"reflect"
	"runtime"
	"testing"
)

type GeneratedWriteServer struct{ received chan string }

func (s *GeneratedWriteServer) NotifyTestAfterControllerWrite(request *NotifyTestAfterControllerWriteRequest, response *Response) error {
	s.received <- request.ResourceKey
	response.Ok = true
	return nil
}

func TestGeneratedNameWritesReachCoordinatorOnlyWithExplicitPattern(t *testing.T) {
	previousConfig, previousActions, previousClient, previousFrames, previousTriggers := config, actions, rpcClient, annotatedReconcileFunctions, triggerDefinitionsByResourceKey
	defer func() {
		config, actions, rpcClient, annotatedReconcileFunctions, triggerDefinitionsByResourceKey = previousConfig, previousActions, previousClient, previousFrames, previousTriggers
	}()
	config = map[string]interface{}{}
	actions = map[string][]map[string]interface{}{}
	frame := runtime.FuncForPC(reflect.ValueOf(TestGeneratedNameWritesReachCoordinatorOnlyWithExplicitPattern).Pointer()).Name()
	annotatedReconcileFunctions = map[string]interface{}{frame: true}
	handler := &GeneratedWriteServer{received: make(chan string, 16)}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", handler); err != nil {
		t.Fatal(err)
	}
	local, remote := net.Pipe()
	go server.ServeConn(remote)
	rpcClient = rpc.NewClient(local)
	defer rpcClient.Close()
	for _, enabled := range []bool{false, true} {
		triggerDefinitionsByResourceKey = map[string][]map[string]interface{}{"secret/default/example-*": {{"condition": map[string]interface{}{"conditionType": "onObjectCreate", "resourceKeyPattern": enabled}, "observationPoint": map[string]interface{}{"when": "afterControllerWrite", "by": frame}}}}
		for _, item := range []struct {
			namespace, name string
			want            bool
		}{{"default", "example-abc", enabled}, {"other", "example-abc", false}, {"default", "different-abc", false}, {"default", "example-def", enabled}} {
			object := &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"namespace": item.namespace, "name": item.name}}}
			NotifyTestAfterRestCall(1, "POST", "", "", item.namespace, true, "secrets", "", "", object, nil, nil)
			select {
			case got := <-handler.received:
				if !item.want || got != "secret/"+item.namespace+"/"+item.name {
					t.Fatalf("unexpected forwarded write %s (pattern=%v)", got, enabled)
				}
			default:
				if item.want {
					t.Fatalf("generated write was dropped (pattern=%v)", enabled)
				}
			}
		}
	}
}
