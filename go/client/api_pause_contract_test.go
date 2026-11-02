package sieve_test

import (
	"encoding/json"
	"net"
	"net/rpc"
	"os"
	"os/exec"
	"testing"
	"time"

	sieve "sieve.client"
)

type apiPauseObject struct{ Object map[string]interface{} }

func (o *apiPauseObject) GetKind() string { return o.Object["kind"].(string) }
func (o *apiPauseObject) GetName() string {
	return o.Object["metadata"].(map[string]interface{})["name"].(string)
}
func (o *apiPauseObject) GetNamespace() string         { return "default" }
func (o *apiPauseObject) MarshalJSON() ([]byte, error) { return json.Marshal(o.Object) }

type APIPauseContractServer struct{ requests chan string }

func (s *APIPauseContractServer) NotifyTestBeforeAPIServerRecv(request *sieve.NotifyTestBeforeAPIServerRecvRequest, response *sieve.Response) error {
	s.requests <- request.ResourceKey
	response.Ok = true
	return nil
}

func TestAPIServerPauseScopeRoutesBeforeWithoutTrigger(t *testing.T) {
	if address := os.Getenv("SIEVE_API_PAUSE_CONTRACT_ADDRESS"); address != "" {
		scope := os.Getenv("SIEVE_API_PAUSE_CONTRACT_SCOPE")
		hostname, err := os.Hostname()
		if err != nil {
			t.Fatal(err)
		}
		plan := map[string]interface{}{
			"sieveServerAddr": address, "annotatedReconcileStackFrame": []string{},
			"actions": []interface{}{map[string]interface{}{
				"actionType": "pauseAPIServer", "apiServerName": hostname,
				"pauseScope": scope,
				"trigger": map[string]interface{}{"definitions": []interface{}{map[string]interface{}{
					"triggerName": "armed", "condition": map[string]interface{}{
						"conditionType": "onObjectCreate", "resourceKey": "cluster/default/example", "occurrence": 1,
					}, "observationPoint": map[string]interface{}{"when": "beforeAPIServerRecv", "by": hostname},
				}}, "expression": "armed"},
			}},
		}
		encoded, err := json.Marshal(plan)
		if err != nil {
			t.Fatal(err)
		}
		configuration := &apiPauseObject{Object: map[string]interface{}{
			"kind": "ConfigMap", "metadata": map[string]interface{}{"name": "sieve-testing-global-config", "namespace": "default"},
			"data": map[string]interface{}{"sieveTestPlan": string(encoded)},
		}}
		sieve.NotifyTestBeforeAPIServerRecv("ADDED", "/configmaps/default/sieve-testing-global-config", configuration)
		for _, name := range []string{"example", "unrelated"} {
			object := &apiPauseObject{Object: map[string]interface{}{
				"kind": "FailoverQuorum", "metadata": map[string]interface{}{"name": name, "namespace": "default"},
			}}
			sieve.NotifyTestBeforeAPIServerRecv("MODIFIED", "/failoverquorums/default/"+name, object)
		}
		return
	}
	server := rpc.NewServer()
	handler := &APIPauseContractServer{requests: make(chan string, 4)}
	if err := server.RegisterName("TestCoordinator", handler); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		for {
			connection, err := listener.Accept()
			if err != nil {
				return
			}
			go func(connection net.Conn) {
				defer connection.Close()
				server.ServeConn(connection)
			}(connection)
		}
	}()
	for _, scope := range []string{"failoverquorum/default/example", "all"} {
		// A fresh process exercises public ConfigMap configuration, without seeding
		// private client globals or inheriting credentials into the helper process.
		child := exec.Command(os.Args[0], "-test.run=^TestAPIServerPauseScopeRoutesBeforeWithoutTrigger$")
		child.Env = []string{"SIEVE_API_PAUSE_CONTRACT_ADDRESS=" + listener.Addr().String(), "SIEVE_API_PAUSE_CONTRACT_SCOPE=" + scope}
		finished := make(chan error, 1)
		go func() { finished <- child.Run() }()
		select {
		case err := <-finished:
			if err != nil {
				t.Fatal(err)
			}
		case <-time.After(3 * time.Second):
			child.Process.Kill()
			t.Fatal("public API callback did not finish")
		}
		expected := []string{"failoverquorum/default/example"}
		if scope == "all" {
			expected = []string{"configmap/default/sieve-testing-global-config", "failoverquorum/default/example", "failoverquorum/default/unrelated"}
		}
		for _, want := range expected {
			select {
			case key := <-handler.requests:
				if key != want {
					t.Fatalf("scope %q callback targeted %q, want %q", scope, key, want)
				}
			default:
				t.Fatalf("pause scope %q did not route its API callback without a matching trigger", scope)
			}
		}
		select {
		case key := <-handler.requests:
			t.Fatalf("unrelated resource unexpectedly routed: %q", key)
		default:
		}
	}
}
