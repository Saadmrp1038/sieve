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

	sieve "sieve.client"
)

const apiPauseContractPlan = `workload: api-pause-contract
actions:
- actionType: pauseAPIServer
  apiServerName: kind-control-plane
  pauseScope: failoverquorum/default/example
  trigger:
    definitions:
    - triggerName: arm
      condition:
        conditionType: onObjectCreate
        resourceKey: configmap/default/arm
        occurrence: 1
      observationPoint:
        when: afterControllerRecv
        by: informer
    expression: arm
- actionType: resumeAPIServer
  apiServerName: kind-control-plane
  pauseScope: failoverquorum/default/example
  trigger:
    definitions:
    - triggerName: release
      condition:
        conditionType: onObjectCreate
        resourceKey: configmap/default/release
        occurrence: 1
      observationPoint:
        when: afterControllerRecv
        by: informer
    expression: release
`

func apiPauseContractClient(t *testing.T, plan string) (*rpc.Client, context.CancelFunc) {
	t.Helper()
	t.Chdir(t.TempDir())
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile("server.yaml", []byte(plan), 0600); err != nil {
		t.Fatal(err)
	}
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
	t.Cleanup(func() { remote.Close() })
	go server.ServeConn(remote)
	client := rpc.NewClient(local)
	t.Cleanup(func() { client.Close() })
	return client, cancel
}

func apiPauseContractMarker(client *rpc.Client, name string) *rpc.Call {
	return client.Go("TestCoordinator.NotifyTestAfterControllerRecv", &sieve.NotifyTestAfterControllerRecvRequest{
		OperationType: "Added", ResourceKey: "configmap/default/" + name,
		Object: fmt.Sprintf(`{"metadata":{"name":%q,"namespace":"default"}}`, name), ObservedBy: "informer",
	}, &sieve.Response{}, nil)
}

func apiPauseContractCompleted(t *testing.T, pending *rpc.Call) {
	t.Helper()
	select {
	case result := <-pending.Done:
		if result.Error != nil || !result.Reply.(*sieve.Response).Ok {
			t.Fatalf("RPC failed: %v", result.Error)
		}
	case <-time.After(time.Second):
		t.Fatal("RPC did not finish")
	}
}

func TestAPIServerResumeWithoutWaitingDeliveryCompletes(t *testing.T) {
	client, _ := apiPauseContractClient(t, apiPauseContractPlan)
	apiPauseContractCompleted(t, apiPauseContractMarker(client, "arm"))
	apiPauseContractCompleted(t, apiPauseContractMarker(client, "release"))
}

func TestAPIServerHeldDeliveryReturnsCancellation(t *testing.T) {
	client, cancel := apiPauseContractClient(t, apiPauseContractPlan)
	apiPauseContractCompleted(t, apiPauseContractMarker(client, "arm"))
	held := client.Go("TestCoordinator.NotifyTestBeforeAPIServerRecv", &sieve.NotifyTestBeforeAPIServerRecvRequest{
		APIServerHostname: "kind-control-plane", OperationType: "MODIFIED",
		ResourceKey: "failoverquorum/default/example",
		Object:      `{"metadata":{"name":"example","namespace":"default"}}`,
	}, &sieve.Response{}, nil)
	select {
	case <-held.Done:
		t.Fatal("scoped delivery returned before cancellation or resume")
	case <-time.After(50 * time.Millisecond):
	}
	cancel()
	select {
	case result := <-held.Done:
		if result.Error == nil {
			t.Fatal("cancelled delivery was reported as successful")
		}
	case <-time.After(time.Second):
		t.Fatal("held delivery did not return on cancellation")
	}
}

func TestAPIServerResumeReleasesEveryDeliveryAndAllowsAnotherPause(t *testing.T) {
	second := strings.ReplaceAll(strings.TrimPrefix(apiPauseContractPlan, "workload: api-pause-contract\nactions:\n"), "configmap/default/arm", "configmap/default/arm-again")
	second = strings.ReplaceAll(second, "configmap/default/release", "configmap/default/release-again")
	client, _ := apiPauseContractClient(t, apiPauseContractPlan+second)
	for _, suffix := range []string{"", "-again"} {
		apiPauseContractCompleted(t, apiPauseContractMarker(client, "arm"+suffix))
		held := make([]*rpc.Call, 10)
		for i := range held {
			held[i] = client.Go("TestCoordinator.NotifyTestBeforeAPIServerRecv", &sieve.NotifyTestBeforeAPIServerRecvRequest{
				APIServerHostname: "kind-control-plane", OperationType: "MODIFIED",
				ResourceKey: "failoverquorum/default/example",
				Object:      `{"metadata":{"name":"example","namespace":"default"}}`,
			}, &sieve.Response{}, nil)
		}
		// Unrelated resources on the same server and the same resource on a
		// different server must remain usable while this delivery is held.
		for _, request := range []*sieve.NotifyTestBeforeAPIServerRecvRequest{
			{APIServerHostname: "kind-control-plane", OperationType: "MODIFIED", ResourceKey: "failoverquorum/default/unrelated", Object: `{}`},
			{APIServerHostname: "another-server", OperationType: "MODIFIED", ResourceKey: "failoverquorum/default/example", Object: `{}`},
		} {
			apiPauseContractCompleted(t, client.Go("TestCoordinator.NotifyTestBeforeAPIServerRecv", request, &sieve.Response{}, nil))
		}
		for _, pending := range held {
			select {
			case <-pending.Done:
				t.Fatal("delivery escaped its active pause")
			case <-time.After(10 * time.Millisecond):
			}
		}
		apiPauseContractCompleted(t, apiPauseContractMarker(client, "release"+suffix))
		for _, pending := range held {
			apiPauseContractCompleted(t, pending)
		}
	}
}
