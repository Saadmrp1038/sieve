package main

import (
	"log"
)

const (
	onObjectCreate         string = "onObjectCreate"
	onObjectDelete         string = "onObjectDelete"
	onObjectUpdate         string = "onObjectUpdate"
	onObjectUpdateTo       string = "onObjectUpdateTo"
	onAnyFieldModification string = "onAnyFieldModification"
	onTimeout              string = "onTimeout"
	onAnnotatedAPICall     string = "onAnnotatedAPICall"
)

type TriggerDefinition interface {
	getTriggerName() string
	satisfy(TriggerNotification) bool
}

type TimeoutTrigger struct {
	name         string
	timeoutValue int
}

func (t *TimeoutTrigger) getTriggerName() string {
	return t.name
}

func (t *TimeoutTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*TimeoutNotification); ok {
		if notification.conditionName == t.name {
			return true
		}
	}
	return false
}

type AnnotatedAPICallTrigger struct {
	name              string
	module            string
	filePath          string
	receiverType      string
	funName           string
	desiredOccurrence int
	actualOccurrence  int
	observedWhen      string
	observedBy        string
}

func (t *AnnotatedAPICallTrigger) getTriggerName() string {
	return t.name
}

func (t *AnnotatedAPICallTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*AnnotatedAPICallNotification); ok {
		if notification.module == t.module && notification.filePath == t.filePath && notification.receiverType == t.receiverType && notification.funName == t.funName && notification.observedWhen == t.observedWhen && notification.observedBy == t.observedBy {
			t.actualOccurrence += 1
			if t.actualOccurrence == t.desiredOccurrence {
				return true
			}
		}
	}
	return false
}

type ObjectCreateTrigger struct {
	name              string
	resourceKey       string
	desiredOccurrence int
	actualOccurrence  int
	observedWhen      string
	observedBy        string
}

func (t *ObjectCreateTrigger) getTriggerName() string {
	return t.name
}

func (t *ObjectCreateTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*ObjectCreateNotification); ok {
		if notification.resourceKey == t.resourceKey && notification.observedWhen == t.observedWhen && notification.observedBy == t.observedBy {
			t.actualOccurrence += 1
			if t.actualOccurrence == t.desiredOccurrence {
				return true
			}
		}
	}
	return false
}

type ObjectDeleteTrigger struct {
	name              string
	resourceKey       string
	desiredOccurrence int
	actualOccurrence  int
	observedWhen      string
	observedBy        string
}

func (t *ObjectDeleteTrigger) getTriggerName() string {
	return t.name
}

func (t *ObjectDeleteTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*ObjectDeleteNotification); ok {
		if notification.resourceKey == t.resourceKey && notification.observedWhen == t.observedWhen && notification.observedBy == t.observedBy {
			t.actualOccurrence += 1
			if t.actualOccurrence == t.desiredOccurrence {
				return true
			}
		}
	}
	return false
}

type ObjectUpdateTrigger struct {
	name                  string
	resourceKey           string
	prevStateDiff         map[string]interface{}
	curStateDiff          map[string]interface{}
	convertStateToAPIForm bool
	desiredOccurrence     int
	actualOccurrence      int
	observedWhen          string
	observedBy            string
}

func (t *ObjectUpdateTrigger) getTriggerName() string {
	return t.name
}

func (t *ObjectUpdateTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*ObjectUpdateNotification); ok {
		if notification.resourceKey == t.resourceKey && notification.observedWhen == t.observedWhen && notification.observedBy == t.observedBy {
			if t.prevStateDiff == nil && t.curStateDiff == nil {
				return true
			}
			// compute state diff
			exactMatch := true
			if notification.observedWhen == beforeAPIServerRecv || notification.observedWhen == afterAPIServerRecv {
				exactMatch = false
			}
			var fieldKeyMaskToUse map[string]struct{}
			var fieldPathMaskToUse map[string]struct{}
			if t.convertStateToAPIForm {
				fieldKeyMaskToUse = notification.fieldKeyMaskAPIForm
				fieldPathMaskToUse = notification.fieldPathMaskAPIForm
			} else {
				fieldKeyMaskToUse = notification.fieldKeyMask
				fieldPathMaskToUse = notification.fieldPathMask
			}
			log.Println(fieldKeyMaskToUse)
			log.Println(fieldPathMaskToUse)
			if isDesiredUpdate(notification.prevState, notification.curState, t.prevStateDiff, t.curStateDiff, fieldKeyMaskToUse, fieldPathMaskToUse, exactMatch) {
				t.actualOccurrence += 1
				if t.actualOccurrence == t.desiredOccurrence {
					return true
				}
			}
		}
	}
	return false
}

