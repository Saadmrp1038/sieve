package sieve

import (
	"encoding/json"
	"fmt"
	"log"
	"net/rpc"
	"os"
	"path"
	"reflect"
	"runtime/debug"
	"strings"
	"sync"
)

const LEARN string = "learn"
const TEST string = "test"
const UNKNOWN_RECONCILE_FUN = "Unknown"

// TODO(xudong): make DEFAULT_SIEVE_SERVER_ADDR configurable
const DEFAULT_SIEVE_SERVER_ADDR string = "kind-control-plane:12345"

const HTTP_GET string = "GET"
const HTTP_POST string = "POST"
const HTTP_PUT string = "PUT"
const HTTP_PATCH string = "PATCH"
const HTTP_DELETE string = "DELETE"

const GET string = "Get"
const LIST string = "List"
const CREATE string = "Create"
const UPDATE string = "Update"
const STATUSUPDATE string = "StatusUpdate"
const PATCH string = "Patch"
const DELETE string = "Delete"
const DELETEALLOF string = "DeleteAllOf"

const UNKNOWN string = "Unknown"

const NO_ERROR string = "NoError"

var config map[string]interface{} = nil
var configLoadingLock sync.Mutex
var triggerDefinitionsByResourceKey map[string][]map[string]interface{} = make(map[string][]map[string]interface{})
var triggerDefinitionsByAnnotatedAPI map[string][]map[string]interface{} = make(map[string][]map[string]interface{})
var actions map[string][]map[string]interface{} = make(map[string][]map[string]interface{})
var annotatedReconcileFunctions = make(map[string]interface{})
var sieveServerAddr string = ""
var crds []string

var apiserverHostname string = ""
var rpcClient *rpc.Client = nil

var exists = struct{}{}
var taintMap sync.Map = sync.Map{}

func printSerializationError(err error) {
	log.Printf("Sieve client serialization error: %v\n", err)
}

func printRPCError(err error) {
	log.Printf("Sieve client RPC error: %v\n", err)
}

func printConfigError(err error) {
	log.Printf("Sieve client configuration error: %v\n", err)
}

func HttpVerbToControllerOperation(verb, resourceName, subresource string) string {
	switch verb {
	case HTTP_GET:
		if resourceName == "" {
			return LIST
		} else {
			return GET
		}
	case HTTP_POST:
		return CREATE
	case HTTP_PUT:
		if subresource == "status" {
			return STATUSUPDATE
		} else {
			return UPDATE
		}
	case HTTP_PATCH:
		return PATCH
	case HTTP_DELETE:
		if resourceName == "" {
			return DELETEALLOF
		} else {
			return DELETE
		}
	default:
		return UNKNOWN
	}
}

func checkKVPairInAction(actionType, key, val string, matchPrefix bool) bool {
	for actionKey, actionsOfTheSameType := range actions {
		if actionKey == actionType {
			for _, action := range actionsOfTheSameType {
				if valInTestPlan, ok := action[key]; ok {
					if valInTestPlanStr, ok := valInTestPlan.(string); ok {
						if val == valInTestPlanStr {
							return true
						}
						if matchPrefix && strings.HasPrefix(valInTestPlanStr, val) {
							return true
						}
					}
				}
			}
		}
	}
	return false
}

func checkKVPairInAnnotatedAPICallTriggerCondition(apiKey string) bool {
	if _, ok := triggerDefinitionsByAnnotatedAPI[apiKey]; ok {
		return true
	} else {
		return false
	}
}

func checkKVPairInTriggerContent(content map[string]interface{}, key, val string) bool {
	if valInTestPlan, ok := content[key]; ok {
		if valInTestPlanStr, ok := valInTestPlan.(string); ok {
			if val == valInTestPlanStr {
				return true
			}
		}
	}
	return false
}

