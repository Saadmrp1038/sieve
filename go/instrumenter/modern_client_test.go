package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// Exercise the instrumenter command and client-go's public informer API with
// the native dependency versions used by cert-manager, CloudNativePG and External Secrets.
func TestModernClientGoInstrumentation(t *testing.T) {
	for _, version := range []string{"v0.36.2", "v0.37.0", "v0.37.1"} {
		t.Run(version, func(t *testing.T) { testModernClientGoInstrumentation(t, version) })
	}
}

func testModernClientGoInstrumentation(t *testing.T, version string) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	goCommand := filepath.Join(runtime.GOROOT(), "bin", "go")
	directory := t.TempDir()
	client, err := filepath.Abs("../client")
	if err != nil {
		t.Fatal(err)
	}
	module := fmt.Sprintf("module fixture\n\ngo 1.27.0\nrequire (\n k8s.io/client-go %s\n k8s.io/apimachinery %s\n sieve.client v0.0.0\n)\nreplace sieve.client => %s\n", version, version, client)
	if err := os.WriteFile(filepath.Join(directory, "go.mod"), []byte(module), 0600); err != nil {
		t.Fatal(err)
	}
	command := func(cwd string, args ...string) []byte {
		t.Helper()
		cmd := exec.CommandContext(ctx, goCommand, args...)
		cmd.Dir = cwd
		cmd.Env = append(os.Environ(), "GOWORK=off")
		output, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("%v: %v\n%s", args, err, output)
		}
		return output
	}
	var downloaded struct{ Dir string }
	if err := json.Unmarshal(command(directory, "mod", "download", "-json", "k8s.io/client-go@"+version), &downloaded); err != nil {
		t.Fatal(err)
	}
	dependency := filepath.Join(directory, "client-go")
	if err := os.CopyFS(dependency, os.DirFS(downloaded.Dir)); err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(filepath.Join(directory, "go.mod"), os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.WriteString("replace k8s.io/client-go => ./client-go\n"); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(directory, "instrumenter")
	command(".", "build", "-o", binary, ".")
	config, err := json.Marshal(InstrumentationConfig{Project: "fixture", Mode: LEARN, ApplicationPath: directory, ClientGoPath: dependency})
	if err != nil {
		t.Fatal(err)
	}
	configuration := filepath.Join(directory, "instrumentation.json")
	if err := os.WriteFile(configuration, config, 0600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.CommandContext(ctx, binary, configuration)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("modern instrumentation: %v\n%s", err, output)
	}
	behavior, err := os.ReadFile("testdata/modern-informer/behavior_test.go.txt")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "behavior_test.go"), behavior, 0600); err != nil {
		t.Fatal(err)
	}
	command(directory, "test", "-mod=mod", "-count=1", "-timeout=60s", "-run", "TestInformerReceiptsBracketRealCacheUpdates", ".")
	if err := os.RemoveAll(dependency); err != nil {
		t.Fatal(err)
	}
	if err := os.CopyFS(dependency, os.DirFS(downloaded.Dir)); err != nil {
		t.Fatal(err)
	}
	config, err = json.Marshal(InstrumentationConfig{Project: "fixture", Mode: TEST, ApplicationPath: directory, ClientGoPath: dependency})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configuration, config, 0600); err != nil {
		t.Fatal(err)
	}
	cmd = exec.CommandContext(ctx, binary, configuration)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("modern test instrumentation: %v\n%s", err, output)
	}
	command(directory, "test", "-mod=mod", "-count=1", "-timeout=60s", "-run", "TestReadPauseOnModernInformer", ".")
}
