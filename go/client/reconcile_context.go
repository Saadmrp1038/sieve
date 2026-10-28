package sieve

import (
	"bytes"
	"fmt"
	"os"
	"reflect"
	"runtime"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

// Cache-store reads have no context argument. Scope the observer to the current
// goroutine, retaining a stack for nested annotated calls. No scheduling or
// operator state is changed. Unsupported runtime headers are an observation
// error, never an attribution to another worker.
type learnInvocation struct {
	frame, id, instance string
}

var invocationScopes = struct {
	sync.Mutex
	workers map[uint64][]learnInvocation
}{workers: make(map[uint64][]learnInvocation)}
var invocationCounter uint64
var invocationProcess = fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano())

func observationWorker() (uint64, error) {
	var header [128]byte
	n := runtime.Stack(header[:], false)
	fields := bytes.Fields(header[:n])
	if len(fields) < 2 || string(fields[0]) != "goroutine" {
		return 0, fmt.Errorf("unsupported runtime stack header")
	}
	id, err := strconv.ParseUint(string(fields[1]), 10, 64)
	if err != nil || id == 0 {
		return 0, fmt.Errorf("invalid runtime worker identity")
	}
	return id, nil
}

func beginLearnInvocation(frame string, receiver interface{}) (learnInvocation, error) {
	worker, err := observationWorker()
	if err != nil {
		return learnInvocation{}, err
	}
	instance := frame
	if receiver != nil {
		value := reflect.ValueOf(receiver)
		if value.Kind() != reflect.Ptr {
			return learnInvocation{}, fmt.Errorf("reconcile instance must be a pointer")
		}
		instance = fmt.Sprintf("%T@%x", receiver, value.Pointer())
	}
	scope := learnInvocation{frame, fmt.Sprintf("%s-%d", invocationProcess, atomic.AddUint64(&invocationCounter, 1)), instance}
	invocationScopes.Lock()
	invocationScopes.workers[worker] = append(invocationScopes.workers[worker], scope)
	invocationScopes.Unlock()
	return scope, nil
}

func currentLearnInvocation() learnInvocation {
	worker, err := observationWorker()
	if err != nil {
		return learnInvocation{}
	}
	invocationScopes.Lock()
	defer invocationScopes.Unlock()
	stack := invocationScopes.workers[worker]
	if len(stack) == 0 {
		return learnInvocation{}
	}
	return stack[len(stack)-1]
}

func endLearnInvocation(scope learnInvocation) {
	worker, err := observationWorker()
	if err != nil {
		return
	}
	invocationScopes.Lock()
	defer invocationScopes.Unlock()
	stack := invocationScopes.workers[worker]
	if len(stack) > 0 && stack[len(stack)-1] == scope {
		if len(stack) == 1 {
			delete(invocationScopes.workers, worker)
		} else {
			invocationScopes.workers[worker] = stack[:len(stack)-1]
		}
	}
}

func learnReconcileID() string { return currentLearnInvocation().id }
