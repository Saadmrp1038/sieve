package main

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func TestFailedActionsNeverAdvanceTheSchedule(t *testing.T) {
	data, err := os.ReadFile("../../fixtures/plans/restart.yaml")
	if err != nil {
		t.Fatal(err)
	}
	for _, async := range []bool{false, true} {
		spec, err := decodePlan(data)
		if err != nil {
			t.Fatal(err)
		}
		spec.Actions[0].Async = planBool(async)
		sentinel := errors.New("restart failed")
		ctx, cancel := context.WithCancel(context.Background())
		actionContext := &ActionContext{ctx: ctx, asyncDoneCh: make(chan *AsyncDoneNotification), restart: func(context.Context, string, string) error { return sentinel }}
		sm := NewStateMachine(spec.build(), make(chan TriggerNotification, 1), actionContext.asyncDoneCh, actionContext)
		go sm.run()
		sm.stateNotificationCh <- &ObjectCreateNotification{resourceKey: "secret/default/sieve-generated-secret", observedWhen: afterControllerWrite, observedBy: "github.com/mittwald/kubernetes-secret-generator/pkg/controller/crd/stringsecret.(*ReconcileStringSecret).Reconcile"}
		select {
		case err := <-sm.errors:
			if !errors.Is(err, sentinel) {
				t.Fatal(err)
			}
		case <-time.After(time.Second):
			t.Fatal("action error was not reported")
		}
		if sm.nextState != 0 {
			t.Fatal("failed action advanced schedule")
		}
		cancel()
	}
}

func TestCoordinatorTimeoutsAndCancellableDelays(t *testing.T) {
	for _, value := range []string{"0", "-1", "NaN", "Inf", "bad", "1e40", "0.00000000001"} {
		t.Setenv("SIEVE_TEST_LIMIT", value)
		if _, err := coordinatorTimeout("SIEVE_TEST_LIMIT", time.Minute); err == nil {
			t.Fatalf("accepted %q", value)
		}
	}
	t.Setenv("SIEVE_TEST_LIMIT", "1.5")
	if limit, err := coordinatorTimeout("SIEVE_TEST_LIMIT", time.Minute); err != nil || limit != 1500*time.Millisecond {
		t.Fatalf("%v %v", limit, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := actionSleep(ctx, 3600); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
