package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strings"

	"go.yaml.in/yaml/v3"
)

// YAML's implicit scalar conversions must not turn a typo into another schedule.
type planText string

func (v *planText) UnmarshalYAML(n *yaml.Node) error {
	if n.Tag != "!!str" {
		return fmt.Errorf("line %d: expected a string", n.Line)
	}
	*v = planText(n.Value)
	return nil
}

type planInt int

func (v *planInt) UnmarshalYAML(n *yaml.Node) error {
	if n.Tag != "!!int" {
		return fmt.Errorf("line %d: expected an integer", n.Line)
	}
	var value int
	if err := n.Decode(&value); err != nil {
		return err
	}
	*v = planInt(value)
	return nil
}

type planBool bool

func (v *planBool) UnmarshalYAML(n *yaml.Node) error {
	if n.Tag != "!!bool" {
		return fmt.Errorf("line %d: expected a boolean", n.Line)
	}
	var value bool
	if err := n.Decode(&value); err != nil {
		return err
	}
	*v = planBool(value)
	return nil
}

type PlanSpec struct {
	Workload                     planText     `yaml:"workload"`
	Actions                      []ActionSpec `yaml:"actions"`
	AnnotatedReconcileStackFrame []planText   `yaml:"annotatedReconcileStackFrame"`
}

type ActionSpec struct {
	ActionType         planText    `yaml:"actionType"`
	Trigger            TriggerSpec `yaml:"trigger"`
	Async              planBool    `yaml:"async"`
	WaitBefore         planInt     `yaml:"waitBefore"`
	WaitAfter          planInt     `yaml:"waitAfter"`
	APIServerName      *planText   `yaml:"apiServerName"`
	PauseScope         *planText   `yaml:"pauseScope"`
	PauseAt            *planText   `yaml:"pauseAt"`
	PauseBy            *planText   `yaml:"pauseBy"`
	AvoidOngoingRead   *planBool   `yaml:"avoidOngoingRead"`
	ControllerLabel    *planText   `yaml:"controllerLabel"`
	ReconnectAPIServer *planText   `yaml:"reconnectAPIServer"`
}

type TriggerSpec struct {
	Definitions []TriggerSpecDefinition `yaml:"definitions"`
	Expression  planText                `yaml:"expression"`
}

type TriggerSpecDefinition struct {
	TriggerName      planText         `yaml:"triggerName"`
	Condition        ConditionSpec    `yaml:"condition"`
	ObservationPoint *ObservationSpec `yaml:"observationPoint"`
}

type ObservationSpec struct {
	When planText `yaml:"when"`
	By   planText `yaml:"by"`
}

type ConditionSpec struct {
	ConditionType         planText  `yaml:"conditionType"`
	ResourceKey           *planText `yaml:"resourceKey"`
	Occurrence            *planInt  `yaml:"occurrence"`
	PrevStateDiff         *planText `yaml:"prevStateDiff"`
	CurStateDiff          *planText `yaml:"curStateDiff"`
	ConvertStateToAPIForm *planBool `yaml:"convertStateToAPIForm"`
	TimeoutValue          *planInt  `yaml:"timeoutValue"`
	Module                *planText `yaml:"module"`
	FilePath              *planText `yaml:"filePath"`
	ReceiverType          *planText `yaml:"receiverType"`
	FunName               *planText `yaml:"funName"`
}