// An input value can be identified without requiring its previous full-object
// diff to match. Other fields may change while an independently produced input
// is delivered to multiple caches.
type ObjectUpdateToTrigger struct {
	name, resourceKey, observedWhen, observedBy string
	stateMatch                                  map[string]interface{}
	desiredOccurrence, actualOccurrence         int
}

func (t *ObjectUpdateToTrigger) getTriggerName() string { return t.name }

func (t *ObjectUpdateToTrigger) satisfy(notification TriggerNotification) bool {
	event, ok := notification.(*ObjectUpdateNotification)
	if !ok || event.resourceKey != t.resourceKey || event.observedWhen != t.observedWhen || event.observedBy != t.observedBy {
		return false
	}
	if !partOfEventAsMap(t.stateMatch, event.curState) {
		return false
	}
	t.actualOccurrence++
	return t.actualOccurrence == t.desiredOccurrence
}

type AnyFieldModificationTrigger struct {
	name                  string
	resourceKey           string
	prevStateDiff         map[string]interface{}
	convertStateToAPIForm bool
	desiredOccurrence     int
	actualOccurrence      int
	observedWhen          string
	observedBy            string
}

func (t *AnyFieldModificationTrigger) getTriggerName() string {
	return t.name
}

func (t *AnyFieldModificationTrigger) satisfy(triggerNotification TriggerNotification) bool {
	if notification, ok := triggerNotification.(*ObjectUpdateNotification); ok {
		if notification.resourceKey == t.resourceKey && notification.observedWhen == t.observedWhen && notification.observedBy == t.observedBy {
			var fieldKeyMaskToUse map[string]struct{}
			var fieldPathMaskToUse map[string]struct{}
			if t.convertStateToAPIForm {
				fieldKeyMaskToUse = notification.fieldKeyMaskAPIForm
				fieldPathMaskToUse = notification.fieldPathMaskAPIForm
			} else {
				fieldKeyMaskToUse = notification.fieldKeyMask
				fieldPathMaskToUse = notification.fieldPathMask
			}
			log.Println(fieldKeyMaskToUse)
			log.Println(fieldPathMaskToUse)
			if isAnyFieldModified(notification.curState, t.prevStateDiff, fieldKeyMaskToUse, fieldPathMaskToUse) {
				t.actualOccurrence += 1
				if t.actualOccurrence == t.desiredOccurrence {
					return true
				}
			}
		}
	}
	return false
}

type Action interface {
	getTriggerGraph() *TriggerGraph
	getTriggerDefinitions() map[string]TriggerDefinition
	isAsync() bool
	run(*ActionContext) error
}

type PauseAPIServerAction struct {
	apiServerName      string
	pauseScope         string
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *PauseAPIServerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *PauseAPIServerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *PauseAPIServerAction) isAsync() bool {
	return a.async
}

func (a *PauseAPIServerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the PauseAPIServerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}
	if _, ok := actionContext.apiserverLockedMap[a.apiServerName]; !ok {
		actionContext.apiserverLockedMap[a.apiServerName] = map[string]bool{}
	}
	if _, ok := actionContext.apiserverLocks[a.apiServerName]; !ok {
		actionContext.apiserverLocks[a.apiServerName] = map[string]chan string{}
	}
	log.Printf("Create channel for %s %s\n", a.apiServerName, a.pauseScope)
	if _, ok := actionContext.apiserverLocks[a.apiServerName][a.pauseScope]; !ok {
		actionContext.apiserverLocks[a.apiServerName][a.pauseScope] = make(chan string)
	}
	actionContext.apiserverLockedMap[a.apiServerName][a.pauseScope] = true
	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	log.Println("PauseAPIServerAction done")
	return nil
}

