package main

import (
	"encoding/json"
	"net"
	"net/rpc"
	"os"
	"os/exec"
	"reflect"
	"runtime"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	sieve "sieve.client"
)

type ClientContractServer struct{ Reads chan string }

func (s *ClientContractServer) NotifyTestBeforeControllerReadPause(r *sieve.NotifyTestBeforeControllerReadPauseRequest, response *sieve.Response) error {
	s.Reads <- r.ResourceKey
	response.Ok = true
	return nil
}
func (s *ClientContractServer) NotifyTestAfterControllerReadPause(r *sieve.NotifyTestAfterControllerReadPauseRequest, response *sieve.Response) error {
	response.Ok = true
	return nil
}

func (s *ClientContractServer) NotifyTestCacheReadResult(r *sieve.NotifyTestCacheReadResultRequest, response *sieve.Response) error {
	response.Ok = true
	return nil
}

// The injected module has no Kubernetes imports; exercise its public hooks
// against real typed and unstructured objects supplied by the current SDK.
func TestInjectedClientAcceptsKubernetesObjects(t *testing.T) {
	// A real operator loads its hook configuration once per process. Isolate
	// that global state so repeated test runs exercise the same lifecycle.
	if os.Getenv("SIEVE_CLIENT_CONTRACT_CHILD") != "1" {
		command := exec.Command(os.Args[0], "-test.run=^TestInjectedClientAcceptsKubernetesObjects$")
		command.Env = append(os.Environ(), "SIEVE_CLIENT_CONTRACT_CHILD=1")
		if output, err := command.CombinedOutput(); err != nil {
			t.Fatalf("client contract failed: %v\n%s", err, output)
		}
		return
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	service := &ClientContractServer{Reads: make(chan string, 4)}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", service); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	connections := make(chan net.Conn, 1)
	go func() {
		defer close(done)
		connection, err := listener.Accept()
		if err == nil {
			connections <- connection
			defer connection.Close()
			server.ServeConn(connection)
		}
	}()
	t.Cleanup(func() {
		listener.Close()
		select {
		case connection := <-connections:
			connection.Close()
		case <-done:
		}
		<-done
	})
	frame := runtime.FuncForPC(reflect.ValueOf(TestInjectedClientAcceptsKubernetesObjects).Pointer()).Name()
	config := map[string]interface{}{"sieveServerAddr": listener.Addr().String(), "annotatedReconcileStackFrame": []string{frame}, "actions": []interface{}{map[string]interface{}{
		"actionType": "pauseController", "pauseAt": "beforeControllerRead", "pauseScope": "all",
		"trigger": map[string]interface{}{"definitions": []interface{}{}},
	}}}
	payload, _ := json.Marshal(config)
	t.Setenv("sieveTestPlan", string(payload))
	secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "example", Namespace: "default"}}
	custom := &unstructured.Unstructured{Object: map[string]interface{}{"kind": "StringSecret", "metadata": map[string]interface{}{"name": "example", "namespace": "default"}}}
	for _, item := range []struct {
		Object interface{}
		Kind   string
	}{{secret, "secret"}, {custom, "stringsecret"}} {
		store := new(int)
		sieve.NotifyTestCacheObject(store, item.Object)
		token := sieve.NotifyTestBeforeCacheGet("default/example", nil, store)
		expected := item.Kind + "/default/example"
		if token != expected {
			t.Fatalf("metadata adapter produced %q, want %q", token, expected)
		}
		sieve.NotifyTestAfterCacheGet("default/example", nil, false, token)
		select {
		case key := <-service.Reads:
			if key != expected {
				t.Fatal(key)
			}
		case <-time.After(time.Second):
			t.Fatal("read hook emitted no notification")
		}
	}
}
