package main

import (
	"fmt"
	"log"
)

type StateMachine struct {
	errors                 chan error
	states                 []Action
	nextState              int
	stateNotificationCh    chan TriggerNotification
	timeoutNotificationCh  chan TriggerNotification
	asyncDoneCh            chan *AsyncDoneNotification
	asyncActionInExecution bool
	actionContext          *ActionContext
}

func NewStateMachine(testPlan *TestPlan, stateNotificationCh chan TriggerNotification, asyncDoneCh chan *AsyncDoneNotification, actionContext *ActionContext) *StateMachine {
	return &StateMachine{
		errors:                 make(chan error, 1),
		states:                 testPlan.actions,
		nextState:              0,
		stateNotificationCh:    stateNotificationCh,
		timeoutNotificationCh:  make(chan TriggerNotification, 500),
		asyncDoneCh:            asyncDoneCh,
		asyncActionInExecution: false,
		actionContext:          actionContext,
	}
}

func (sm *StateMachine) waitForTimeout(timeoutValue int, triggerName string) {
	if err := actionSleep(sm.actionContext.ctx, timeoutValue); err != nil {
		return
	}
	select {
	case sm.timeoutNotificationCh <- &TimeoutNotification{conditionName: triggerName}:
	case <-sm.actionContext.ctx.Done():
	}
}

func (sm *StateMachine) setTimeoutForTimeoutTriggers() {
	triggerGraph := sm.states[sm.nextState].getTriggerGraph()
	triggerDefinitions := sm.states[sm.nextState].getTriggerDefinitions()
	for triggerName := range triggerGraph.toSatisfy {
		if timeoutTrigger, ok := triggerDefinitions[triggerName].(*TimeoutTrigger); ok {
			go sm.waitForTimeout(timeoutTrigger.timeoutValue, timeoutTrigger.getTriggerName())
		}
	}
}

func (sm *StateMachine) processNotification(notification TriggerNotification) error {
	defer func() {
		if blockingCh := notification.getBlockingCh(); blockingCh != nil {
			log.Println("release the blocking ch")
			select {
			case blockingCh <- "release":
			case <-sm.actionContext.ctx.Done():
			}
		}
	}()
	if sm.states == nil {
		return nil
	}
	if sm.nextState >= len(sm.states) {
		// all the actions are finished
		return nil
	}
	if sm.asyncActionInExecution {
		// do not process triggers before the previous async action gets finished
		return nil
	}

	action := sm.states[sm.nextState]
	triggerGraph := sm.states[sm.nextState].getTriggerGraph()
	triggerDefinitions := sm.states[sm.nextState].getTriggerDefinitions()
	for triggerName := range triggerGraph.toSatisfy {
		triggerDefinition, foundTrigger := triggerDefinitions[triggerName]
		if !foundTrigger {
			log.Printf("trigger %s is not in the definition table; skip it\n", triggerName)
			continue
		}
		if triggerDefinition.satisfy(notification) {
			triggerGraph.trigger(triggerName)
			log.Printf("trigger %s is satisfied\n", triggerName)
			if triggerGraph.fullyTriggered() {
				log.Printf("all triggers are satisfied for action %d\n", sm.nextState)
				if err := action.run(sm.actionContext); err != nil {
					return fmt.Errorf("action %d (%T): %w", sm.nextState, action, err)
				}
				if !action.isAsync() {
					sm.nextState += 1
					if sm.nextState >= len(sm.states) {
						log.Println("Sieve test coordinator finishes all actions")
					} else {
						sm.setTimeoutForTimeoutTriggers()
					}
				} else {
					sm.asyncActionInExecution = true
				}
				break
			} else {
				sm.setTimeoutForTimeoutTriggers()
			}
		}
	}
	return nil
}

func (sm *StateMachine) processAsyncDone(notification *AsyncDoneNotification) error {
	if notification.err != nil {
		return fmt.Errorf("async action %d: %w", sm.nextState, notification.err)
	}
	sm.nextState += 1
	sm.asyncActionInExecution = false
	if sm.nextState >= len(sm.states) {
		log.Println("Sieve test coordinator finishes all actions")
	} else {
		sm.setTimeoutForTimeoutTriggers()
	}
	return nil
}

func (sm *StateMachine) run() {
	for {
		var err error
		select {
		case <-sm.actionContext.ctx.Done():
			return
		case notification := <-sm.stateNotificationCh:
			err = sm.processNotification(notification)
		case notification := <-sm.timeoutNotificationCh:
			err = sm.processNotification(notification)
		case notification := <-sm.asyncDoneCh:
			err = sm.processAsyncDone(notification)
		}
		if err != nil {
			sm.errors <- err
			return
		}
	}
}
