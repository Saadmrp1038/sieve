package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	sieve "sieve.client"
	"strings"
	"sync"
	"time"
)

type DeliveryOverlapRequest struct {
	WaitFor       string
	TimeoutMillis int
}
type DeliveryOverlapStatus struct {
	Mode                    string `json:"mode"`
	State                   string `json:"state"`
	ResourceKey             string `json:"resourceKey"`
	Reconciler              string `json:"reconciler"`
	InvocationID            string `json:"invocationID"`
	InstanceID              string `json:"instanceID"`
	UID                     string `json:"uid"`
	Generation              int64  `json:"generation"`
	ResourceVersion         string `json:"resourceVersion"`
	ReleasedGeneration      int64  `json:"releasedGeneration"`
	ReleasedResourceVersion string `json:"releasedResourceVersion"`
	Error                   string `json:"error,omitempty"`
}
type overlapObject struct {
	Spec     map[string]json.RawMessage `json:"spec"`
	Metadata struct {
		Name            string            `json:"name"`
		Namespace       string            `json:"namespace"`
		UID             string            `json:"uid"`
		Generation      int64             `json:"generation"`
		ResourceVersion string            `json:"resourceVersion"`
		Labels          map[string]string `json:"labels"`
	} `json:"metadata"`
}
type deliveryOverlap struct {
	sync.Mutex
	spec                           DeliveryOverlapSpec
	status                         DeliveryOverlapStatus
	changed                        chan struct{}
	released                       chan struct{}
	finished                       chan struct{}
	releasedClosed, finishedClosed bool
	timer                          *time.Timer
}