// Patterns opt in per definition; a literal '*' never changes existing schedules.
func triggerMatchesResource(definition map[string]interface{}, selector, resourceKey string, onlyMatchType bool) bool {
	if selector == resourceKey || (onlyMatchType && strings.HasPrefix(selector, resourceKey+"/")) {
		return true
	}
	condition, ok := definition["condition"].(map[string]interface{})
	if !ok {
		return false
	}
	enabled, _ := condition["resourceKeyPattern"].(bool)
	return enabled && strings.Count(resourceKey, "/") == 2 && strings.HasSuffix(selector, "*") && strings.HasPrefix(resourceKey, strings.TrimSuffix(selector, "*"))
}

func checkKVPairInTriggerCondition(resourceKey, key, val string, onlyMatchType bool) bool {
	for selector, triggers := range triggerDefinitionsByResourceKey {
		for _, trigger := range triggers {
			if !triggerMatchesResource(trigger, selector, resourceKey, onlyMatchType) {
				continue
			}
			if content, ok := trigger["condition"].(map[string]interface{}); ok && checkKVPairInTriggerContent(content, key, val) {
				return true
			}
		}
	}
	return false
}

func checkKVPairInTriggerObservationPoint(resourceKey, key, val string, onlyMatchType bool) bool {
	for selector, triggers := range triggerDefinitionsByResourceKey {
		for _, trigger := range triggers {
			if !triggerMatchesResource(trigger, selector, resourceKey, onlyMatchType) {
				continue
			}
			if content, ok := trigger["observationPoint"].(map[string]interface{}); ok && checkKVPairInTriggerContent(content, key, val) {
				return true
			}
		}
	}
	return false
}

func loadTriggerDefinitions(trigger map[string]interface{}) error {
	definitions, ok := trigger["definitions"].([]interface{})
	if !ok {
		return fmt.Errorf("cannot convert trigger[\"definitions\"] to []interface{}")
	}
	for idx, definitionRaw := range definitions {
		definition, ok := definitionRaw.(map[string]interface{})
		if !ok {
			return fmt.Errorf("cannot convert trigger[\"definitions\"][%d] to map[string]interface{}", idx)
		}
		conditionRaw, ok := definition["condition"]
		if !ok {
			return fmt.Errorf("cannot find condition in trigger[\"definitions\"][%d]", idx)
		}
		condition, ok := conditionRaw.(map[string]interface{})
		if !ok {
			return fmt.Errorf("cannot convert trigger[\"definitions\"][%d][\"condition\"] to map[string]interface{}", idx)
		}
		conditionTypeRaw, ok := condition["conditionType"]
		if !ok {
			return fmt.Errorf("cannot find conditionType in trigger[\"definitions\"][%d][\"condition\"]", idx)
		}
		conditionType, ok := conditionTypeRaw.(string)
		if !ok {
			return fmt.Errorf("cannot convert trigger[\"definitions\"][%d][\"condition\"] to string", idx)
		}
		if conditionType == "onTimeout" {
			continue
		} else if conditionType == "onAnnotatedAPICall" {
			receiverTypeRaw, ok := condition["receiverType"]
			if !ok {
				return fmt.Errorf("cannot find receiverType in trigger[\"definitions\"][%d][\"condition\"]", idx)
			}
			receiverType, ok := receiverTypeRaw.(string)
			if !ok {
				return fmt.Errorf("cannot convert trigger[\"definitions\"][%d][\"condition\"][\"receiverType\"] to string", idx)
			}
			funNameRaw, ok := condition["funName"]
			if !ok {
				return fmt.Errorf("cannot find funName in trigger[\"definitions\"][%d][\"condition\"]", idx)
			}
			funName, ok := funNameRaw.(string)
			if !ok {
				return fmt.Errorf("cannot convert trigger[\"definitions\"][%d][\"condition\"][\"funName\"] to string", idx)
			}
			apiKey := receiverType + funName
			if _, ok := triggerDefinitionsByAnnotatedAPI[apiKey]; !ok {
				triggerDefinitionsByAnnotatedAPI[apiKey] = []map[string]interface{}{}
			}
			triggerDefinitionsByAnnotatedAPI[apiKey] = append(triggerDefinitionsByAnnotatedAPI[apiKey], definition)
		} else {
			resourceKeyRaw, ok := condition["resourceKey"]
			if !ok {
				return fmt.Errorf("cannot find resourceKey in trigger[\"definitions\"][%d][\"condition\"]", idx)
			}
			resourceKey, ok := resourceKeyRaw.(string)
			if !ok {
				return fmt.Errorf("cannot convert trigger[\"definitions\"][%d][\"condition\"][\"resourceKey\"] to string", idx)
			}
			if _, ok := triggerDefinitionsByResourceKey[resourceKey]; !ok {
				triggerDefinitionsByResourceKey[resourceKey] = []map[string]interface{}{}
			}
			triggerDefinitionsByResourceKey[resourceKey] = append(triggerDefinitionsByResourceKey[resourceKey], definition)
		}
	}
	return nil
}

