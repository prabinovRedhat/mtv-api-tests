"""ForkliftController VM in-flight limit helpers for copy-offload VM throttling tests."""

from __future__ import annotations

import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from kubernetes.dynamic.exceptions import DynamicApiError
from ocp_resources.forklift_controller import ForkliftController
from ocp_resources.resource import ResourceEditor
from simple_logger.logger import get_logger

from utilities.forklift_controller_populator import (
    FORKLIFT_CONTROLLER_CONDITION_TIMEOUT,
    parse_forklift_controller_int_field,
    wait_for_populator_inflight_deployment,
)

if TYPE_CHECKING:
    from kubernetes.dynamic import DynamicClient

LOGGER = get_logger(__name__)


def _controller_max_vm_inflight_as_int(raw_value: Any) -> int | None:
    """Parse ForkliftController controller_max_vm_inflight as an integer.

    Args:
        raw_value (Any): Value from the ForkliftController CR spec.

    Returns:
        int | None: Parsed limit, or None when the field is unset.

    Raises:
        ValueError: If the API returns a non-integer value.
    """
    return parse_forklift_controller_int_field(raw_value, "controller_max_vm_inflight")


def get_cr_vm_inflight_limit(forklift_controller: ForkliftController) -> int | None:
    """Return controller_max_vm_inflight from the ForkliftController CR as an integer.

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to read.

    Returns:
        int | None: Parsed limit, or None when the field is unset.

    Raises:
        ValueError: If the API returns a non-integer value.
    """
    raw_limit = getattr(forklift_controller.instance.spec, "controller_max_vm_inflight", None)
    return _controller_max_vm_inflight_as_int(raw_limit)


def _warn_if_cr_vm_limit_leftover_from_crashed_run(
    cr_vm_limit: int | None,
    test_vm_limit: int,
) -> None:
    """Log when the CR vm limit already matches the test value before patching.

    Args:
        cr_vm_limit (int | None): Parsed CR controller_max_vm_inflight before patching.
        test_vm_limit (int): Limit applied for the test.
    """
    if cr_vm_limit == test_vm_limit:
        LOGGER.warning(
            f"ForkliftController controller_max_vm_inflight already at test limit {test_vm_limit}; "
            "this may be leftover from a previous crashed run"
        )


def _patch_forkliftcontroller_limits(
    forklift_controller: ForkliftController,
    vm_limit: int | None,
    populator_limit: int | None,
) -> None:
    """Patch ForkliftController vm and populator in-flight limits in a single API call.

    Uses a non-restoring ResourceEditor update (``backup_resources=False``). Callers that
    need restore on exit must patch back explicitly, as ``vm_throttle_limits`` does in its
    ``finally`` block.

    A ``vm_limit`` of ``None`` removes the ``controller_max_vm_inflight`` field (restoring
    the Forklift operator default).

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to patch.
        vm_limit (int | None): Desired controller_max_vm_inflight value, or None to remove.
        populator_limit (int | None): Desired controller_max_populator_inflight value, or None to remove.
    """
    ResourceEditor(
        patches={
            forklift_controller: {
                "spec": {
                    "controller_max_vm_inflight": vm_limit,
                    "controller_max_populator_inflight": populator_limit,
                }
            }
        }
    ).update(backup_resources=False)
    forklift_controller.wait_for_condition(
        status=forklift_controller.Condition.Status.TRUE,
        condition=forklift_controller.Condition.Type.SUCCESSFUL,
        timeout=FORKLIFT_CONTROLLER_CONDITION_TIMEOUT,
    )


@contextmanager
def vm_throttle_limits(
    forklift_controller: ForkliftController,
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
    test_vm_limit: int,
    test_populator_limit: int,
    original_vm_limit: int | None,
    original_cr_populator_limit: int | None,
    original_deployment_populator_limit: int,
) -> Generator[None, None, None]:
    """Temporarily patch ForkliftController VM and populator in-flight limits and restore on exit.

    Sets ``controller_max_vm_inflight`` to ``test_vm_limit`` and
    ``controller_max_populator_inflight`` to ``test_populator_limit`` in a single patch.
    Restores ``original_vm_limit`` and ``original_cr_populator_limit`` in the ``finally`` block,
    including after test failures, then waits for populator deployment reconciliation.

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to patch.
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where the populator controller runs.
        test_vm_limit (int): controller_max_vm_inflight to apply for the test.
        test_populator_limit (int): controller_max_populator_inflight to apply for the test.
        original_vm_limit (int | None): controller_max_vm_inflight value before the test,
            or None if the field was unset (removes the field on restore).
        original_cr_populator_limit (int | None): controller_max_populator_inflight CR value
            before the test, or None if the field was unset.
        original_deployment_populator_limit (int): MAX_POPULATOR_INFLIGHT deployment value
            before the test; used for populator deployment reconciliation wait.

    Raises:
        TimeoutError: If deployment reconciliation does not complete for either apply or restore.
        ValueError: If the controller spec contains invalid values or restore cannot be completed.
        ConnectionError: If API communication fails while applying or restoring limits.
        DynamicApiError: If the dynamic Kubernetes API fails while applying or restoring limits.
    """
    _warn_if_cr_vm_limit_leftover_from_crashed_run(
        cr_vm_limit=get_cr_vm_inflight_limit(forklift_controller=forklift_controller),
        test_vm_limit=test_vm_limit,
    )

    LOGGER.info(
        f"Setting ForkliftController controller_max_vm_inflight={test_vm_limit}, "
        f"controller_max_populator_inflight={test_populator_limit}"
    )
    try:
        _patch_forkliftcontroller_limits(
            forklift_controller=forklift_controller,
            vm_limit=test_vm_limit,
            populator_limit=test_populator_limit,
        )
        wait_for_populator_inflight_deployment(
            ocp_admin_client=ocp_admin_client,
            mtv_namespace=mtv_namespace,
            expected_limit=test_populator_limit,
        )
        yield
    finally:
        pending_exc = sys.exc_info()
        restored_expected_limit = (
            original_deployment_populator_limit
            if original_cr_populator_limit is None
            else original_cr_populator_limit
        )
        LOGGER.info(
            f"Restoring ForkliftController controller_max_vm_inflight={original_vm_limit!r}, "
            f"controller_max_populator_inflight={original_cr_populator_limit!r}"
        )
        try:
            _patch_forkliftcontroller_limits(
                forklift_controller=forklift_controller,
                vm_limit=original_vm_limit,
                populator_limit=original_cr_populator_limit,
            )
            wait_for_populator_inflight_deployment(
                ocp_admin_client=ocp_admin_client,
                mtv_namespace=mtv_namespace,
                expected_limit=restored_expected_limit,
            )
        except (TimeoutError, ValueError, ConnectionError, DynamicApiError) as err:
            LOGGER.exception(
                f"Failed to restore ForkliftController limits "
                f"(vm={original_vm_limit!r}, populator={original_cr_populator_limit!r}) during cleanup"
            )
            if pending_exc[0] is None:
                raise
