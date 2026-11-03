package main

import (
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"sync"

	sieve "sieve.client"
)

// Serialize coverage records at one observer, independently of fault triggers.
type testCoverage struct {
	sync.Mutex
	overlap   *deliveryOverlap
	resources map[string]bool
	active    map[string]string
	seen      map[string]bool
	receipts  map[int]sieve.NotifyTestCoverageRequest
	next      int
}

func newTestCoverage(resources []planText) *testCoverage {
	result := &testCoverage{resources: map[string]bool{}, active: map[string]string{}, seen: map[string]bool{}, receipts: map[int]sieve.NotifyTestCoverageRequest{}}
	for _, resource := range resources {
		result.resources[string(resource)] = true
	}
	return result
}

func (c *testCoverage) record(r *sieve.NotifyTestCoverageRequest, response *sieve.Response) error {
	c.Lock()
	err := c.recordLocked(r, response)
	if c.overlap != nil {
		if err != nil {
			c.overlap.Lock()
			c.overlap.failLocked(err.Error())
			c.overlap.Unlock()
		} else {
			err = c.overlap.observe(r)
		}
	}
	c.Unlock()
	if err == nil && c.overlap != nil && r.Kind == "read" {
		return c.overlap.waitRead(r)
	}
	return err
}

func (c *testCoverage) recordLocked(r *sieve.NotifyTestCoverageRequest, response *sieve.Response) error {
	fail := func(problem string) error {
		log.Printf("[SIEVE-TRACE-ERROR] test coverage: %s", problem)
		return fmt.Errorf("test coverage: %s", problem)
	}
	if len(c.resources) == 0 {
		return fail("not enabled")
	}
	key := r.Frame + "\t" + r.InvocationID
	if strings.ContainsAny(r.Frame+r.InvocationID+r.InstanceID+r.ResourceKey+r.Operation+r.Observer, "\t\r\n") {
		return fail("invalid identity")
	}
	if r.Kind == "begin" || r.Kind == "end" || r.Kind == "read" {
		if r.Frame == "" || r.InvocationID == "" || r.InstanceID == "" {
			return fail("missing invocation identity")
		}
	}
	if r.Kind == "read" || r.Kind == "receipt-begin" || r.Kind == "receipt-end" {
		var body map[string]interface{}
		if !c.resources[r.ResourceKey] || json.Unmarshal([]byte(r.Object), &body) != nil || body == nil || strings.ContainsAny(r.Object, "\r\n\t") {
			return fail("invalid or unscoped object")
		}
	}
	switch r.Kind {
	case "begin":
		if c.seen[key] {
			return fail("duplicate invocation")
		}
		c.seen[key] = true
		c.active[key] = r.InstanceID
		log.Printf("[SIEVE-BEFORE-RECONCILE]\t%s\t%s\tSIEVE-V2:%s", r.Frame, r.InvocationID, r.InstanceID)
	case "end":
		if c.active[key] != r.InstanceID {
			return fail("unmatched invocation end")
		}
		delete(c.active, key)
		log.Printf("[SIEVE-AFTER-RECONCILE]\t%s\t%s\tSIEVE-V2:%s", r.Frame, r.InvocationID, r.InstanceID)
	case "read":
		if c.active[key] != r.InstanceID {
			return fail("read outside invocation")
		}
		parts := strings.Split(r.ResourceKey, "/")
		log.Printf("[SIEVE-AFTER-CACHE-READ]\tGet\t%s\t%s\t%s\t%s\tNoError\t%s\tSIEVE-V2:%s", parts[0], parts[1], parts[2], r.Frame, r.Object, r.InvocationID)
	case "receipt-begin":
		if r.Observer != "informer" && r.Observer != "metadata-informer" {
			return fail("invalid receipt observer")
		}
		if r.Operation != "Added" && r.Operation != "Updated" && r.Operation != "Deleted" && r.Operation != "Replaced" && r.Operation != "Sync" {
			return fail("invalid receipt operation")
		}
		c.next++
		c.receipts[c.next] = *r
		response.Number = c.next
		observer := ""
		if r.Observer != "informer" {
			observer = "\t" + r.Observer
		}
		log.Printf("[SIEVE-BEFORE-HEAR]\t%d\t%s\t%s\t%s%s", c.next, r.Operation, strings.Split(r.ResourceKey, "/")[0], r.Object, observer)
	case "receipt-end":
		previous, ok := c.receipts[r.ReceiptID]
		if !ok || previous.ResourceKey != r.ResourceKey || previous.Object != r.Object || previous.Operation != r.Operation || previous.Observer != r.Observer {
			return fail("unmatched receipt end")
		}
		delete(c.receipts, r.ReceiptID)
		log.Printf("[SIEVE-AFTER-HEAR]\t%d", r.ReceiptID)
	default:
		return fail("invalid observation kind")
	}
	response.Ok = true
	return nil
}
