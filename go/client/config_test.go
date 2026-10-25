package sieve

import (
	"encoding/json"
	"os"
	"testing"
)

func TestJSONConfigurationFromEnvAndConfigMap(t *testing.T) {
	previousConfig, previousActions := config, actions
	previousCRDs, previousFrames := crds, annotatedReconcileFunctions
	previousTriggers := triggerDefinitionsByResourceKey
	previous, present := os.LookupEnv("sieveTestPlan")
	defer func() {
		config, actions = previousConfig, previousActions
		crds, annotatedReconcileFunctions = previousCRDs, previousFrames
		triggerDefinitionsByResourceKey = previousTriggers
		if present {
			os.Setenv("sieveTestPlan", previous)
		} else {
			os.Unsetenv("sieveTestPlan")
		}
	}()
	for _, testMode := range []bool{false, true} {
		plan := map[string]interface{}{"crdList": []string{"stringsecret"}, "annotatedReconcileStackFrame": []string{"example.Reconcile"}, "actions": []interface{}{map[string]interface{}{
			"actionType": "pauseController", "pauseAt": "beforeControllerRead", "pauseScope": "secret/default/example",
			"trigger": map[string]interface{}{"definitions": []interface{}{map[string]interface{}{
				"condition":        map[string]interface{}{"conditionType": "onObjectDelete", "resourceKey": "secret/default/example"},
				"observationPoint": map[string]interface{}{"when": "afterControllerRecv"},
			}}},
		}}}
		payload, err := json.Marshal(plan)
		if err != nil {
			t.Fatal(err)
		}
		config = nil
		actions = make(map[string][]map[string]interface{})
		annotatedReconcileFunctions = make(map[string]interface{})
		triggerDefinitionsByResourceKey = make(map[string][]map[string]interface{})
		os.Setenv("sieveTestPlan", string(payload))
		if err := loadSieveConfigFromEnv(testMode); err != nil {
			t.Fatal(err)
		}
		if config == nil || len(annotatedReconcileFunctions) != 1 {
			t.Fatal("configuration was not initialized")
		}
		if testMode && len(actions["pauseController"]) != 1 {
			t.Fatal("JSON actions were dropped")
		}
		if testMode && len(triggerDefinitionsByResourceKey["secret/default/example"]) != 1 {
			t.Fatal("JSON triggers were dropped")
		}
		for _, key := range []string{"data", "Data"} {
			config = nil
			object := &testObject{Object: map[string]interface{}{"kind": "ConfigMap", key: map[string]interface{}{"sieveTestPlan": string(payload)}}}
			if err := loadSieveConfigFromConfigMap("ADDED", "/configmaps/default/sieve-testing-global-config", object, testMode); err != nil {
				t.Fatal(err)
			}
			if config == nil {
				t.Fatal("ConfigMap did not initialize configuration")
			}
		}
	}
}
