package main

import (
	"bytes"
	"context"
	"flag"
	"fmt"
	"github.com/dave/dst"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

var updateGoldens = flag.Bool("update", false, "update reviewed instrumentation snapshots")

func TestInstrumentedSourcesCompileAndPreserveBehavior(t *testing.T) {
	client, err := filepath.Abs("../client")
	if err != nil {
		t.Fatal(err)
	}
	type instrumentationCase struct {
		name, mode string
		instrument func(string, string, string)
	}
	cases := []instrumentationCase{
		{"reconcile", "Learn", func(input, output, mode string) {
			instrumentAnnotatedReconcile(input, output, "example", "Reconcile", "*Reconciler", "example.(*Reconciler).Reconcile")
		}},
	}
	for _, mode := range []string{"Learn", "Test"} {
		cases = append(cases,
			instrumentationCase{"api", mode, func(input, output, mode string) {
				instrumentAnnotatedAPI(input, output, "example", "client.go", "example", "Create", "*Client", mode, nil, true)
			}},
			instrumentationCase{"store", mode, instrumentStoreGoForAll},
			instrumentationCase{"informer", mode, instrumentSharedInformerGoForAll},
			instrumentationCase{"request", mode, instrumentRequestGoForAll},
			instrumentationCase{"watch", mode, func(input, output, mode string) {
				instrumentWatchCacheGoForAll(input, output, mode, true, mode == "Test")
			}},
		)
	}
	for _, test := range cases {
		t.Run(test.name+"/"+test.mode, func(t *testing.T) {
			directory := t.TempDir()
			fixture := filepath.Join("testdata", test.name)
			input := filepath.Join(fixture, "input.go.txt")
			output := filepath.Join(directory, "instrumented.go")
			test.instrument(input, output, test.mode)
			actual, err := os.ReadFile(output)
			if err != nil {
				t.Fatal(err)
			}
			golden := filepath.Join(fixture, strings.ToLower(test.mode)+".golden.go.txt")
			if *updateGoldens {
				if err := os.WriteFile(golden, actual, 0600); err != nil {
					t.Fatal(err)
				}
			}
			expected, err := os.ReadFile(golden)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(actual, expected) {
				t.Fatalf("instrumentation changed; review %s before updating the snapshot", golden)
			}
			behavior, err := os.ReadFile(filepath.Join(fixture, "behavior_test.go.txt"))
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(directory, "behavior_test.go"), behavior, 0600); err != nil {
				t.Fatal(err)
			}
			module := fmt.Sprintf("module fixture\n\ngo 1.27.0\nrequire sieve.client v0.0.0\nreplace sieve.client => %q\n", client)
			if err := os.WriteFile(filepath.Join(directory, "go.mod"), []byte(module), 0600); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			command := exec.CommandContext(ctx, filepath.Join(runtime.GOROOT(), "bin", "go"), "test", "-count=1", "./...")
			command.Dir = directory
			for _, value := range os.Environ() {
				key := strings.SplitN(value, "=", 2)[0]
				if key != "sieveTestPlan" && key != "GOWORK" && key != "GOPROXY" {
					command.Env = append(command.Env, value)
				}
			}
			command.Env = append(command.Env, "GOWORK=off", "GOPROXY=off")
			if output, err := command.CombinedOutput(); err != nil {
				t.Fatalf("instrumented source failed: %v\n%s", err, output)
			}
		})
	}
}

func TestMissingTargetDoesNotOverwriteTheSource(t *testing.T) {
	path := filepath.Join(t.TempDir(), "input.go")
	original := []byte("package example\n type Other struct{}\n")
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if recover() == nil {
			t.Error("missing target was accepted")
		}
		actual, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(actual, original) {
			t.Fatal("failed instrumentation changed the source")
		}
	}()
	instrumentAnnotatedReconcile(path, path, "example", "Reconcile", "*Reconciler", "example.(*Reconciler).Reconcile")
}

func TestRenderingFailureKeepsOriginalSource(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.go")
	original := []byte("package example\n")
	if err := os.WriteFile(path, original, 0640); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if recover() == nil {
			t.Error("invalid syntax tree was accepted")
		}
		actual, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(actual, original) {
			t.Fatal("render failure truncated source")
		}
	}()
	writeInstrumentedFile(path, "example", &dst.File{}, nil)
}