func loadActions(action map[string]interface{}) error {
	actionType, ok := action["actionType"].(string)
	if !ok {
		return fmt.Errorf("cannot convert action[\"actionType\"] to string")
	}
	if _, ok := actions[actionType]; !ok {
		actions[actionType] = []map[string]interface{}{}
	}
	actions[actionType] = append(actions[actionType], action)
	return nil
}

func loadSieveServerAddr(plan map[string]interface{}) error {
	if val, ok := plan["sieveServerAddr"]; ok {
		sieveServerAddr = val.(string)
	} else {
		sieveServerAddr = DEFAULT_SIEVE_SERVER_ADDR
	}
	return nil
}

func loadReconcileFuns(learnPlan map[string]interface{}) error {
	if cs, ok := learnPlan["annotatedReconcileStackFrame"]; ok {
		switch v := cs.(type) {
		case []interface{}:
			for _, c := range v {
				annotatedReconcileFunctions[c.(string)] = exists
			}
		case []string:
			for _, c := range v {
				annotatedReconcileFunctions[c] = exists
			}
		default:
			return fmt.Errorf("annotatedReconcileStackFrame wrong type")
		}
	} else {
		return fmt.Errorf("not find annotatedReconcileStackFrame from config")
	}
	return nil
}

func loadTestPlan(testPlan map[string]interface{}) error {
	if err := loadSieveServerAddr(testPlan); err != nil {
		return err
	}
	if err := loadReconcileFuns(testPlan); err != nil {
		return err
	}
	actions, ok := testPlan["actions"].([]interface{})
	if !ok {
		return fmt.Errorf("cannot convert testPlan[\"actions\"] to []interface{}")
	}
	for idx, val := range actions {
		action, ok := val.(map[string]interface{})
		err := loadActions(action)
		if err != nil {
			return nil
		}
		if !ok {
			return fmt.Errorf("cannot convert testPlan[\"actions\"][%d] to []interface{}", idx)
		}
		trigger, ok := action["trigger"].(map[string]interface{})
		if !ok {
			return fmt.Errorf("cannot convert testPlan[\"actions\"][%d][\"trigger\"] to []interface{}", idx)
		}
		err = loadTriggerDefinitions(trigger)
		if err != nil {
			return err
		}
	}
	log.Printf("triggerDefinitionsByResourceKey:\n%v\n", triggerDefinitionsByResourceKey)
	return nil
}

func loadCRDs(learnPlan map[string]interface{}) error {
	crds = []string{}
	if cs, ok := learnPlan["crdList"]; ok {
		switch v := cs.(type) {
		case []interface{}:
			for _, c := range v {
				crds = append(crds, c.(string))
			}
		case []string:
			crds = append(crds, v...)
		default:
			return fmt.Errorf("crdList wrong type")
		}
	} else {
		return fmt.Errorf("not find crdList from config")
	}
	return nil
}

func loadLearnPlan(learnPlan map[string]interface{}) error {
	if err := loadCRDs(learnPlan); err != nil {
		return err
	}
	if err := loadReconcileFuns(learnPlan); err != nil {
		return err
	}
	if err := loadSieveServerAddr(learnPlan); err != nil {
		return err
	}
	return nil
}

