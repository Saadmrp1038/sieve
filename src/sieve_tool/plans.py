"""Validated fault schedules shared by generators and the runtime boundary."""

import json
import re
from collections.abc import Hashable
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

Nonempty = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0, le=(1 << 63) - 1)]
NonnegativeInt = Annotated[int, Field(ge=0, le=9_223_372_036)]
type Observation = Literal[
    "beforeAPIServerRecv",
    "afterAPIServerRecv",
    "beforeControllerRecv",
    "afterControllerRecv",
    "beforeControllerWrite",
    "afterControllerWrite",
    "beforeControllerRead",
    "afterControllerRead",
    "beforeAnnotatedAPICall",
    "afterAnnotatedAPICall",
]


class PlanModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def reject_nulls(cls, value: object) -> object:
        if isinstance(value, dict):
            for key, item in value.items():
                if item is None and key not in cls.nullable_fields:
                    raise ValueError(
                        f"{key} cannot be null; omit optional fields instead"
                    )
        return value


class ObservationPoint(PlanModel):
    when: Observation
    by: Nonempty


class ObjectCondition(PlanModel):
    condition_type: Literal["onObjectCreate", "onObjectDelete"]
    resource_key: Nonempty
    occurrence: PositiveInt


class UpdateCondition(PlanModel):
    condition_type: Literal["onObjectUpdate", "onAnyFieldModification"]
    resource_key: Nonempty
    occurrence: PositiveInt
    prev_state_diff: str | None = None
    cur_state_diff: str | None = None
    convert_state_to_api_form: bool = Field(
        default=False, alias="convertStateToAPIForm"
    )

    @field_validator("prev_state_diff", "cur_state_diff")
    @classmethod
    def object_diff(cls, value: str | None) -> str | None:
        if value is not None:
            decoded = json.loads(value)
            if decoded is not None and not isinstance(decoded, dict):
                raise ValueError("state difference must encode a JSON object or null")
        return value

    @model_validator(mode="after")
    def paired_differences(self) -> Self:
        if self.condition_type == "onAnyFieldModification":
            if self.prev_state_diff is None or self.cur_state_diff is not None:
                raise ValueError("onAnyFieldModification requires only prevStateDiff")
        elif (self.prev_state_diff is None) != (self.cur_state_diff is None):
            raise ValueError("prevStateDiff and curStateDiff must be supplied together")
        return self


class TimeoutCondition(PlanModel):
    condition_type: Literal["onTimeout"]
    timeout_value: NonnegativeInt


class StateMatchCondition(PlanModel):
    condition_type: Literal["onObjectUpdateTo"]
    resource_key: Nonempty
    occurrence: PositiveInt
    state_match: str
    convert_state_to_api_form: bool = Field(
        default=False, alias="convertStateToAPIForm"
    )

    @field_validator("state_match")
    @classmethod
    def nonempty_object(cls, value: str) -> str:
        decoded = json.loads(value)
        if not isinstance(decoded, dict) or not decoded:
            raise ValueError("stateMatch must encode a nonempty JSON object")
        return value


class APICallCondition(PlanModel):
    condition_type: Literal["onAnnotatedAPICall"]
    module: Nonempty
    file_path: Nonempty
    receiver_type: str
    fun_name: Nonempty
    occurrence: PositiveInt


type Condition = Annotated[
    ObjectCondition
    | UpdateCondition
    | StateMatchCondition
    | TimeoutCondition
    | APICallCondition,
    Field(discriminator="condition_type"),
]


class TriggerDefinition(PlanModel):
    trigger_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    condition: Condition
    observation_point: ObservationPoint | None = None

    @model_validator(mode="after")
    def observation_required(self) -> Self:
        if isinstance(self.condition, TimeoutCondition):
            if self.observation_point is not None:
                raise ValueError("onTimeout has no observationPoint")
        elif self.observation_point is None:
            raise ValueError("observationPoint is required for an observed trigger")
        if isinstance(self.condition, StateMatchCondition) and (
            self.observation_point is None
            or self.observation_point.when
            not in {
                "beforeAPIServerRecv",
                "afterAPIServerRecv",
                "beforeControllerRecv",
                "afterControllerRecv",
            }
        ):
            raise ValueError("onObjectUpdateTo requires an object-receipt observation")
        return self