func (a *PauseAPIServerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type ResumeAPIServerAction struct {
	apiServerName      string
	pauseScope         string
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *ResumeAPIServerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *ResumeAPIServerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *ResumeAPIServerAction) isAsync() bool {
	return a.async
}

func (a *ResumeAPIServerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the ResumeAPIServerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}
	log.Printf("Close channel for %s %s\n", a.apiServerName, a.pauseScope)
	actionContext.apiserverLocks[a.apiServerName][a.pauseScope] <- "release"
	close(actionContext.apiserverLocks[a.apiServerName][a.pauseScope])
	actionContext.apiserverLockedMap[a.apiServerName][a.pauseScope] = false
	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	log.Println("ResumeAPIServerAction done")
	return nil
}

func (a *ResumeAPIServerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type PauseControllerAction struct {
	pauseScope         string
	pauseAt            string
	avoidOngoingRead   bool
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *PauseControllerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *PauseControllerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *PauseControllerAction) isAsync() bool {
	return a.async
}

func (a *PauseControllerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the PauseControllerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}

	actionContext.pauseControllerSharedDataLock.Lock()
	if _, ok := actionContext.controllerPausingChs[a.pauseAt]; !ok {
		actionContext.controllerPausingChs[a.pauseAt] = map[string]chan string{}
	}
	log.Printf("Create channel for %s %s\n", a.pauseAt, a.pauseScope)
	if _, ok := actionContext.controllerPausingChs[a.pauseAt][a.pauseScope]; !ok {
		actionContext.controllerPausingChs[a.pauseAt][a.pauseScope] = make(chan string)
	}

	if _, ok := actionContext.controllerShouldPauseMap[a.pauseAt]; !ok {
		actionContext.controllerShouldPauseMap[a.pauseAt] = map[string]bool{}
	}
	actionContext.controllerShouldPauseMap[a.pauseAt][a.pauseScope] = true
	actionContext.pauseControllerSharedDataLock.Unlock()

	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	if a.avoidOngoingRead {
		actionContext.controllerOngoingReadLock.Lock()
		log.Println("there is no ongoing read now; PauseControllerAction can return safely")
		actionContext.controllerOngoingReadLock.Unlock()
	}
	log.Println("PauseControllerAction done")
	return nil
}

func (a *PauseControllerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type ResumeControllerAction struct {
	pauseScope         string
	pauseAt            string
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *ResumeControllerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *ResumeControllerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *ResumeControllerAction) isAsync() bool {
	return a.async
}

func (a *ResumeControllerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the ResumeControllerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}
	log.Printf("Close channel for %s %s\n", a.pauseAt, a.pauseScope)

	actionContext.pauseControllerSharedDataLock.Lock()
	// actionContext.controllerPausingChs[a.pauseAt][a.pauseScope] <- "release"
	close(actionContext.controllerPausingChs[a.pauseAt][a.pauseScope])
	actionContext.controllerShouldPauseMap[a.pauseAt][a.pauseScope] = false
	actionContext.pauseControllerSharedDataLock.Unlock()

	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	log.Println("ResumeControllerAction done")
	return nil
}

func (a *ResumeControllerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type RestartControllerAction struct {
	controllerLabel    string
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *RestartControllerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *RestartControllerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *RestartControllerAction) isAsync() bool {
	return a.async
}

func (a *RestartControllerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the RestartControllerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}
	if err := actionContext.restart(actionContext.ctx, a.controllerLabel, ""); err != nil {
		return err
	}
	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	log.Println("RestartControllerAction done")
	return nil
}

