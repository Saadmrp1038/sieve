package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

type InstrumentedAPI struct {
	Module       string            `json:"module"`
	FilePath     string            `json:"file_path"`
	Package      string            `json:"package"`
	Function     string            `json:"func_name"`
	ReceiverType string            `json:"receiver_type"`
	ImportMap    map[string]string `json:"import_map"`
}

type InstrumentationConfig struct {
	Project            string            `json:"project"`
	Mode               string            `json:"mode"`
	KubernetesPath     string            `json:"k8s_filepath"`
	ApplicationPath    string            `json:"app_file_path"`
	ClientGoPath       string            `json:"client_go_filepath"`
	ReconcileFunctions map[string]string `json:"annotated_reconcile_functions"`
	APIs               []InstrumentedAPI `json:"apis_to_instrument"`
}

func reconcileTarget(stackFrame string) (string, string, string, error) {
	pathParts := strings.Split(stackFrame, "/")
	tokens := strings.Split(pathParts[len(pathParts)-1], ".")
	if len(tokens) < 3 {
		return "", "", "", fmt.Errorf("invalid reconcile function %q", stackFrame)
	}
	pkg, receiver, function := tokens[len(tokens)-3], strings.Trim(tokens[len(tokens)-2], "()"), tokens[len(tokens)-1]
	if pkg == "" || receiver == "" || function == "" {
		return "", "", "", fmt.Errorf("invalid reconcile function %q", stackFrame)
	}
	return pkg, receiver, function, nil
}

func readConfig(configPath string) (InstrumentationConfig, error) {
	var config InstrumentationConfig
	data, err := os.ReadFile(configPath)
	if err != nil {
		return config, err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&config); err != nil {
		return config, fmt.Errorf("instrumentation config: %w", err)
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return config, fmt.Errorf("instrumentation config must contain one JSON object")
	}
	if config.Project == "" {
		return config, fmt.Errorf("project: required")
	}
	if config.Mode != LEARN && config.Mode != TEST && config.Mode != VANILLA {
		return config, fmt.Errorf("mode: unsupported mode %q", config.Mode)
	}
	if config.Mode == VANILLA {
		return config, nil
	}
	if config.Project == "kubernetes" {
		if config.KubernetesPath == "" {
			return config, fmt.Errorf("k8s_filepath: required")
		}
	} else {
		if config.ApplicationPath == "" || config.ClientGoPath == "" {
			return config, fmt.Errorf("app_file_path and client_go_filepath: required")
		}
		for path, frame := range config.ReconcileFunctions {
			if path == "" {
				return config, fmt.Errorf("annotated_reconcile_functions: empty source path")
			}
			if _, _, _, err := reconcileTarget(frame); err != nil {
				return config, err
			}
		}
		for i, api := range config.APIs {
			if api.Module == "" || api.FilePath == "" || api.Package == "" || api.Function == "" {
				return config, fmt.Errorf("apis_to_instrument[%d]: module, file_path, package and func_name are required", i)
			}
		}
	}
	return config, nil
}
