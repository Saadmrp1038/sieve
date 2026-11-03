package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestTestModeInstrumenterCommandPreservesReturnsAndEmitsInvocationEvidence(t *testing.T) {
	app, dependency := t.TempDir(), t.TempDir()
	for _, item := range []struct{ fixture, path string }{{"informer", "tools/cache/shared_informer.go"}, {"store", "tools/cache/store.go"}, {"request", "rest/request.go"}} {
		body, err := os.ReadFile("testdata/" + item.fixture + "/input.go.txt")
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(dependency, item.path)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	original, err := os.ReadFile("testdata/reconcile/input.go.txt")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app, "reconcile.go"), original, 0600); err != nil {
		t.Fatal(err)
	}
	goCommand := filepath.Join(runtime.GOROOT(), "bin/go")
	binary := filepath.Join(t.TempDir(), "instrumenter")
	build := exec.Command(goCommand, "build", "-o", binary, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, output)
	}
	data, err := json.Marshal(InstrumentationConfig{Project: "fixture", Mode: TEST, ApplicationPath: app, ClientGoPath: dependency, ReconcileFunctions: map[string]string{"reconcile.go": "example.(*Reconciler).Reconcile"}})
	if err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(app, "instrumentation.json")
	if err := os.WriteFile(config, data, 0600); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command(binary, config).CombinedOutput(); err != nil {
		t.Fatalf("instrument: %v %s", err, output)
	}
	client, err := filepath.Abs("../client")
	if err != nil {
		t.Fatal(err)
	}
	module := fmt.Sprintf("module example\ngo 1.27.0\nrequire sieve.client v0.0.0\nreplace sieve.client => %s\n", client)
	if err := os.WriteFile(filepath.Join(app, "go.mod"), []byte(module), 0600); err != nil {
		t.Fatal(err)
	}
	behavior, err := os.ReadFile("testdata/reconcile/test_behavior_test.go.txt")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app, "behavior_test.go"), behavior, 0600); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(goCommand, "test", "-count=1", "-timeout=15s", ".")
	command.Dir = app
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("instrumented public behavior: %v\n%s", err, output)
	}
}