def validate_expression(expression: str, names: set[str]) -> None:
    """Check syntax without changing the executor's precedence or associativity."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[;&|()]", expression)
    if "".join(tokens) != expression or not tokens:
        raise ValueError("invalid trigger expression")
    operand = True
    depth = 0
    for token in tokens:
        if operand:
            if token == "(":
                depth += 1
            elif token in names:
                operand = False
            else:
                raise ValueError(f"expected a defined trigger or '(', got {token!r}")
        elif token == ")" and depth:
            depth -= 1
        elif token in {";", "&", "|"}:
            operand = True
        else:
            raise ValueError(f"expected an operator or ')', got {token!r}")
    if operand or depth:
        raise ValueError("incomplete trigger expression")


class Trigger(PlanModel):
    definitions: Annotated[list[TriggerDefinition], Field(min_length=1)]
    expression: Nonempty

    @model_validator(mode="after")
    def valid_expression(self) -> Self:
        names = [definition.trigger_name for definition in self.definitions]
        if len(set(names)) != len(names):
            raise ValueError("trigger names must be unique within an action")
        validate_expression(self.expression, set(names))
        return self


class Action(PlanModel):
    trigger: Trigger
    async_: bool = Field(default=False, alias="async")
    wait_before: NonnegativeInt = 0
    wait_after: NonnegativeInt = 0


class APIServerAction(Action):
    action_type: Literal["pauseAPIServer", "resumeAPIServer"]
    api_server_name: Nonempty
    pause_scope: Nonempty


class ResumeControllerAction(Action):
    action_type: Literal["resumeController"]
    pause_at: Literal[
        "beforeControllerRead",
        "afterControllerRead",
        "beforeControllerWrite",
        "afterControllerWrite",
    ]
    pause_scope: Nonempty = "all"


class PauseControllerAction(Action):
    action_type: Literal["pauseController"]
    pause_at: Literal[
        "beforeControllerRead",
        "afterControllerRead",
        "beforeControllerWrite",
        "afterControllerWrite",
    ]
    pause_scope: Nonempty = "all"
    pause_by: Nonempty | None = None
    avoid_ongoing_read: bool = False


class RestartControllerAction(Action):
    action_type: Literal["restartController"]
    controller_label: Nonempty


class ReconnectControllerAction(Action):
    action_type: Literal["reconnectController"]
    controller_label: Nonempty
    reconnect_api_server: Nonempty = Field(alias="reconnectAPIServer")


type FaultAction = Annotated[
    APIServerAction
    | PauseControllerAction
    | ResumeControllerAction
    | RestartControllerAction
    | ReconnectControllerAction,
    Field(discriminator="action_type"),
]


class FaultPlan(PlanModel):
    nullable_fields: ClassVar[frozenset[str]] = frozenset({"actions"})
    workload: Nonempty
    actions: list[FaultAction] | None
    sieve_server_addr: Nonempty | None = None
    annotated_reconcile_stack_frame: list[str] | None = None

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_unset=True)


class PlanLoader(yaml.SafeLoader):
    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, object]:
        result: dict[Hashable, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "plan keys must be unique strings",
                    key_node.start_mark,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_plan(value: object) -> FaultPlan:
    return FaultPlan.model_validate(value, by_alias=True, by_name=False)


def load_plan(path: str | Path) -> FaultPlan:
    source = Path(path)
    try:
        return parse_plan(yaml.load(source.read_text(), Loader=PlanLoader))
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"Invalid fault plan {source}: {error}") from error


def write_plan(value: object, path: str | Path) -> None:
    plan = value if isinstance(value, FaultPlan) else parse_plan(value)
    Path(path).write_text(yaml.safe_dump(plan.to_dict(), sort_keys=False))


def single_trigger(
    name: str, condition: Condition, when: Observation, by: str
) -> Trigger:
    return Trigger(
        definitions=[
            TriggerDefinition(
                trigger_name=name,
                condition=condition,
                observation_point=ObservationPoint(when=when, by=by),
            )
        ],
        expression=name,
    )
