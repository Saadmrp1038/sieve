"""Human-readable alarm reporting; no comparison decisions are made here."""

from sieve_tool.common.common import TestContext, bcolors, cprint
from sieve_tool.events import Object
from sieve_tool.plans import load_plan
from sieve_tool.results import TestResult


def convert_occurrence(occurrence: str) -> str:
    if occurrence.endswith("1"):
        return occurrence + "st"
    elif occurrence.endswith("2"):
        return occurrence + "nd"
    elif occurrence.endswith("3"):
        return occurrence + "rd"
    else:
        return occurrence + "th"


def generate_perturbation_description(test_context: TestContext) -> str:
    test_plan_content: Object = load_plan(test_context.test_plan).to_dict()
    if test_plan_content["actions"] is None:
        return "Sieve does not perform any actions."
    desc = ""
    controller_pod_label = test_context.controller_config.controller_pod_label
    for action in test_plan_content["actions"]:
        action_type = action["actionType"]
        action_desc = "Sieve "
        if action_type == "pauseAPIServer":
            action_desc += "pauses the API server {}".format(action["apiServerName"])
        elif action_type == "resumeAPIServer":
            action_desc += "resumes the API server {}".format(action["apiServerName"])
        elif action_type == "pauseController":
            action_desc += "pauses the controller {} ".format(controller_pod_label)
            pause_at = action["pauseAt"]
            if pause_at == "beforeControllerRead":
                action_desc += "before the controller reads "
            elif pause_at == "afterControllerRead":
                action_desc += "after the controller reads "
            elif pause_at == "beforeControllerWrite":
                action_desc += "before the controller writes "
            elif pause_at == "afterControllerWrite":
                action_desc += "after the controller writes "
            else:
                raise AssertionError()
            if "pauseScope" not in action:
                action_desc += "any object"
            elif action["pauseScope"] == "all":
                action_desc += "any object"
            else:
                action_desc += action["pauseScope"]
        elif action_type == "resumeController":
            action_desc += "resumes the controller {}".format(controller_pod_label)
        elif action_type == "restartController":
            action_desc += "restarts the controller {}".format(controller_pod_label)
        elif action_type == "reconnectController":
            action_desc += "reconnects the controller {} from API server {} to API server {}".format(
                controller_pod_label,
                test_context.common_config.leading_api,
                action["reconnectAPIServer"],
            )
        else:
            raise AssertionError()
        action_desc += " when the trigger expression {} is satisfied, where\n".format(
            action["trigger"]["expression"]
        )
        for trigger in action["trigger"]["definitions"]:
            action_desc += "{} is satisfied ".format(trigger["triggerName"])
            cond_type = trigger["condition"]["conditionType"]
            if cond_type == "onTimeout":
                action_desc += "by a {}-second timeout.\n".format(
                    trigger["condition"]["timeoutValue"]
                )
            elif cond_type == "onAnnotatedAPICall":
                module = trigger["condition"]["module"]
                file_path = trigger["condition"]["filePath"]
                receiver_type = trigger["condition"]["receiverType"]
                fun_name = trigger["condition"]["funName"]
                occurrence = trigger["condition"]["occurrence"]
                observed_when = trigger["observationPoint"]["when"]
                if observed_when == "beforeAnnotatedAPICall":
                    action_desc += (
                        "before the annotated API {}.{} (in {} from module {})".format(
                            receiver_type, fun_name, file_path, module
                        )
                    )
                elif observed_when == "afterAnnotatedAPICall":
                    action_desc += (
                        "after the annotated API {}.{} (in {} from module {})".format(
                            receiver_type, fun_name, file_path, module
                        )
                    )
                else:
                    raise AssertionError()
                action_desc += " is called with the {} occurrence.\n".format(
                    convert_occurrence(str(occurrence))
                )
            else:
                observed_when = trigger["observationPoint"]["when"]
                resource_key = trigger["condition"]["resourceKey"]
                occurrence = trigger["condition"]["occurrence"]
                if observed_when == "beforeAPIServerRecv":
                    action_desc += (
                        "before the API server {} receives the event:\n".format(
                            trigger["observationPoint"]["by"]
                        )
                    )
                elif observed_when == "afterAPIServerRecv":
                    action_desc += (
                        "after the API server {} receives the event:\n".format(
                            trigger["observationPoint"]["by"]
                        )
                    )
                elif observed_when == "beforeControllerRecv":
                    action_desc += (
                        "before the controller {} receives the event:\n".format(
                            controller_pod_label
                        )
                    )
                elif observed_when == "afterControllerRecv":
                    action_desc += (
                        "after the controller {} receives the event:\n".format(
                            controller_pod_label
                        )
                    )
                elif observed_when == "beforeControllerWrite":
                    action_desc += "before the controller {} issues:\n".format(
                        controller_pod_label
                    )
                elif observed_when == "afterControllerWrite":
                    action_desc += "after the controller {} issues:\n".format(
                        controller_pod_label
                    )
                if cond_type == "onObjectCreate":
                    action_desc += "create {} ".format(resource_key)
                elif cond_type == "onObjectDelete":
                    action_desc += "delete {} ".format(resource_key)
                elif cond_type == "onObjectUpdate":
                    action_desc += "update {} ".format(resource_key)
                    if "prevStateDiff" in trigger["condition"]:
                        action_desc += "from\n{}\nto\n{}\n".format(
                            trigger["condition"]["prevStateDiff"],
                            trigger["condition"]["curStateDiff"],
                        )
                elif cond_type == "onAnyFieldModification":
                    action_desc += (
                        "update {} so that any field in\n{}\nis modified ".format(
                            resource_key, trigger["condition"]["prevStateDiff"]
                        )
                    )
                action_desc += "with the {} occurrence.\n".format(
                    convert_occurrence(str(occurrence))
                )
        desc += action_desc
    return desc


def print_error_and_debugging_info(
    test_context: TestContext, test_result: TestResult
) -> None:
    if not test_result.injection_completed:
        cprint("injection is not completed", bcolors.WARNING)

    if not test_result.workload_completed:
        cprint("workload is not completed", bcolors.WARNING)

    if len(test_result.common_errors) > 0:
        cprint(
            "{} detected errors as follows".format(len(test_result.common_errors)),
            bcolors.FAIL,
        )
        cprint("\n".join(test_result.common_errors) + "\n", bcolors.FAIL)

    if len(test_result.end_state_errors) > 0:
        cprint(
            "{} detected end state inconsistencies as follows".format(
                len(test_result.end_state_errors)
            ),
            bcolors.FAIL,
        )
        cprint("\n".join(test_result.end_state_errors) + "\n", bcolors.FAIL)

    if len(test_result.history_errors) > 0:
        cprint(
            "{} detected history inconsistencies as follows".format(
                len(test_result.history_errors)
            ),
            bcolors.FAIL,
        )
        cprint("\n".join(test_result.history_errors) + "\n", bcolors.FAIL)

    if test_context.common_config.generate_debugging_information_enabled:
        desc = "[PERTURBATION DESCRIPTION]\n" + generate_perturbation_description(
            test_context
        )
        print(desc)
