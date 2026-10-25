package main

import (
	"encoding/json"
	"log"
	"path"
	"strconv"
	"sync"
)

const (
	HEAR_ADDED          string = "Added"
	HEAR_UPDATED        string = "Updated"
	HEAR_DELETED        string = "Deleted"
	HEAR_REPLACED       string = "Replaced"
	HEAR_SYNC           string = "Sync"
	API_ADDED           string = "ADDED"
	API_MODIFIED        string = "MODIFIED"
	API_DELETED         string = "DELETED"
	WRITE_CREATE        string = "Create"
	WRITE_UPDATE        string = "Update"
	WRITE_DELETE        string = "Delete"
	WRITE_PATCH         string = "Patch"
	WRITE_STATUS_UPDATE string = "StatusUpdate"
	WRITE_STATUS_PATCH  string = "StatusPatch"
)

// observedWhen
const (
	beforeAPIServerRecv    string = "beforeAPIServerRecv"
	afterAPIServerRecv     string = "afterAPIServerRecv"
	beforeControllerRecv   string = "beforeControllerRecv"
	afterControllerRecv    string = "afterControllerRecv"
	beforeControllerWrite  string = "beforeControllerWrite"
	afterControllerWrite   string = "afterControllerWrite"
	beforeControllerRead   string = "beforeControllerRead"
	afterControllerRead    string = "afterControllerRead"
	beforeAnnotatedAPICall string = "beforeAnnotatedAPICall"
	afterAnnotatedAPICall  string = "afterAnnotatedAPICall"
)

// actionType
const (
	pauseAPIServer        string = "pauseAPIServer"
	resumeAPIServer       string = "resumeAPIServer"
	pauseController       string = "pauseController"
	resumeController      string = "resumeController"
	pauseControllerWrite  string = "pauseControllerWrite"
	resumeControllerWrite string = "resumeControllerWrite"
	restartController     string = "restartController"
	reconnectController   string = "reconnectController"
)

var mergedMaskLock sync.Mutex

func checkError(err error) {
	if err != nil {
		log.Fatalf("Fail due to error: %v\n", err)
	}
}

func strToMap(str string) map[string]interface{} {
	m := make(map[string]interface{})
	err := json.Unmarshal([]byte(str), &m)
	if err != nil {
		log.Fatalf("cannot unmarshal to map: %s\n", str)
	}
	return m
}

func strToSlice(str string) []map[string]interface{} {
	var s []map[string]interface{}
	err := json.Unmarshal([]byte(str), &s)
	if err != nil {
		log.Fatalf("cannot unmarshal to slice: %s\n", str)
	}
	return s
}

func strToInt(str string) int {
	i, err := strconv.Atoi(str)
	if err != nil {
		log.Fatalf("cannot conver to int: %s\n", str)
	}
	return i
}

func strToBool(str string) bool {
	b, err := strconv.ParseBool(str)
	if err != nil {
		log.Fatalf("cannot conver to bool: %s\n", str)
	}
	return b
}

func deepCopyMap(src map[string]interface{}) map[string]interface{} {
	dest := make(map[string]interface{})
	if src == nil {
		log.Fatalf("src is nil. You cannot read from a nil map")
	}
	jsonStr, err := json.Marshal(src)
	if err != nil {
		log.Fatal(err)
	}
	err = json.Unmarshal(jsonStr, &dest)
	if err != nil {
		log.Fatal(err)
	}
	return dest
}

func extractNameNamespaceFromObjMap(objMap map[string]interface{}) (string, string) {
	name := ""
	namespace := ""
	if _, ok := objMap["metadata"]; ok {
		if metadataMap, ok := objMap["metadata"].(map[string]interface{}); ok {
			if _, ok := metadataMap["name"]; ok {
				name = metadataMap["name"].(string)
			}
			if _, ok := metadataMap["namespace"]; ok {
				namespace = metadataMap["namespace"].(string)
			}
		}
	} else {
		if _, ok := objMap["name"]; ok {
			name = objMap["name"].(string)
		}
		if _, ok := objMap["namespace"]; ok {
			namespace = objMap["namespace"].(string)
		}
	}
	return name, namespace
}

func generateResourceKey(resourceType, namespace, name string) string {
	return path.Join(resourceType, namespace, name)
}

func isSameObjectServerSide(currentEvent map[string]interface{}, namespace string, name string) bool {
	extractedName, extractedNamespace := extractNameNamespaceFromObjMap(currentEvent)
	return extractedNamespace == namespace && extractedName == name
}
