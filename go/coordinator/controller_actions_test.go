package main

import (
	"context"
	"errors"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/kubernetes/fake"
	ktesting "k8s.io/client-go/testing"
)

func controllerPod(ready bool) *corev1.Pod {
	status := corev1.ConditionFalse
	if ready {
		status = corev1.ConditionTrue
	}
	return &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Name: "operator", Namespace: "default", Labels: map[string]string{"sievetag": "operator"}}, Status: corev1.PodStatus{Phase: corev1.PodRunning, Conditions: []corev1.PodCondition{{Type: corev1.PodReady, Status: status}}}}
}

func TestRunningPodIsNotReady(t *testing.T) {
	successContext, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	client := fake.NewSimpleClientset(controllerPod(false))
	actions := controllerActions{client: client, namespace: "default", pollInterval: time.Millisecond}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := actions.waitForPods(ctx, "sievetag=operator", podsReady); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("accepted unready pod: %v", err)
	}
	// Reconnection during an intentional API pause uses the earlier Running
	// milestone; waiting for cache readiness would prevent the resume action.
	if err := actions.waitForPods(successContext, "sievetag=operator", podsRunning); err != nil {
		t.Fatal(err)
	}
	pod := controllerPod(true)
	if _, err := client.CoreV1().Pods("default").Update(context.Background(), pod, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	if err := actions.waitForPods(successContext, "sievetag=operator", podsReady); err != nil {
		t.Fatal(err)
	}
}

func TestPodPollingCancelsAndPreservesAPIErrors(t *testing.T) {
	successContext, stop := context.WithTimeout(context.Background(), time.Second)
	defer stop()
	client := fake.NewSimpleClientset()
	actions := controllerActions{client: client, namespace: "default", pollInterval: time.Millisecond}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := actions.waitForPods(ctx, "", podsReady); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	sentinel := errors.New("API unavailable")
	client.PrependReactor("list", "pods", func(ktesting.Action) (bool, runtime.Object, error) { return true, nil, sentinel })
	if err := actions.waitForPods(successContext, "", podsDeleted); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
}

func TestRestartPreservesOwnerSpecAndOnlyRedirectsTheRequestedHost(t *testing.T) {
	for _, kind := range []string{"Deployment", "StatefulSet"} {
		for _, followingAPI := range []string{"", "kind-control-plane3"} {
			t.Run(kind+"/"+followingAPI, func(t *testing.T) {
				controlled := true
				pod := controllerPod(true)
				ownerKind, ownerName := kind, "operator"
				if kind == "Deployment" {
					ownerKind = "ReplicaSet"
					ownerName = "operator-rs"
				}
				pod.OwnerReferences = []metav1.OwnerReference{{Kind: ownerKind, Name: ownerName, Controller: &controlled}}
				template := corev1.PodTemplateSpec{Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: "manager", Image: "pinned/operator", Env: []corev1.EnvVar{{Name: "KUBERNETES_SERVICE_HOST", Value: "kind-control-plane"}, {Name: "OTHER", Value: "unchanged"}}}}}}
				deployment := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: "operator", Namespace: "default", Labels: map[string]string{"owned": "label"}}, Spec: appsv1.DeploymentSpec{Template: template}}
				statefulSet := &appsv1.StatefulSet{ObjectMeta: deployment.ObjectMeta, Spec: appsv1.StatefulSetSpec{Template: template, ServiceName: "controller"}}
				rs := &appsv1.ReplicaSet{ObjectMeta: metav1.ObjectMeta{Name: "operator-rs", Namespace: "default", OwnerReferences: []metav1.OwnerReference{{Kind: "Deployment", Name: "operator", Controller: &controlled}}}}
				client := fake.NewSimpleClientset(pod, deployment, statefulSet, rs)
				resource := "deployments"
				if kind == "StatefulSet" {
					resource = "statefulsets"
				}
				client.PrependReactor("delete", resource, func(ktesting.Action) (bool, runtime.Object, error) {
					err := client.Tracker().Delete(schema.GroupVersionResource{Version: "v1", Resource: "pods"}, "default", "operator")
					return false, nil, err
				})
				client.PrependReactor("create", resource, func(ktesting.Action) (bool, runtime.Object, error) {
					ready := followingAPI == ""
					err := client.Tracker().Create(schema.GroupVersionResource{Version: "v1", Resource: "pods"}, controllerPod(ready), "default")
					return false, nil, err
				})
				executor := controllerActions{client: client, namespace: "default", operationTimeout: time.Second, pollInterval: time.Millisecond}
				if err := executor.restart(context.Background(), "operator", followingAPI); err != nil {
					t.Fatal(err)
				}
				expected := template.DeepCopy()
				if followingAPI != "" {
					expected.Spec.Containers[0].Env[0].Value = followingAPI
				}
				if kind == "Deployment" {
					actual, err := client.AppsV1().Deployments("default").Get(context.Background(), "operator", metav1.GetOptions{})
					if err != nil {
						t.Fatal(err)
					}
					if !reflect.DeepEqual(actual.Spec.Template, *expected) {
						t.Fatalf("changed deployment spec: %#v", actual.Spec)
					}
				} else {
					actual, err := client.AppsV1().StatefulSets("default").Get(context.Background(), "operator", metav1.GetOptions{})
					if err != nil {
						t.Fatal(err)
					}
					if actual.Spec.ServiceName != "controller" || !reflect.DeepEqual(actual.Spec.Template, *expected) {
						t.Fatalf("changed StatefulSet spec: %#v", actual.Spec)
					}
				}
			})
		}
	}
}

func TestRestartStopsOnDeletionFailure(t *testing.T) {
	pod := controllerPod(true)
	controlled := true
	pod.OwnerReferences = []metav1.OwnerReference{{Kind: "StatefulSet", Name: "operator", Controller: &controlled}}
	statefulSet := &appsv1.StatefulSet{ObjectMeta: metav1.ObjectMeta{Name: "operator", Namespace: "default"}}
	client := fake.NewSimpleClientset(pod, statefulSet)
	sentinel := errors.New("delete denied")
	client.PrependReactor("delete", "statefulsets", func(ktesting.Action) (bool, runtime.Object, error) { return true, nil, sentinel })
	executor := controllerActions{client: client, namespace: "default", operationTimeout: time.Second, pollInterval: time.Millisecond}
	if err := executor.restart(context.Background(), "operator", ""); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	for _, action := range client.Actions() {
		if action.GetVerb() == "create" {
			t.Fatal("recreated after failed deletion")
		}
	}
}

func TestHungKubernetesRequestHonorsActionDeadline(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	defer server.Close()
	client, err := kubernetes.NewForConfig(&rest.Config{Host: server.URL, Timeout: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	executor := controllerActions{client: client, namespace: "default", operationTimeout: 20 * time.Millisecond, pollInterval: time.Millisecond}
	started := time.Now()
	if err := executor.restart(context.Background(), "operator", ""); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	if time.Since(started) > time.Second {
		t.Fatal("request ignored action deadline")
	}
}

func TestMissingControllerOwnershipReturnsAnError(t *testing.T) {
	for _, objects := range [][]runtime.Object{nil, {controllerPod(true)}} {
		client := fake.NewSimpleClientset(objects...)
		executor := controllerActions{client: client, namespace: "default", operationTimeout: time.Second, pollInterval: time.Millisecond}
		if err := executor.restart(context.Background(), "operator", ""); err == nil {
			t.Fatal("accepted missing controller ownership")
		}
	}
}
