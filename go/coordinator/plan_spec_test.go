package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"go.yaml.in/yaml/v3"
)

func TestSharedInvalidPlans(t *testing.T) {
	var cases []struct {
		Name   string
		Path   []string
		Value  any
		Delete bool
	}
	data, err := os.ReadFile("../../fixtures/plans/invalid-cases.json")
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&cases); err != nil {
		t.Fatal(err)
	}
	for _, test := range cases {
		t.Run(test.Name, func(t *testing.T) {
			data, err := os.ReadFile("../../fixtures/plans/restart.yaml")
			if err != nil {
				t.Fatal(err)
			}
			var root map[string]any
			if err := yaml.Unmarshal(data, &root); err != nil {
				t.Fatal(err)
			}
			var parent any = root
			for _, key := range test.Path[:len(test.Path)-1] {
				switch value := parent.(type) {
				case map[string]any:
					parent = value[key]
				case []any:
					index, err := strconv.Atoi(key)
					if err != nil {
						t.Fatal(err)
					}
					parent = value[index]
				default:
					t.Fatalf("invalid test path %v", test.Path)
				}
			}
			object := parent.(map[string]any)
			key := test.Path[len(test.Path)-1]
			if test.Delete {
				delete(object, key)
			} else {
				object[key] = test.Value
			}
			encoded, err := json.Marshal(root)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := decodePlan(encoded); err == nil {
				t.Fatalf("accepted invalid plan:\n%s", encoded)
			}

		})
	}
}

func TestStrictScalarAndDocumentDecoding(t *testing.T) {
	for _, data := range []string{
		"workload: example\nactions: []\n---\nactions: []\n",
		"workload: example\nworkload: duplicate\nactions: []\n",
		"workload: example\nactions: [{actionType: restartController, waitBefore: 1.0}]\n",
	} {
		if _, err := decodePlan([]byte(data)); err == nil {
			t.Fatalf("accepted invalid plan: %s", data)
		}
	}
}

func TestSharedValidPlans(t *testing.T) {
	files, err := filepath.Glob("../../fixtures/plans/*.yaml")
	if err != nil || len(files) == 0 {
		t.Fatalf("plan corpus unavailable: %v", err)
	}
	for _, file := range files {
		t.Run(filepath.Base(file), func(t *testing.T) {
			data, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			spec, err := decodePlan(data)
			if err != nil {
				t.Fatal(err)
			}
			if len(spec.build().actions) != len(spec.Actions) {
				t.Fatal("lost actions")
			}
		})
	}
}

func TestTriggerExpressionValidation(t *testing.T) {
	names := map[string]bool{"a": true, "b": true, "c": true}
	for _, expression := range []string{"a", "a|b&c", "(a|b)&c", "a;b", "a&(b;c)"} {
		if err := validateExpression(expression, names); err != nil {
			t.Fatal(err)
		}
		tokens := expressionToInfixTokens(expression)
		graph := binaryTreeToTriggerGraph(prefixExpressionToBinaryTree(infixToPrefix(tokens)))
		if graph == nil {
			t.Fatalf("failed to compile %q", expression)
		}
	}
}

func FuzzPlanDecoding(f *testing.F) {
	for _, path := range []string{"../../fixtures/plans/restart.yaml", "../../fixtures/plans/missed-state.yaml"} {
		data, err := os.ReadFile(path)
		if err != nil {
			f.Fatal(err)
		}
		f.Add(data)
	}
	f.Add([]byte("workload: no-fault\nactions: null\n"))
	f.Fuzz(func(t *testing.T, data []byte) {
		// Invalid schedules must return an error, including malformed YAML;
		// callers should never need to recover from a decoder panic.
		_, _ = decodePlan(data)
	})
}