func (a *RestartControllerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type ReconnectControllerAction struct {
	controllerLabel    string
	reconnectAPIServer string
	async              bool
	waitBefore         int
	waitAfter          int
	triggerGraph       *TriggerGraph
	triggerDefinitions map[string]TriggerDefinition
}

func (a *ReconnectControllerAction) getTriggerGraph() *TriggerGraph {
	return a.triggerGraph
}

func (a *ReconnectControllerAction) getTriggerDefinitions() map[string]TriggerDefinition {
	return a.triggerDefinitions
}

func (a *ReconnectControllerAction) isAsync() bool {
	return a.async
}

func (a *ReconnectControllerAction) runInternal(actionContext *ActionContext, async bool) error {
	log.Println("run the ReconnectControllerAction")
	if a.waitBefore > 0 {
		if err := actionSleep(actionContext.ctx, a.waitBefore); err != nil {
			return err
		}
	}
	if err := actionContext.restart(actionContext.ctx, a.controllerLabel, a.reconnectAPIServer); err != nil {
		return err
	}
	if a.waitAfter > 0 {
		if err := actionSleep(actionContext.ctx, a.waitAfter); err != nil {
			return err
		}
	}
	if async {
		actionContext.completeAsync(nil)
	}
	log.Println("ReconnectControllerAction done")
	return nil
}

func (a *ReconnectControllerAction) run(actionContext *ActionContext) error {
	return dispatchAction(actionContext, a.async, func() error { return a.runInternal(actionContext, a.async) })
}

type TestPlan struct {
	actions []Action
}

func (spec TriggerSpecDefinition) build() TriggerDefinition {
	condition := spec.Condition
	conditionType := string(condition.ConditionType)
	switch conditionType {
	case onObjectUpdateTo:
		state := strToMap(text(condition.StateMatch, ""))
		if boolean(condition.ConvertStateToAPIForm) {
			state = convertObjectStateToAPIForm(state)
		}
		return &ObjectUpdateToTrigger{
			name: string(spec.TriggerName), resourceKey: text(condition.ResourceKey, ""),
			observedWhen: string(spec.ObservationPoint.When), observedBy: string(spec.ObservationPoint.By),
			stateMatch: state, desiredOccurrence: integer(condition.Occurrence),
		}
	case onObjectCreate:
		return &ObjectCreateTrigger{
			name:              string(spec.TriggerName),
			resourceKey:       text(condition.ResourceKey, ""),
			desiredOccurrence: integer(condition.Occurrence),
			actualOccurrence:  0,
			observedWhen:      string(spec.ObservationPoint.When),
			observedBy:        string(spec.ObservationPoint.By),
		}
	case onObjectDelete:
		return &ObjectDeleteTrigger{
			name:              string(spec.TriggerName),
			resourceKey:       text(condition.ResourceKey, ""),
			desiredOccurrence: integer(condition.Occurrence),
			actualOccurrence:  0,
			observedWhen:      string(spec.ObservationPoint.When),
			observedBy:        string(spec.ObservationPoint.By),
		}
	case onObjectUpdate:
		convertStateToAPIForm := boolean(condition.ConvertStateToAPIForm)
		var prevStateDiff map[string]interface{} = nil
		var curStateDiff map[string]interface{} = nil
		if condition.PrevStateDiff != nil && condition.CurStateDiff != nil {
			if convertStateToAPIForm {
				prevStateDiff = convertObjectStateToAPIForm(strToMap(text(condition.PrevStateDiff, "")))
				curStateDiff = convertObjectStateToAPIForm(strToMap(text(condition.CurStateDiff, "")))
			} else {
				prevStateDiff = strToMap(text(condition.PrevStateDiff, ""))
				curStateDiff = strToMap(text(condition.CurStateDiff, ""))
			}
		}
		return &ObjectUpdateTrigger{
			name:                  string(spec.TriggerName),
			resourceKey:           text(condition.ResourceKey, ""),
			prevStateDiff:         prevStateDiff,
			curStateDiff:          curStateDiff,
			convertStateToAPIForm: convertStateToAPIForm,
			desiredOccurrence:     integer(condition.Occurrence),
			actualOccurrence:      0,
			observedWhen:          string(spec.ObservationPoint.When),
			observedBy:            string(spec.ObservationPoint.By),
		}
	case onAnyFieldModification:
		convertStateToAPIForm := boolean(condition.ConvertStateToAPIForm)
		var prevStateDiff map[string]interface{}
		if convertStateToAPIForm {
			prevStateDiff = convertObjectStateToAPIForm(strToMap(text(condition.PrevStateDiff, "")))
		} else {
			prevStateDiff = strToMap(text(condition.PrevStateDiff, ""))
		}
		return &AnyFieldModificationTrigger{
			name:                  string(spec.TriggerName),
			resourceKey:           text(condition.ResourceKey, ""),
			prevStateDiff:         prevStateDiff,
			convertStateToAPIForm: convertStateToAPIForm,
			desiredOccurrence:     integer(condition.Occurrence),
			actualOccurrence:      0,
			observedWhen:          string(spec.ObservationPoint.When),
			observedBy:            string(spec.ObservationPoint.By),
		}
	case onTimeout:
		return &TimeoutTrigger{
			name:         string(spec.TriggerName),
			timeoutValue: integer(condition.TimeoutValue),
		}
	case onAnnotatedAPICall:
		return &AnnotatedAPICallTrigger{
			name:              string(spec.TriggerName),
			module:            text(condition.Module, ""),
			filePath:          text(condition.FilePath, ""),
			receiverType:      text(condition.ReceiverType, ""),
			funName:           text(condition.FunName, ""),
			desiredOccurrence: integer(condition.Occurrence),
			actualOccurrence:  0,
			observedWhen:      string(spec.ObservationPoint.When),
			observedBy:        string(spec.ObservationPoint.By),
		}
	default:
		panic("unvalidated trigger type: " + conditionType)
	}
}

func (spec ActionSpec) build() Action {
	expression := string(spec.Trigger.Expression)
	infix := expressionToInfixTokens(expression)
	prefix := infixToPrefix(infix)
	binaryTreeRoot := prefixExpressionToBinaryTree(prefix)
	triggerGraph := binaryTreeToTriggerGraph(binaryTreeRoot)
	printTriggerGraph(triggerGraph)

	triggerDefinitions := map[string]TriggerDefinition{}
	for _, definition := range spec.Trigger.Definitions {
		trigger := definition.build()
		triggerDefinitions[trigger.getTriggerName()] = trigger
	}
	actionType := string(spec.ActionType)
	async := bool(spec.Async)
	waitBefore, waitAfter := int(spec.WaitBefore), int(spec.WaitAfter)

	switch actionType {
	case pauseAPIServer:
		return &PauseAPIServerAction{
			apiServerName:      text(spec.APIServerName, ""),
			pauseScope:         text(spec.PauseScope, ""),
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	case resumeAPIServer:
		return &ResumeAPIServerAction{
			apiServerName:      text(spec.APIServerName, ""),
			pauseScope:         text(spec.PauseScope, ""),
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	case pauseController:
		pauseScope := text(spec.PauseScope, "all")
		avoidOngoingRead := boolean(spec.AvoidOngoingRead)
		return &PauseControllerAction{
			pauseScope:         pauseScope,
			pauseAt:            text(spec.PauseAt, ""),
			avoidOngoingRead:   avoidOngoingRead,
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	case resumeController:
		pauseScope := text(spec.PauseScope, "all")
		return &ResumeControllerAction{
			pauseScope:         pauseScope,
			pauseAt:            text(spec.PauseAt, ""),
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	case restartController:
		return &RestartControllerAction{
			controllerLabel:    text(spec.ControllerLabel, ""),
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	case reconnectController:
		return &ReconnectControllerAction{
			controllerLabel:    text(spec.ControllerLabel, ""),
			reconnectAPIServer: text(spec.ReconnectAPIServer, ""),
			async:              async,
			waitBefore:         waitBefore,
			waitAfter:          waitAfter,
			triggerGraph:       triggerGraph,
			triggerDefinitions: triggerDefinitions,
		}
	default:
		panic("unvalidated action type: " + actionType)
	}
}

func (spec *PlanSpec) build() *TestPlan {
	actions := make([]Action, 0, len(spec.Actions))
	for _, action := range spec.Actions {
		actions = append(actions, action.build())
	}
	return &TestPlan{actions: actions}
}

func printExpression(exp []string) {
	log.Printf("%v", exp)
}

func printTriggerNode(triggerNode *TriggerNode) {
	log.Printf("node name: %s, node type: %s\n", triggerNode.nodeName, triggerNode.nodeType)
	for _, predecessor := range triggerNode.predecessors {
		log.Printf("predecessor: %s\n", predecessor.nodeName)
	}
	for _, successor := range triggerNode.successors {
		log.Printf("successor: %s\n", successor.nodeName)
	}
}

func printTriggerGraph(triggerGraph *TriggerGraph) {
	log.Println("all nodes:")
	for _, node := range triggerGraph.allNodes {
		log.Printf("node name: %s\n", node.nodeName)
	}
	log.Println("print each node:")
	for _, node := range triggerGraph.allNodes {
		printTriggerNode(node)
	}
}
