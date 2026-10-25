package main

import (
	"context"
	"fmt"
	"log"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/util/wait"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

type controllerActions struct {
	client           kubernetes.Interface
	namespace        string
	operationTimeout time.Duration
	pollInterval     time.Duration
}

func newControllerActions(namespace, api string, requestTimeout, operationTimeout time.Duration) (*controllerActions, error) {
	config, err := clientcmd.BuildConfigFromFlags("https://"+api+":6443", "/root/.kube/config")
	if err != nil {
		return nil, fmt.Errorf("load coordinator Kubernetes configuration: %w", err)
	}
	config.Timeout = requestTimeout
	client, err := kubernetes.NewForConfig(config)
	if err != nil {
		return nil, fmt.Errorf("create coordinator Kubernetes client: %w", err)
	}
	return &controllerActions{client: client, namespace: namespace, operationTimeout: operationTimeout, pollInterval: time.Second}, nil
}

type podMilestone int

const (
	podsDeleted podMilestone = iota
	podsRunning
	podsReady
)

func (a *controllerActions) waitForPods(ctx context.Context, selector string, milestone podMilestone) error {
	return wait.PollUntilContextCancel(ctx, a.pollInterval, false, func(ctx context.Context) (bool, error) {
		pods, err := a.client.CoreV1().Pods(a.namespace).List(ctx, metav1.ListOptions{LabelSelector: selector})
		if err != nil {
			return false, fmt.Errorf("list controller pods: %w", err)
		}
		if milestone == podsDeleted {
			if len(pods.Items) == 0 {
				log.Println("controller pod gets deleted")
				return true, nil
			}
			log.Println("controller pod not deleted yet")
			return false, nil
		}
		if len(pods.Items) == 0 {
			log.Println("controller pod not created yet")
			return false, nil
		}
		ready := true
		for i := range pods.Items {
			pod := &pods.Items[i]
			podReady := milestone == podsRunning
			for _, condition := range pod.Status.Conditions {
				if milestone == podsReady && condition.Type == corev1.PodReady && condition.Status == corev1.ConditionTrue {
					podReady = true
				}
			}
			if pod.Status.Phase != corev1.PodRunning || pod.DeletionTimestamp != nil {
				podReady = false
			}
			if podReady {
				if milestone == podsReady {
					log.Printf("controller pod %d ready now", i)
				} else {
					log.Printf("controller pod %d running now", i)
				}
			} else {
				log.Printf("controller pod %d not ready yet", i)
				ready = false
			}
		}
		return ready, nil
	})
}

func redirectContainers(containers []corev1.Container, followingAPI string) {
	if followingAPI == "" {
		return
	}
	for i := range containers {
		for j := range containers[i].Env {
			if containers[i].Env[j].Name == "KUBERNETES_SERVICE_HOST" {
				containers[i].Env[j].Value = followingAPI
				break
			}
		}
	}
}

func (a *controllerActions) restart(ctx context.Context, controllerLabel, followingAPI string) error {
	ctx, cancel := context.WithTimeout(ctx, a.operationTimeout)
	defer cancel()
	selector := labels.Set{"sievetag": controllerLabel}.String()
	pods, err := a.client.CoreV1().Pods(a.namespace).List(ctx, metav1.ListOptions{LabelSelector: selector})
	if err != nil {
		return fmt.Errorf("find controller pod: %w", err)
	}
	if len(pods.Items) == 0 {
		return fmt.Errorf("no controller pod matches %q", selector)
	}
	owner := metav1.GetControllerOf(&pods.Items[0])
	if owner == nil {
		return fmt.Errorf("controller pod %s has no controlling owner", pods.Items[0].Name)
	}
	if owner.Kind == "ReplicaSet" {
		replicaSet, err := a.client.AppsV1().ReplicaSets(a.namespace).Get(ctx, owner.Name, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("get controller ReplicaSet: %w", err)
		}
		owner = metav1.GetControllerOf(replicaSet)
		if owner == nil || owner.Kind != "Deployment" {
			return fmt.Errorf("controller ReplicaSet must be owned by a Deployment")
		}
	}
	// Keep the existing fault mechanism: delete the owner, wait for all selected
	// pods to disappear, then recreate the same spec (optionally redirected).
	switch owner.Kind {
	case "Deployment":
		deployments := a.client.AppsV1().Deployments(a.namespace)
		original, err := deployments.Get(ctx, owner.Name, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("get controller Deployment: %w", err)
		}
		if err := deployments.Delete(ctx, owner.Name, metav1.DeleteOptions{}); err != nil {
			return fmt.Errorf("delete controller Deployment: %w", err)
		}
		if err := a.waitForPods(ctx, selector, podsDeleted); err != nil {
			return fmt.Errorf("wait for controller deletion: %w", err)
		}
		replacement := &appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: original.Name, Namespace: original.Namespace, Labels: original.Labels}, Spec: *original.Spec.DeepCopy()}
		redirectContainers(replacement.Spec.Template.Spec.Containers, followingAPI)
		if _, err := deployments.Create(ctx, replacement, metav1.CreateOptions{}); err != nil {
			return fmt.Errorf("recreate controller Deployment: %w", err)
		}
	case "StatefulSet":
		statefulSets := a.client.AppsV1().StatefulSets(a.namespace)
		original, err := statefulSets.Get(ctx, owner.Name, metav1.GetOptions{})
		if err != nil {
			return fmt.Errorf("get controller StatefulSet: %w", err)
		}
		if err := statefulSets.Delete(ctx, owner.Name, metav1.DeleteOptions{}); err != nil {
			return fmt.Errorf("delete controller StatefulSet: %w", err)
		}
		if err := a.waitForPods(ctx, selector, podsDeleted); err != nil {
			return fmt.Errorf("wait for controller deletion: %w", err)
		}
		replacement := &appsv1.StatefulSet{ObjectMeta: metav1.ObjectMeta{Name: original.Name, Namespace: original.Namespace, Labels: original.Labels}, Spec: *original.Spec.DeepCopy()}
		redirectContainers(replacement.Spec.Template.Spec.Containers, followingAPI)
		if _, err := statefulSets.Create(ctx, replacement, metav1.CreateOptions{}); err != nil {
			return fmt.Errorf("recreate controller StatefulSet: %w", err)
		}
	default:
		return fmt.Errorf("unsupported controller owner %q", owner.Kind)
	}
	milestone := podsReady
	if followingAPI != "" {
		// A stale-state schedule resumes the paused API only after reconnect
		// completes. Requiring cache readiness here would deadlock that schedule.
		milestone = podsRunning
	}
	if err := a.waitForPods(ctx, selector, milestone); err != nil {
		return fmt.Errorf("wait for controller readiness: %w", err)
	}
	return nil
}
