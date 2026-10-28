package main

import (
	"bytes"
	"log"
	"net"
	"net/rpc"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	sieve "sieve.client"
)

type synchronizedLog struct {
	sync.Mutex
	bytes.Buffer
}

func (b *synchronizedLog) Write(p []byte) (int, error) {
	b.Lock()
	defer b.Unlock()
	return b.Buffer.Write(p)
}

func (b *synchronizedLog) text() string {
	b.Lock()
	defer b.Unlock()
	return b.Buffer.String()
}

func TestConcurrentReconciliationsKeepInvocationAndInstanceIdentity(t *testing.T) {
	t.Chdir(t.TempDir())
	if err := os.WriteFile("server.yaml", []byte("rateLimiterEnabled: false\nrateLimiterInterval: 1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	var output synchronizedLog
	previous := log.Writer()
	log.SetOutput(&output)
	defer log.SetOutput(previous)
	server := rpc.NewServer()
	if err := server.RegisterName("LearnListener", NewLearnListener()); err != nil {
		t.Fatal(err)
	}
	local, remote := net.Pipe()
	go server.ServeConn(remote)
	client := rpc.NewClient(local)
	defer client.Close()
	call := func(method string, request interface{}) {
		t.Helper()
		var response sieve.Response
		if err := client.Call("LearnListener."+method, request, &response); err != nil || !response.Ok {
			t.Fatalf("%s: response=%+v error=%v", method, response, err)
		}
	}
	for _, id := range []string{"worker-a", "worker-b"} {
		call("NotifyLearnBeforeReconcile", &sieve.NotifyLearnBeforeReconcileRequest{ReconcilerName: "same.Frame", InvocationID: id, InstanceID: "controller-" + id})
	}
	call("NotifyLearnAfterCacheGet", &sieve.NotifyLearnAfterCacheGetRequest{ReconcileFun: "same.Frame", ReconcileID: "worker-b", ResourceType: "secret", Namespace: "default", Name: "b", Error: "NoError", Object: `{}`})
	for _, id := range []string{"worker-b", "worker-a"} {
		call("NotifyLearnAfterReconcile", &sieve.NotifyLearnAfterReconcileRequest{ReconcilerName: "same.Frame", InvocationID: id, InstanceID: "controller-" + id})
	}
	deadline := time.Now().Add(2 * time.Second)
	for !strings.Contains(output.text(), "[SIEVE-AFTER-RECONCILE]\tsame.Frame\tworker-a\tSIEVE-V2:controller-worker-a") && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	for _, want := range []string{
		"[SIEVE-BEFORE-RECONCILE]\tsame.Frame\tworker-a\tSIEVE-V2:controller-worker-a",
		"[SIEVE-BEFORE-RECONCILE]\tsame.Frame\tworker-b\tSIEVE-V2:controller-worker-b",
		"Get\tsecret\tdefault\tb\tsame.Frame\tNoError\t{}\tSIEVE-V2:worker-b",
		"[SIEVE-AFTER-RECONCILE]\tsame.Frame\tworker-b\tSIEVE-V2:controller-worker-b",
	} {
		if !strings.Contains(output.text(), want) {
			t.Fatalf("missing %q in %s", want, output.text())
		}
	}
}
