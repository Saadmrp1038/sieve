package main

import (
	"bytes"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestCoordinatorOverlapCLIUsesPublicRPCAndEmitsStructuredControlStatus(t *testing.T) {
	binary := filepath.Join(t.TempDir(), "coordinator")
	build := exec.Command("go", "build", "-o", binary, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, out)
	}
	dir := t.TempDir()
	for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	plan := `workload: metadata-labels-overlap
actions: []
coverageResources: [externalsecret/default/a]
deliveryOverlap:
  mode: observe
  resourceKey: externalsecret/default/a
  reconciler: operator.Reconcile
  holdPhase: change
  releasePhase: remove
  timeoutSeconds: 5
`
	if err := os.WriteFile(filepath.Join(dir, "server.yaml"), []byte(plan), 0600); err != nil {
		t.Fatal(err)
	}
	var logs bytes.Buffer
	server := exec.Command(binary, "test")
	server.Dir = dir
	server.Stdout = &logs
	server.Stderr = &logs
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan error, 1)
	go func() { exited <- server.Wait() }()
	t.Cleanup(func() {
		server.Process.Signal(syscall.SIGTERM)
		select {
		case <-exited:
		case <-time.After(3 * time.Second):
			server.Process.Kill()
			<-exited
		}
	})
	deadline := time.Now().Add(3 * time.Second)
	for {
		connection, err := net.DialTimeout("tcp", "127.0.0.1:12345", 50*time.Millisecond)
		if err == nil {
			connection.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("coordinator did not listen")
		}
		select {
		case err := <-exited:
			t.Fatalf("server exited: %v", err)
		default:
		}
		time.Sleep(5 * time.Millisecond)
	}
	result := exec.Command(binary, "overlap", "status", "--timeout-seconds", "2")
	out, err := result.CombinedOutput()
	if err != nil {
		t.Fatalf("public CLI: %v %s", err, out)
	}
	var status map[string]interface{}
	if err := json.Unmarshal(out, &status); err != nil {
		t.Fatalf("CLI did not emit JSON: %s", out)
	}
	if status["mode"] != "observe" || status["state"] != "observing" || status["resourceKey"] != "externalsecret/default/a" {
		t.Fatalf("wrong control: %s", out)
	}
	bad := exec.Command(binary, "overlap", "release", "--timeout-seconds", "2")
	if err := bad.Run(); err == nil {
		t.Fatal("manual release must not be possible")
	}
}