func loadSieveConfigFromEnv(testMode bool) error {
	if config != nil {
		return nil
	}
	configLoadingLock.Lock()
	defer configLoadingLock.Unlock()
	if config != nil {
		return nil
	}
	if _, ok := os.LookupEnv("sieveTestPlan"); ok {
		configFromEnv := make(map[string]interface{})
		data := os.Getenv("sieveTestPlan")
		err := json.Unmarshal([]byte(data), &configFromEnv)
		if err != nil {
			printSerializationError(err)
			return fmt.Errorf("fail to load from env")
		}
		log.Printf("config from env:\n%v\n", configFromEnv)
		config = configFromEnv
		if testMode {
			err = loadTestPlan(configFromEnv)
			if err != nil {
				printConfigError(err)
				log.Println("failure in loadTestPlan")
				return nil
			}
		} else {
			err = loadLearnPlan(configFromEnv)
			if err != nil {
				printConfigError(err)
				log.Println("failure in loadLearnPlan")
				return nil
			}
		}
	} else {
		return fmt.Errorf("fail to load from env")
	}
	return nil
}

func loadSieveConfigFromConfigMap(eventType, key string, object interface{}, testMode bool) error {
	if config != nil {
		return nil
	}
	if eventType == "ADDED" {
		tokens := strings.Split(key, "/")
		if len(tokens) < 4 {
			return fmt.Errorf("tokens len should be >= 4")
		}
		namespace := tokens[len(tokens)-2]
		name := tokens[len(tokens)-1]
		if namespace == "default" && name == "sieve-testing-global-config" {
			resourceType := getResourceTypeFromObj(object)
			if resourceType == "configmap" {
				log.Println("have seen ADDED configmap/default/sieve-testing-global-config")
				jsonObject, err := json.Marshal(object)
				if err != nil {
					printSerializationError(err)
					return fmt.Errorf("fail to load from configmap")
				}
				configMapObject := make(map[string]interface{})
				err = json.Unmarshal(jsonObject, &configMapObject)
				if err != nil {
					printSerializationError(err)
					return fmt.Errorf("fail to load from configmap")
				}
				configFromConfigMapData := make(map[string]interface{})
				configMapData, ok := configMapObject["data"].(map[string]interface{})
				if !ok {
					configMapData, ok = configMapObject["Data"].(map[string]interface{})
				}
				if !ok {
					log.Printf("cannot convert configMapObject[\"Data\"] to map[string]interface{}")
					return fmt.Errorf("fail to load from configmap")
				}
				if str, ok := configMapData["sieveTestPlan"].(string); ok {
					err = json.Unmarshal([]byte(str), &configFromConfigMapData)
					if err != nil {
						printSerializationError(err)
						return fmt.Errorf("fail to load from configmap")
					}
					log.Printf("config from configMap:\n%v\n", configFromConfigMapData)
					config = configFromConfigMapData
					if testMode {
						err = loadTestPlan(configFromConfigMapData)
						if err != nil {
							printConfigError(err)
							log.Println("failure in loadTestPlan")
							return nil
						}
					} else {
						err = loadLearnPlan(configFromConfigMapData)
						if err != nil {
							printConfigError(err)
							log.Println("failure in loadLearnPlan")
							return nil
						}
					}
				} else {
					log.Printf("cannot convert %v to string", configMapData["sieveTestPlan"])
					return fmt.Errorf("fail to load from configmap")
				}
			} else {
				return fmt.Errorf("have not seen ADDED configmap/default/sieve-testing-global-config yet")
			}
		} else {
			return fmt.Errorf("have not seen ADDED configmap/default/sieve-testing-global-config yet")
		}
	} else {
		return fmt.Errorf("have not seen ADDED configmap/default/sieve-testing-global-config yet")
	}
	return nil
}