func newDeliveryOverlap(ctx context.Context, spec *DeliveryOverlapSpec) *deliveryOverlap {
	if spec == nil {
		return nil
	}
	d := &deliveryOverlap{spec: *spec, changed: make(chan struct{}), released: make(chan struct{}), finished: make(chan struct{}), status: DeliveryOverlapStatus{Mode: string(spec.Mode), State: "armed", ResourceKey: string(spec.ResourceKey), Reconciler: string(spec.Reconciler)}}
	if spec.Mode == "observe" {
		d.status.State = "observing"
		return d
	}
	go func() {
		select {
		case <-ctx.Done():
			d.Lock()
			if d.status.State != "completed" && d.status.State != "failed" {
				d.failLocked("coordinator cancelled")
			}
			d.Unlock()
		case <-d.finished:
		}
	}()
	return d
}
func (d *deliveryOverlap) updateLocked() {
	body, _ := json.Marshal(d.status)
	log.Printf("[SIEVE-DELIVERY-OVERLAP]\t%s", body)
	close(d.changed)
	d.changed = make(chan struct{})
}
func (d *deliveryOverlap) finishLocked() {
	if d.timer != nil {
		d.timer.Stop()
	}
	if !d.finishedClosed {
		close(d.finished)
		d.finishedClosed = true
	}
}
func (d *deliveryOverlap) releaseLocked() {
	if !d.releasedClosed {
		close(d.released)
		d.releasedClosed = true
	}
}
func (d *deliveryOverlap) failLocked(reason string) error {
	if d.status.State != "failed" {
		d.status.State = "failed"
		d.status.Error = reason
		d.releaseLocked()
		d.finishLocked()
		d.updateLocked()
		log.Printf("[SIEVE-TRACE-ERROR] delivery overlap: %s", reason)
	}
	return fmt.Errorf("delivery overlap: %s", d.status.Error)
}
func (d *deliveryOverlap) observe(r *sieve.NotifyTestCoverageRequest) error {
	d.Lock()
	defer d.Unlock()
	if d.status.Mode != "hold" {
		return nil
	}
	if r.Kind == "read" && d.status.State == "armed" && r.ResourceKey == string(d.spec.ResourceKey) && r.Frame == string(d.spec.Reconciler) {
		var obj overlapObject
		if err := json.Unmarshal([]byte(r.Object), &obj); err != nil {
			return d.failLocked("invalid held input")
		}
		if obj.Metadata.Labels["sieve.testing/state"] != string(d.spec.HoldPhase) {
			return nil
		}
		if obj.Spec == nil {
			return nil
		}
		parts := strings.Split(r.ResourceKey, "/")
		m := obj.Metadata
		if m.UID == "" || m.Generation < 1 || m.ResourceVersion == "" || m.Name != parts[2] || m.Namespace != parts[1] {
			return d.failLocked("invalid held input identity")
		}
		d.status.State = "held"
		d.status.InvocationID = r.InvocationID
		d.status.InstanceID = r.InstanceID
		d.status.UID = m.UID
		d.status.Generation = m.Generation
		d.status.ResourceVersion = m.ResourceVersion
		d.timer = time.AfterFunc(time.Duration(d.spec.TimeoutSeconds)*time.Second, func() {
			d.Lock()
			defer d.Unlock()
			if d.status.State == "held" || d.status.State == "released" {
				d.failLocked("held invocation deadline exceeded")
			}
		})
		d.updateLocked()
	}
	if r.Kind == "receipt-end" && d.status.State == "held" && r.ResourceKey == d.status.ResourceKey && r.Operation == "Updated" && r.Observer == "informer" {
		var obj overlapObject
		if err := json.Unmarshal([]byte(r.Object), &obj); err != nil {
			return d.failLocked("invalid release input")
		}
		m := obj.Metadata
		parts := strings.Split(r.ResourceKey, "/")
		if obj.Spec == nil {
			return nil
		}
		if m.UID == d.status.UID && m.Name == parts[2] && m.Namespace == parts[1] && m.Generation > d.status.Generation && m.Generation-d.status.Generation == 1 && m.ResourceVersion != "" && m.ResourceVersion != d.status.ResourceVersion && m.Labels["sieve.testing/state"] == string(d.spec.ReleasePhase) {
			d.status.State = "released"
			d.status.ReleasedGeneration = m.Generation
			d.status.ReleasedResourceVersion = m.ResourceVersion
			d.updateLocked()
			d.releaseLocked()
		}
	}
	if r.Kind == "end" && r.Frame == d.status.Reconciler && r.InvocationID == d.status.InvocationID && r.InstanceID == d.status.InstanceID {
		if d.status.State == "held" {
			return d.failLocked("held invocation ended before release")
		}
		if d.status.State == "released" {
			d.status.State = "completed"
			d.finishLocked()
			d.updateLocked()
		}
	}
	return nil
}
func (d *deliveryOverlap) waitRead(r *sieve.NotifyTestCoverageRequest) error {
	d.Lock()
	selected := r.Frame == d.status.Reconciler && r.InvocationID == d.status.InvocationID && r.InstanceID == d.status.InstanceID
	released := d.released
	d.Unlock()
	if !selected {
		return nil
	}
	<-released
	d.Lock()
	defer d.Unlock()
	if d.status.State == "failed" {
		return fmt.Errorf("delivery overlap: %s", d.status.Error)
	}
	return nil
}
func (d *deliveryOverlap) query(r *DeliveryOverlapRequest, out *DeliveryOverlapStatus) error {
	if r.WaitFor != "status" && r.WaitFor != "held" && r.WaitFor != "completed" {
		return fmt.Errorf("invalid overlap wait condition")
	}
	if r.WaitFor != "status" && (r.TimeoutMillis < 1 || r.TimeoutMillis > 60000) {
		return fmt.Errorf("overlap wait requires 1–60000ms timeout")
	}
	timer := time.NewTimer(time.Duration(r.TimeoutMillis) * time.Millisecond)
	defer timer.Stop()
	for {
		d.Lock()
		*out = d.status
		if out.State == "failed" {
			err := fmt.Errorf("delivery overlap: %s", out.Error)
			d.Unlock()
			return err
		}
		ready := r.WaitFor == "status" || out.Mode == "observe" || r.WaitFor == "held" && (out.State == "held" || out.State == "released" || out.State == "completed") || r.WaitFor == "completed" && out.State == "completed"
		changed := d.changed
		d.Unlock()
		if ready {
			return nil
		}
		select {
		case <-changed:
		case <-timer.C:
			d.Lock()
			err := d.failLocked("workload wait deadline exceeded")
			d.Unlock()
			return err
		}
	}
}
