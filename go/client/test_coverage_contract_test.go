package sieve

import (
	"encoding/json"
	"net"
	"net/rpc"
	"os"
	"os/exec"
	"reflect"
	"runtime"
	"sync"
	"testing"
)

type CoverageContractServer struct {
	sync.Mutex
	Records []NotifyTestCoverageRequest
	next    int
}

func (s *CoverageContractServer) NotifyTestCoverage(r *NotifyTestCoverageRequest, response *Response) error {
	s.Lock()
	defer s.Unlock()
	s.Records = append(s.Records, *r)
	response.Ok = true
	if r.Kind == "receipt-begin" {
		s.next++
		response.Number = s.next
	}
	return nil
}
func coverageWorker(frame string, instance *int, entered chan<- bool, release <-chan struct{}, done *sync.WaitGroup) {
	defer done.Done()
	NotifyTestBeforeReconcile(frame, instance)
	defer NotifyTestAfterReconcile(frame)
	obj := &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"name": "a", "namespace": "default", "uid": "a", "generation": 1}}}
	NotifyTestAfterCacheGet("default/a", obj, true, "")
	entered <- true
	<-release
}
func TestPublicTestCoverageHooksKeepTenWorkersScopedAndOptIn(t *testing.T) {
	if os.Getenv("SIEVE_TEST_COVERAGE_CHILD") == "" {
		for _, enabled := range []string{"enabled", "disabled"} {
			t.Run(enabled, func(t *testing.T) {
				cmd := exec.Command(os.Args[0], "-test.run=^TestPublicTestCoverageHooksKeepTenWorkersScopedAndOptIn$")
				cmd.Env = append(os.Environ(), "SIEVE_TEST_COVERAGE_CHILD="+enabled)
				if output, err := cmd.CombinedOutput(); err != nil {
					t.Fatalf("public hooks failed: %v\n%s", err, output)
				}
			})
		}
		return
	}
	recorder := &CoverageContractServer{}
	server := rpc.NewServer()
	if err := server.RegisterName("TestCoordinator", recorder); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		for {
			connection, err := listener.Accept()
			if err != nil {
				return
			}
			go server.ServeConn(connection)
		}
	}()
	frame := runtime.FuncForPC(reflect.ValueOf(coverageWorker).Pointer()).Name()
	plan := map[string]interface{}{"sieveServerAddr": listener.Addr().String(), "actions": []interface{}{}, "annotatedReconcileStackFrame": []string{frame}}
	enabled := os.Getenv("SIEVE_TEST_COVERAGE_CHILD") == "enabled"
	if enabled {
		plan["coverageResources"] = []string{"secret/default/a"}
	}
	payload, _ := json.Marshal(plan)
	t.Setenv("sieveTestPlan", string(payload))
	entered, release := make(chan bool, 10), make(chan struct{})
	var done sync.WaitGroup
	instances := make([]int, 10)
	for i := range instances {
		done.Add(1)
		go coverageWorker(frame, &instances[i], entered, release, &done)
	}
	for range instances {
		<-entered
	}
	newer := &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"name": "a", "namespace": "default", "uid": "a", "generation": 2}}}
	token := NotifyTestBeforeControllerRecv("Updated", newer)
	NotifyTestAfterControllerRecv(token, "Updated", newer)
	unrelated := &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"name": "other", "namespace": "default"}}}
	token = NotifyTestBeforeControllerRecv("Updated", unrelated)
	NotifyTestAfterControllerRecv(token, "Updated", unrelated)
	close(release)
	done.Wait()
	recorder.Lock()
	defer recorder.Unlock()
	if !enabled {
		if len(recorder.Records) != 0 {
			t.Fatal("historical plan emitted coverage")
		}
		return
	}
	begins := map[string]NotifyTestCoverageRequest{}
	ends := 0
	reads := 0
	receiptBegin, receiptEnd := 0, 0
	for _, r := range recorder.Records {
		switch r.Kind {
		case "begin":
			if r.InvocationID == "" || r.InstanceID == "" || begins[r.InvocationID].Kind != "" {
				t.Fatal("missing/duplicate identity")
			}
			begins[r.InvocationID] = r
		case "read":
			if begins[r.InvocationID].Frame != frame || r.ResourceKey != "secret/default/a" {
				t.Fatalf("misattributed read %+v", r)
			}
			reads++
		case "end":
			if begins[r.InvocationID].InstanceID != r.InstanceID {
				t.Fatal("wrong instance end")
			}
			ends++
		case "receipt-begin":
			if r.ResourceKey != "secret/default/a" {
				t.Fatal("unscoped receipt")
			}
			receiptBegin++
		case "receipt-end":
			if r.ReceiptID <= 0 {
				t.Fatal("unpaired receipt")
			}
			receiptEnd++
		}
	}
	if len(begins) != 10 || ends != 10 || reads != 10 || receiptBegin != 1 || receiptEnd != 1 {
		t.Fatalf("coverage lost: begins=%d ends=%d reads=%d receipts=%d/%d", len(begins), ends, reads, receiptBegin, receiptEnd)
	}
}
