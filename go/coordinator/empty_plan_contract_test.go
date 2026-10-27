package main

import (
	"context"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnfaultedPlansCompleteWithoutAnObservation(t *testing.T) {
	for _, actions := range []string{"null", "[]"} {
		t.Run(actions, func(t *testing.T) {
			t.Chdir(t.TempDir())
			for _, name := range []string{"learned_field_path_mask.json", "configured_field_path_mask.json", "configured_field_key_mask.json"} {
				if err := os.WriteFile(name, []byte("{}"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := os.WriteFile("server.yaml", []byte("workload: control\nactions: "+actions+"\n"), 0600); err != nil {
				t.Fatal(err)
			}
			output, err := os.Create(filepath.Join(t.TempDir(), "coordinator.log"))
			if err != nil {
				t.Fatal(err)
			}
			defer output.Close()
			previous := log.Writer()
			log.SetOutput(output)
			defer log.SetOutput(previous)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if _, err := NewTestCoordinator(ctx); err != nil {
				t.Fatal(err)
			}
			content, err := os.ReadFile(output.Name())
			if err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(string(content), "Sieve test coordinator finishes all actions") {
				t.Fatal("unfaulted schedule did not complete at startup")
			}
		})
	}
}
