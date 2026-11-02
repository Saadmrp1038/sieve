package sieve

import (
	"encoding/json"
	"fmt"
	"log"
	"regexp"
)

var testCoverageResources map[string]bool

func loadTestCoverage(plan map[string]interface{}) error {
	resources := map[string]bool{}
	if raw, present := plan["coverageResources"]; present {
		values, ok := raw.([]interface{})
		if !ok || len(values) > 16 {
			return fmt.Errorf("coverageResources requires at most 16 exact scopes")
		}
		pattern := regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*$`)
		for _, value := range values {
			key, ok := value.(string)
			if !ok || resources[key] || !pattern.MatchString(key) {
				return fmt.Errorf("coverageResources requires unique exact scopes")
			}
			resources[key] = true
		}
	}
	testCoverageResources = resources
	return nil
}

func notifyTestCoverage(request NotifyTestCoverageRequest) int {
	if err := initRPCClient(); err != nil {
		printRPCError(err)
		return -1
	}
	var response Response
	if err := rpcClient.Call("TestCoordinator.NotifyTestCoverage", &request, &response); err != nil {
		printRPCError(err)
		return -1
	}
	if !response.Ok {
		log.Print("[SIEVE-TRACE-ERROR] test coverage rejected")
		return -1
	}
	return response.Number
}

func NotifyTestBeforeReconcile(frame string, receivers ...interface{}) {
	if err := loadSieveConfigFromEnv(true); err != nil || len(testCoverageResources) == 0 {
		return
	}
	var receiver interface{}
	if len(receivers) > 0 {
		receiver = receivers[0]
	}
	scope, err := beginLearnInvocation(frame, receiver)
	if err != nil {
		log.Printf("[SIEVE-TRACE-ERROR] %v", err)
		return
	}
	notifyTestCoverage(NotifyTestCoverageRequest{Kind: "begin", Frame: frame, InvocationID: scope.id, InstanceID: scope.instance})
}

func NotifyTestAfterReconcile(frame string) {
	if err := loadSieveConfigFromEnv(true); err != nil || len(testCoverageResources) == 0 {
		return
	}
	scope := currentLearnInvocation()
	if scope.id == "" || scope.frame != frame {
		log.Printf("[SIEVE-TRACE-ERROR] unmatched test reconciliation end %s", frame)
		return
	}
	defer endLearnInvocation(scope)
	notifyTestCoverage(NotifyTestCoverageRequest{Kind: "end", Frame: frame, InvocationID: scope.id, InstanceID: scope.instance})
}

func testReceipt(kind string, token int, operation, resource string, object interface{}) (NotifyTestCoverageRequest, bool) {
	if !testCoverageResources[resource] {
		return NotifyTestCoverageRequest{}, false
	}
	body, err := json.Marshal(object)
	if err != nil {
		printSerializationError(err)
		return NotifyTestCoverageRequest{}, false
	}
	return NotifyTestCoverageRequest{Kind: kind, ReceiptID: token, ResourceKey: resource, Object: string(body), Operation: operation, Observer: informerObserver(object)}, true
}
