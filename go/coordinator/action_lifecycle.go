package main

import (
	"context"
	"fmt"
	"math"
	"os"
	"strconv"
	"time"
)

func coordinatorTimeout(name string, fallback time.Duration) (time.Duration, error) {
	value, present := os.LookupEnv(name)
	if !present {
		return fallback, nil
	}
	seconds, err := strconv.ParseFloat(value, 64)
	if err != nil || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds <= 0 || seconds >= float64(math.MaxInt64)/float64(time.Second) {
		return 0, fmt.Errorf("%s must be finite positive seconds", name)
	}
	duration := time.Duration(seconds * float64(time.Second))
	if duration <= 0 {
		return 0, fmt.Errorf("%s is below clock resolution", name)
	}
	return duration, nil
}

func actionSleep(ctx context.Context, seconds int) error {
	timer := time.NewTimer(time.Duration(seconds) * time.Second)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (a *ActionContext) completeAsync(err error) {
	select {
	case a.asyncDoneCh <- &AsyncDoneNotification{err: err}:
	case <-a.ctx.Done():
	}
}

func dispatchAction(ctx *ActionContext, async bool, run func() error) error {
	if !async {
		return run()
	}
	go func() {
		if err := run(); err != nil {
			ctx.completeAsync(err)
		}
	}()
	return nil
}
