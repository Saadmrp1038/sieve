package sieve

import (
	"bytes"
	"log"
	"net"
	"net/rpc"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

type ReadPauseTestServer struct {
	released chan string
	entered  chan string
}

func (s *ReadPauseTestServer) NotifyTestBeforeControllerReadPause(request *NotifyTestBeforeControllerReadPauseRequest, response *Response) error {
	s.entered <- request.ResourceKey
	response.Ok = true
	return nil
}

func (s *ReadPauseTestServer) NotifyTestAfterControllerReadPause(request *NotifyTestAfterControllerReadPauseRequest, response *Response) error {
	s.released <- request.ResourceKey
	response.Ok = true
	return nil
}

func TestEmptyCacheReadStillNotifies(t *testing.T) {
	store, otherStore := new(int), new(int)
	defer cacheResourceTypes.Delete(store)
	defer cacheResourceTypes.Delete(otherStore)
	object := &testObject{Object: map[string]interface{}{
		"kind": "Secret", "metadata": map[string]interface{}{"name": "example", "namespace": "default"},
	}}
	// Delete/Replace instrumentation remembers the type even if no successful
	// cache read happened before its last object disappeared.
	NotifyTestCacheObject(store, object)
	if got := cacheReadResourceKey("default/example", nil, store); got != "secret/default/example" {
		t.Fatalf("empty cache resolved to %q", got)
	}
	if got := cacheReadResourceKey("default/example", nil, otherStore); got != "" {
		t.Fatalf("unrelated empty cache inherited type: %q", got)
	}

	handler := &ReadPauseTestServer{entered: make(chan string, 1), released: make(chan string, 1)}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", handler); err != nil {
		t.Fatal(err)
	}
	serverConn, clientConn := net.Pipe()
	go server.ServeConn(serverConn)
	client := rpc.NewClient(clientConn)
	defer client.Close()
	previousConfig, previousActions, previousClient, previousReconciles := config, actions, rpcClient, annotatedReconcileFunctions
	defer func() {
		config, actions, rpcClient, annotatedReconcileFunctions = previousConfig, previousActions, previousClient, previousReconciles
	}()
	config = map[string]interface{}{}
	actions = map[string][]map[string]interface{}{
		"pauseController": {{"pauseScope": "secret/default/example", "pauseAt": "beforeControllerRead"}},
	}
	rpcClient = client
	annotatedReconcileFunctions = map[string]interface{}{
		runtime.FuncForPC(reflect.ValueOf(TestEmptyCacheReadStillNotifies).Pointer()).Name(): true,
	}
	token := NotifyTestBeforeCacheGet("default/example", nil, store)
	if token != "secret/default/example" {
		t.Fatalf("before hook skipped an empty cache: %q", token)
	}
	NotifyTestAfterCacheGet("default/example", nil, false, token)
	if got := <-handler.entered; got != token {
		t.Fatalf("before RPC key %q, want %q", got, token)
	}
	if got := <-handler.released; got != token {
		t.Fatalf("after RPC key %q, want %q", got, token)
	}
	// An unrelated reconciler must be able to recreate the object while the
	// selected reconciler is paused; it must not acquire the pause's read lock.
	actions["pauseController"][0]["pauseBy"] = "another.Reconcile"
	if got := NotifyTestBeforeCacheGet("default/example", nil, store); got != "" {
		t.Fatalf("unrelated reconciler entered a targeted pause: %q", got)
	}
	select {
	case got := <-handler.entered:
		t.Fatalf("unrelated reconciler sent a blocking RPC: %q", got)
	default:
	}
	actions["pauseController"][0]["pauseBy"] = runtime.FuncForPC(reflect.ValueOf(TestEmptyCacheReadStillNotifies).Pointer()).Name()
	token = NotifyTestBeforeCacheGet("default/example", nil, store)
	if token != "secret/default/example" {
		t.Fatalf("selected reconciler skipped its targeted pause: %q", token)
	}
	NotifyTestAfterCacheGet("default/example", nil, false, token)
	<-handler.entered
	<-handler.released
}

func TestAPIServerRecordsEventsBeforeConfiguration(t *testing.T) {
	previousConfig, previousWriter := config, log.Writer()
	defer func() { config = previousConfig; log.SetOutput(previousWriter) }()
	object := &testObject{Object: map[string]interface{}{
		"kind": "ConfigMap", "metadata": map[string]interface{}{"name": "kube-root-ca.crt", "namespace": "default"},
	}}
	for _, hook := range []struct {
		name string
		call func(string, string, interface{})
	}{
		{"learn", NotifyLearnBeforeAPIServerRecv},
		{"test", NotifyTestBeforeAPIServerRecv},
	} {
		t.Run(hook.name, func(t *testing.T) {
			config = nil
			var output bytes.Buffer
			log.SetOutput(&output)
			hook.call("ADDED", "/configmaps/default/kube-root-ca.crt", object)
			if !strings.Contains(output.String(), "[SIEVE-API-EVENT]\tADDED\t/configmaps/default/kube-root-ca.crt\tconfigmap\tdefault\tkube-root-ca.crt\t") {
				t.Fatalf("pre-configuration API event was dropped: %s", output.String())
			}
			if config != nil {
				t.Fatal("ordinary ConfigMap unexpectedly initialized configuration")
			}
		})
	}
}

func TestMissingCacheObjectReleasesOnlyItsPairedRead(t *testing.T) {
	const resourceKey = "secret/default/example"
	server := rpc.NewServer()
	handler := &ReadPauseTestServer{released: make(chan string, 2)}
	if err := server.RegisterName("TestCoordinator", handler); err != nil {
		t.Fatal(err)
	}
	serverConn, clientConn := net.Pipe()
	go server.ServeConn(serverConn)
	client := rpc.NewClient(clientConn)
	defer client.Close()

	previousConfig, previousActions, previousClient := config, actions, rpcClient
	defer func() { config, actions, rpcClient = previousConfig, previousActions, previousClient }()
	config = map[string]interface{}{}
	actions = map[string][]map[string]interface{}{
		"pauseController": {{"pauseScope": resourceKey, "pauseAt": "beforeControllerRead"}},
	}
	rpcClient = client

	// A successful before hook can resume after the object was deleted.
	NotifyTestAfterCacheGet("default/example", nil, false, resourceKey)
	select {
	case got := <-handler.released:
		if got != resourceKey {
			t.Fatalf("released %q, want %q", got, resourceKey)
		}
	default:
		t.Fatal("NotFound cache read did not release its paired pause hook")
	}

	// An empty cache can skip the before hook entirely; do not unlock for it.
	NotifyTestAfterCacheGet("default/example", nil, false, "")
	select {
	case got := <-handler.released:
		t.Fatalf("unpaired cache read unexpectedly released %q", got)
	default:
	}
}
