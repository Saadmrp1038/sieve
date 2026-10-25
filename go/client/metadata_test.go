package sieve

import (
	"encoding/json"
	"fmt"
	"testing"
)

type testObject struct{ Object map[string]interface{} }

func (o *testObject) GetKind() string { return o.Object["kind"].(string) }
func (o *testObject) GetName() string {
	return o.Object["metadata"].(map[string]interface{})["name"].(string)
}
func (o *testObject) GetNamespace() string {
	return o.Object["metadata"].(map[string]interface{})["namespace"].(string)
}
func (o *testObject) GetOwnerReferences() []ownerReference {
	owners, _ := o.Object["owners"].([]ownerReference)
	return owners
}
func (o *testObject) MarshalJSON() ([]byte, error) { return json.Marshal(o.Object) }

type testMetadataWrapper struct{ Object *testObject }

func (w *testMetadataWrapper) GetObjectMeta() *testObject { return w.Object }

type testStatus struct{ Reason string }
type testAPIError struct{ Reason string }

func (e *testAPIError) Error() string      { return e.Reason }
func (e *testAPIError) Status() testStatus { return testStatus{Reason: e.Reason} }

func TestMetadataWithoutKubernetesDependency(t *testing.T) {
	object := &testObject{Object: map[string]interface{}{"kind": "StringSecret", "metadata": map[string]interface{}{"name": "example", "namespace": "default"}}}
	for _, value := range []interface{}{object, &testMetadataWrapper{Object: object}} {
		name, namespace := extractNameNamespaceFromObj(value)
		if name != "example" || namespace != "default" {
			t.Fatalf("metadata: %s/%s", namespace, name)
		}
	}
	var missing *testObject
	for _, value := range []interface{}{nil, missing, 1} {
		if _, ok := metadataForObject(value); ok {
			t.Fatalf("invalid metadata object: %v", value)
		}
	}
	if getResourceTypeFromObj(object) != "stringsecret" {
		t.Fatal("unstructured kind was lost")
	}
	if got := generateResourceKeyFromRestCall(HTTP_POST, "stringsecrets", "default", "", object); got != "stringsecret/default/example" {
		t.Fatal(got)
	}
}

func TestOwnershipTaintAndAPIErrorReason(t *testing.T) {
	previousCRDs := crds
	crds = []string{"stringsecret"}
	defer func() { crds = previousCRDs }()
	defer taintMap.Delete("default-secret-child")
	defer taintMap.Delete("default-pod-grandchild")
	child := &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"name": "child", "namespace": "default"}, "owners": []ownerReference{{Kind: "StringSecret", Name: "parent"}}}}
	grandchild := &testObject{Object: map[string]interface{}{"kind": "Pod", "metadata": map[string]interface{}{"name": "grandchild", "namespace": "default"}, "owners": []ownerReference{{Kind: "Secret", Name: "child"}}}}
	if !triggerReconcile(child) || !triggerReconcile(grandchild) {
		t.Fatal("owner taint chain lost")
	}
	if reasonForError(&testAPIError{Reason: "NotFound"}) != "NotFound" {
		t.Fatal("API reason lost")
	}
	if reasonForError(fmt.Errorf("wrapped: %w", &testAPIError{Reason: "Conflict"})) != "Conflict" {
		t.Fatal("wrapped API reason lost")
	}
	if reasonForError(fmt.Errorf("ordinary")) != "Unknown" {
		t.Fatal("ordinary error misclassified")
	}
}
