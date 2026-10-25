package main

import (
	"go.yaml.in/yaml/v3"
	"io"
	"log"
	"os"
	"path/filepath"
	"testing"
)

func TestHistoricalPlansKeepTheirTriggersWithModernYAML(t *testing.T) {
	previous := log.Writer()
	log.SetOutput(io.Discard)
	defer log.SetOutput(previous)
	files, err := filepath.Glob("../../fixtures/reproductions/*.yaml")
	if err != nil || len(files) == 0 {
		t.Fatalf("plan corpus unavailable: %v", err)
	}
	for _, file := range files {
		t.Run(filepath.Base(file), func(t *testing.T) {
			data, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			var raw map[string]interface{}
			if err := yaml.Unmarshal(data, &raw); err != nil {
				t.Fatal(err)
			}
			plan := parseTestPlan(raw)
			actions, ok := raw["actions"].([]interface{})
			if (!ok && raw["actions"] != nil) || len(plan.actions) != len(actions) {
				t.Fatal("actions were lost during YAML decoding")
			}
			for _, action := range plan.actions {
				if len(action.getTriggerDefinitions()) == 0 {
					t.Fatal("action lost its triggers")
				}
			}
		})
	}
}
