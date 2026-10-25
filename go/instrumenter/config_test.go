package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstrumentationConfigFailsBeforeSourceRewriting(t *testing.T) {
	for _, value := range []string{
		`{}`, `{"project":"operator","mode":"guess"}`,
		`{"project":"kubernetes","mode":"test"}`,
		`{"project":"operator","mode":"test","app_file_path":"src","client_go_filepath":"client","typo":true}`,
		`{"project":"operator","mode":"test","app_file_path":"src","client_go_filepath":"client","annotated_reconcile_functions":{"reconcile.go":"missing-receiver"}}`,
		`{"project":"operator","mode":"test","app_file_path":"src","client_go_filepath":"client","apis_to_instrument":[{"module":1}]}`,
	} {
		path := filepath.Join(t.TempDir(), "config.json")
		if err := os.WriteFile(path, []byte(value), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readConfig(path); err == nil {
			t.Fatalf("accepted invalid configuration %s", value)
		}
	}
}
