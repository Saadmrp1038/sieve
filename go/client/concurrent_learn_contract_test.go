package sieve

import (
	"fmt"
	"net"
	"net/rpc"
	"os"
	"reflect"
	"runtime"
	"sync"
	"testing"
)

type ConcurrentLearnServer struct {
	begins chan NotifyLearnBeforeReconcileRequest
	reads  chan NotifyLearnAfterCacheGetRequest
	ends   chan NotifyLearnAfterReconcileRequest
}

func coldLearnWorker(frame string, instance *int, start <-chan struct{}, done *sync.WaitGroup) {
	defer done.Done()
	<-start
	NotifyLearnBeforeReconcile(frame, instance)
	NotifyLearnAfterReconcile(frame)
}

func TestConcurrentLearnHooksInitializeOneConsistentObserver(t *testing.T) {
	previousConfig, previousClient, previousFrames, previousAddress, previousCRDs := config, rpcClient, annotatedReconcileFunctions, sieveServerAddr, crds
	defer func() {
		config, rpcClient, annotatedReconcileFunctions, sieveServerAddr, crds = previousConfig, previousClient, previousFrames, previousAddress, previousCRDs
	}()
	config, rpcClient = nil, nil
	frame := runtime.FuncForPC(reflect.ValueOf(coldLearnWorker).Pointer()).Name()
	handler := &ConcurrentLearnServer{make(chan NotifyLearnBeforeReconcileRequest, 32), make(chan NotifyLearnAfterCacheGetRequest, 32), make(chan NotifyLearnAfterReconcileRequest, 32)}
	server := rpc.NewServer()
	if err := server.RegisterName("LearnListener", handler); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		for {
			connection, e := listener.Accept()
			if e != nil {
				return
			}
			go server.ServeConn(connection)
		}
	}()
	previousEnv, present := os.LookupEnv("sieveTestPlan")
	defer func() {
		if present {
			os.Setenv("sieveTestPlan", previousEnv)
		} else {
			os.Unsetenv("sieveTestPlan")
		}
	}()
	os.Setenv("sieveTestPlan", fmt.Sprintf(`{"sieveServerAddr":%q,"annotatedReconcileStackFrame":[%q],"crdList":[],"rateLimiterEnabled":false}`, listener.Addr().String(), frame))
	start := make(chan struct{})
	var done sync.WaitGroup
	instances := make([]int, 32)
	for i := range instances {
		done.Add(1)
		go coldLearnWorker(frame, &instances[i], start, &done)
	}
	close(start)
	done.Wait()
	if rpcClient != nil {
		rpcClient.Close()
	}
	if len(handler.begins) != 32 || len(handler.ends) != 32 {
		t.Fatalf("lost cold-start invocations: %d/%d", len(handler.begins), len(handler.ends))
	}
}

func (s *ConcurrentLearnServer) NotifyLearnBeforeReconcile(r *NotifyLearnBeforeReconcileRequest, response *Response) error {
	s.begins <- *r
	response.Ok = true
	return nil
}
func (s *ConcurrentLearnServer) NotifyLearnAfterCacheGet(r *NotifyLearnAfterCacheGetRequest, response *Response) error {
	s.reads <- *r
	response.Ok = true
	return nil
}
func (s *ConcurrentLearnServer) NotifyLearnAfterReconcile(r *NotifyLearnAfterReconcileRequest, response *Response) error {
	s.ends <- *r
	response.Ok = true
	return nil
}

func concurrentLearnWorker(frame string, instance *int, key string, entered chan<- string, release <-chan struct{}, done chan<- struct{}) {
	NotifyLearnBeforeReconcile(frame, instance)
	entered <- key
	<-release
	NotifyLearnAfterCacheGet("default/"+key, &testObject{Object: map[string]interface{}{"kind": "Secret", "metadata": map[string]interface{}{"name": key, "namespace": "default"}}}, true)
	NotifyLearnAfterReconcile(frame)
	done <- struct{}{}
}

func TestConcurrentLearnHooksAttributeReadsToTheirOwnInvocation(t *testing.T) {
	previousConfig, previousClient, previousFrames := config, rpcClient, annotatedReconcileFunctions
	defer func() {
		config, rpcClient, annotatedReconcileFunctions = previousConfig, previousClient, previousFrames
	}()
	config = map[string]interface{}{}
	frame := runtime.FuncForPC(reflect.ValueOf(concurrentLearnWorker).Pointer()).Name()
	annotatedReconcileFunctions = map[string]interface{}{frame: true}
	handler := &ConcurrentLearnServer{make(chan NotifyLearnBeforeReconcileRequest, 2), make(chan NotifyLearnAfterCacheGetRequest, 2), make(chan NotifyLearnAfterReconcileRequest, 2)}
	server := rpc.NewServer()
	if err := server.RegisterName("LearnListener", handler); err != nil {
		t.Fatal(err)
	}
	local, remote := net.Pipe()
	go server.ServeConn(remote)
	rpcClient = rpc.NewClient(local)
	defer rpcClient.Close()
	entered, done := make(chan string, 2), make(chan struct{}, 2)
	a, b := 1, 2
	releaseA, releaseB := make(chan struct{}), make(chan struct{})
	go concurrentLearnWorker(frame, &a, "a", entered, releaseA, done)
	<-entered
	beginA := <-handler.begins
	go concurrentLearnWorker(frame, &b, "b", entered, releaseB, done)
	<-entered
	beginB := <-handler.begins
	close(releaseB)
	<-done
	close(releaseA)
	<-done
	readB, readA := <-handler.reads, <-handler.reads
	endB, endA := <-handler.ends, <-handler.ends
	if beginA.InvocationID == "" || beginB.InvocationID == "" || beginA.InvocationID == beginB.InvocationID || beginA.InstanceID == beginB.InstanceID {
		t.Fatalf("distinct invocation/instance identity missing: %+v %+v", beginA, beginB)
	}
	if readA.Name != "a" || readB.Name != "b" || readA.ReconcileID != beginA.InvocationID || readB.ReconcileID != beginB.InvocationID || endA.InvocationID != beginA.InvocationID || endB.InvocationID != beginB.InvocationID {
		t.Fatalf("interleaved workers lost attribution: %+v %+v %+v %+v", readA, readB, endA, endB)
	}
}