func decodePlan(data []byte) (*PlanSpec, error) {
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	var spec PlanSpec
	if err := decoder.Decode(&spec); err != nil {
		return nil, fmt.Errorf("invalid fault plan: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("fault plan must contain exactly one YAML document")
	}
	var document yaml.Node
	if err := yaml.Unmarshal(data, &document); err != nil {
		return nil, err
	}
	if len(document.Content) == 0 {
		return nil, fmt.Errorf("fault plan is empty")
	}
	root := document.Content[0]
	foundActions := false
	for i := 0; i < len(root.Content); i += 2 {
		if root.Content[i].Value == "actions" {
			foundActions = true
		}
	}
	if !foundActions {
		return nil, fmt.Errorf("actions: required (use null for an unfaulted historical plan)")
	}
	if err := rejectPlanNulls(root, ""); err != nil {
		return nil, err
	}
	if spec.Workload == "" {
		return nil, fmt.Errorf("workload: required nonempty string")
	}
	for i := range spec.Actions {
		if err := spec.Actions[i].validate(); err != nil {
			return nil, fmt.Errorf("actions[%d].%w", i, err)
		}
	}
	return &spec, nil
}

func requiredText(value *planText, field string) error {
	if value == nil || *value == "" {
		return fmt.Errorf("%s: required nonempty string", field)
	}
	return nil
}
func text(value *planText, fallback string) string {
	if value == nil {
		return fallback
	}
	return string(*value)
}
func integer(value *planInt) int {
	if value == nil {
		return 0
	}
	return int(*value)
}
func boolean(value *planBool) bool { return value != nil && bool(*value) }

// Reject fields belonging to another variant, even when the decoder knows them.
func rejectFields(present map[string]bool, allowed ...string) error {
	for _, key := range allowed {
		delete(present, key)
	}
	for key, exists := range present {
		if exists {
			return fmt.Errorf("%s: not valid for this type", key)
		}
	}
	return nil
}

func (a *ActionSpec) validate() error {
	if a.WaitBefore < 0 || a.WaitAfter < 0 {
		return fmt.Errorf("waitBefore/waitAfter: must be nonnegative")
	}
	fields := map[string]bool{
		"apiServerName": a.APIServerName != nil, "pauseScope": a.PauseScope != nil,
		"pauseAt": a.PauseAt != nil, "pauseBy": a.PauseBy != nil,
		"avoidOngoingRead": a.AvoidOngoingRead != nil, "controllerLabel": a.ControllerLabel != nil,
		"reconnectAPIServer": a.ReconnectAPIServer != nil,
	}
	var allowed []string
	switch string(a.ActionType) {
	case pauseAPIServer, resumeAPIServer:
		allowed = []string{"apiServerName", "pauseScope"}
		if err := requiredText(a.APIServerName, "apiServerName"); err != nil {
			return err
		}
		if err := requiredText(a.PauseScope, "pauseScope"); err != nil {
			return err
		}
	case pauseController, resumeController:
		allowed = []string{"pauseAt", "pauseScope"}
		if a.ActionType == planText(pauseController) {
			allowed = append(allowed, "pauseBy", "avoidOngoingRead")
		}
		switch text(a.PauseAt, "") {
		case beforeControllerRead, afterControllerRead, beforeControllerWrite, afterControllerWrite:
		default:
			return fmt.Errorf("pauseAt: invalid controller observation point")
		}
		if a.PauseScope != nil && *a.PauseScope == "" {
			return fmt.Errorf("pauseScope: must not be empty")
		}
		if a.PauseBy != nil && *a.PauseBy == "" {
			return fmt.Errorf("pauseBy: must not be empty")
		}
	case restartController, reconnectController:
		allowed = []string{"controllerLabel"}
		if err := requiredText(a.ControllerLabel, "controllerLabel"); err != nil {
			return err
		}
		if a.ActionType == planText(reconnectController) {
			allowed = append(allowed, "reconnectAPIServer")
			if err := requiredText(a.ReconnectAPIServer, "reconnectAPIServer"); err != nil {
				return err
			}
		}
	default:
		return fmt.Errorf("actionType: unknown action %q", a.ActionType)
	}
	if err := rejectFields(fields, allowed...); err != nil {
		return err
	}
	return a.Trigger.validate()
}

var triggerNamePattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
var expressionTokens = regexp.MustCompile(`[A-Za-z_][A-Za-z0-9_]*|[;&|()]`)

func validateExpression(expression string, names map[string]bool) error {
	tokens := expressionTokens.FindAllString(expression, -1)
	if len(tokens) == 0 || strings.Join(tokens, "") != expression {
		return fmt.Errorf("invalid expression syntax")
	}
	operand, depth := true, 0
	for _, token := range tokens {
		if operand {
			if token == "(" {
				depth++
			} else if names[token] {
				operand = false
			} else {
				return fmt.Errorf("expected a defined trigger or '(', got %q", token)
			}
		} else if token == ")" && depth > 0 {
			depth--
		} else if token == ";" || token == "&" || token == "|" {
			operand = true
		} else {
			return fmt.Errorf("expected an operator or ')', got %q", token)
		}
	}
	if operand || depth != 0 {
		return fmt.Errorf("incomplete expression")
	}
	return nil
}

func (t *TriggerSpec) validate() error {
	if len(t.Definitions) == 0 {
		return fmt.Errorf("trigger.definitions: at least one trigger is required")
	}
	names := map[string]bool{}
	for i, definition := range t.Definitions {
		name := string(definition.TriggerName)
		if !triggerNamePattern.MatchString(name) || names[name] {
			return fmt.Errorf("trigger.definitions[%d].triggerName: invalid or duplicate name", i)
		}
		names[name] = true
		if err := definition.Condition.validate(); err != nil {
			return fmt.Errorf("trigger.definitions[%d].condition.%w", i, err)
		}
		point := definition.ObservationPoint
		if definition.Condition.ConditionType == planText(onTimeout) {
			if point != nil {
				return fmt.Errorf("trigger.definitions[%d].observationPoint: not valid for onTimeout", i)
			}
		} else {
			if point == nil || point.By == "" {
				return fmt.Errorf("trigger.definitions[%d].observationPoint: required", i)
			}
			switch string(point.When) {
			case beforeAPIServerRecv, afterAPIServerRecv, beforeControllerRecv, afterControllerRecv, beforeControllerWrite, afterControllerWrite, beforeControllerRead, afterControllerRead, beforeAnnotatedAPICall, afterAnnotatedAPICall:
			default:
				return fmt.Errorf("trigger.definitions[%d].observationPoint.when: invalid observation point", i)
			}
		}
	}
	if err := validateExpression(string(t.Expression), names); err != nil {
		return fmt.Errorf("trigger.expression: %w", err)
	}
	return nil
}

func (c *ConditionSpec) validate() error {
	fields := map[string]bool{
		"resourceKey": c.ResourceKey != nil, "occurrence": c.Occurrence != nil,
		"prevStateDiff": c.PrevStateDiff != nil, "curStateDiff": c.CurStateDiff != nil,
		"convertStateToAPIForm": c.ConvertStateToAPIForm != nil, "timeoutValue": c.TimeoutValue != nil,
		"module": c.Module != nil, "filePath": c.FilePath != nil, "receiverType": c.ReceiverType != nil, "funName": c.FunName != nil,
	}
	var allowed []string
	switch string(c.ConditionType) {
	case onTimeout:
		allowed = []string{"timeoutValue"}
		if c.TimeoutValue == nil || *c.TimeoutValue < 0 {
			return fmt.Errorf("timeoutValue: required nonnegative integer")
		}
	case onObjectCreate, onObjectDelete, onObjectUpdate, onAnyFieldModification:
		allowed = []string{"resourceKey", "occurrence"}
		if err := requiredText(c.ResourceKey, "resourceKey"); err != nil {
			return err
		}
		if c.ConditionType == planText(onObjectUpdate) || c.ConditionType == planText(onAnyFieldModification) {
			allowed = append(allowed, "prevStateDiff", "convertStateToAPIForm")
			if c.ConditionType == planText(onObjectUpdate) {
				allowed = append(allowed, "curStateDiff")
				if (c.PrevStateDiff == nil) != (c.CurStateDiff == nil) {
					return fmt.Errorf("prevStateDiff/curStateDiff: supply both or neither")
				}
			} else if c.PrevStateDiff == nil {
				return fmt.Errorf("prevStateDiff: required for onAnyFieldModification")
			}
			for _, diff := range []*planText{c.PrevStateDiff, c.CurStateDiff} {
				if diff != nil {
					var object map[string]any
					if err := json.Unmarshal([]byte(*diff), &object); err != nil {
						return fmt.Errorf("state difference: expected a JSON object or null: %w", err)
					}
				}
			}
		}
	case onAnnotatedAPICall:
		allowed = []string{"module", "filePath", "receiverType", "funName", "occurrence"}
		for field, value := range map[string]*planText{"module": c.Module, "filePath": c.FilePath, "funName": c.FunName} {
			if err := requiredText(value, field); err != nil {
				return err
			}
		}
		if c.ReceiverType == nil {
			return fmt.Errorf("receiverType: required")
		}
	default:
		return fmt.Errorf("conditionType: unknown condition %q", c.ConditionType)
	}
	if c.ConditionType != planText(onTimeout) && (c.Occurrence == nil || *c.Occurrence <= 0) {
		return fmt.Errorf("occurrence: required positive integer")
	}
	return rejectFields(fields, allowed...)
}

func rejectPlanNulls(node *yaml.Node, path string) error {
	if node.Tag == "!!null" && path != "actions" {
		return fmt.Errorf("%s: cannot be null", path)
	}
	if node.Kind == yaml.MappingNode {
		for i := 0; i < len(node.Content); i += 2 {
			key := node.Content[i].Value
			if path != "" {
				key = path + "." + key
			}
			if err := rejectPlanNulls(node.Content[i+1], key); err != nil {
				return err
			}
		}
	} else if node.Kind == yaml.SequenceNode {
		for i, child := range node.Content {
			if err := rejectPlanNulls(child, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}