func initAPIServerHostName() error {
	if apiserverHostname != "" {
		return nil
	}
	configLoadingLock.Lock()
	defer configLoadingLock.Unlock()
	if apiserverHostname != "" {
		return nil
	}
	var err error = nil
	apiserverHostname, err = os.Hostname()
	if err != nil {
		log.Printf("error in getting host name\n")
		return err
	}
	return nil
}

func initRPCClient() error {
	if rpcClient != nil {
		return nil
	}
	configLoadingLock.Lock()
	defer configLoadingLock.Unlock()
	if rpcClient != nil {
		return nil
	}
	var err error
	rpcServerAddr := sieveServerAddr
	rpcClient, err = rpc.Dial("tcp", rpcServerAddr)
	if err != nil {
		log.Printf("error in setting up connection to %s due to %v\n", rpcServerAddr, err)
		return err
	}
	return nil
}

func checkResponse(response Response) {
	if !response.Ok {
		log.Printf("Sieve client receives bad response: %s\n", response.Message)
	}
}

func generateResourceKey(resourceType, namespace, name string) string {
	return path.Join(resourceType, namespace, name)
}

// TODO: handle more complex plural cases
func pluralToSingular(plural string) string {
	if len(plural) == 0 {
		return plural
	} else {
		return plural[:len(plural)-1]
	}
}

func generateResourceKeyFromRestCall(verb, resourceType, namespace, name string, object interface{}) string {
	if verb == HTTP_POST {
		if o, ok := metadataForObject(object); ok {
			return generateResourceKey(pluralToSingular(resourceType), namespace, o.GetName())
		} else {
			return generateResourceKey(pluralToSingular(resourceType), namespace, name)
		}
	} else {
		return generateResourceKey(pluralToSingular(resourceType), namespace, name)
	}
}

func getResourceTypeFromObj(object interface{}) string {
	value := reflect.ValueOf(object)
	if !value.IsValid() || (value.Kind() == reflect.Ptr && value.IsNil()) {
		return ""
	}
	if kind, ok := object.(interface{ GetKind() string }); ok {
		if name := kind.GetKind(); name != "" {
			return strings.ToLower(name)
		}
	}
	if kind := runtimeObjectKind(value); kind != "" {
		return strings.ToLower(kind)
	}
	resourceType := reflect.TypeOf(object).String()
	tokens := strings.Split(resourceType, ".")
	return strings.ToLower(tokens[len(tokens)-1])
}

func extractNameNamespaceFromObj(object interface{}) (string, string) {
	if o, ok := metadataForObject(object); ok {
		return o.GetName(), o.GetNamespace()
	}
	return "", ""
}

func getMatchedReconcileStackFrame() string {
	// log.Println(string(debug.Stack()))
	for _, stackframe := range strings.Split(string(debug.Stack()), "\n") {
		if strings.HasPrefix(stackframe, "\t") {
			continue
		}
		for annotatedReconcileFun := range annotatedReconcileFunctions {
			if strings.HasPrefix(stackframe, annotatedReconcileFun+"(") {
				return annotatedReconcileFun
			}
		}
	}
	return UNKNOWN_RECONCILE_FUN
}

func getResourceNamespaceNameFromAPIKey(key string) (string, string, error) {
	namespace := ""
	name := ""
	tokens := strings.Split(key, "/")
	if len(tokens) < 4 {
		// log.Printf("unrecognizable key %s\n", key)
		return namespace, name, fmt.Errorf("tokens len should be >= 4")
	}
	namespace = tokens[len(tokens)-2]
	name = tokens[len(tokens)-1]
	return namespace, name, nil
}

func LogAPIEvent(eventType, key string, object interface{}) {
	namespace, name, err := getResourceNamespaceNameFromAPIKey(key)
	if err != nil {
		return
	}
	if namespace != "default" {
		return
	}
	jsonObject, err := json.Marshal(object)
	if err != nil {
		printSerializationError(err)
		return
	}
	resourceType := getResourceTypeFromObj(object)
	log.Printf("[SIEVE-API-EVENT]\t%s\t%s\t%s\t%s\t%s\t%s\n", eventType, key, resourceType, namespace, name, string(jsonObject))
}
