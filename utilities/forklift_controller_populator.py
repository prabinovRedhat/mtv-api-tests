"""ForkliftController populator in-flight limit helpers for copy-offload tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import filelock
from kubernetes.dynamic.exceptions import DynamicApiError
from ocp_resources.deployment import Deployment
from ocp_resources.forklift_controller import ForkliftController
from ocp_resources.resource import ResourceEditor
from simple_logger.logger import get_logger
from timeout_sampler import TimeoutExpiredError, TimeoutSampler

if TYPE_CHECKING:
    from kubernetes.dynamic import DynamicClient

LOGGER = get_logger(__name__)

POPULATOR_CONTROLLER_DEPLOYMENT = "forklift-volume-populator-controller"
MAX_POPULATOR_INFLIGHT_ENV = "MAX_POPULATOR_INFLIGHT"
POPULATOR_INFLIGHT_LOCK_TIMEOUT = 3600  # seconds; covers full 7-step class including migration
FORKLIFT_CONTROLLER_CONDITION_TIMEOUT = 300  # seconds to wait for ForkliftController reconciliation
FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME = "forklift-controller-limit-lock"


def parse_forklift_controller_int_field(raw_value: Any, field_name: str) -> int | None:
    """Parse a ForkliftController CR spec integer field.

    Args:
        raw_value (Any): Value from the ForkliftController CR spec.
        field_name (str): Field name, used in the error message.

    Returns:
        int | None: Parsed value, or None when the field is unset.

    Raises:
        ValueError: If the API returns a non-integer value.
    """
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (ValueError, TypeError) as err:
        raise ValueError(f"{field_name} on ForkliftController has non-integer value {raw_value!r}") from err


def _controller_max_populator_inflight_as_int(raw_value: Any) -> int | None:
    """Parse ForkliftController controller_max_populator_inflight as an integer.

    Args:
        raw_value (Any): Value from the ForkliftController CR spec.

    Returns:
        int | None: Parsed limit, or None when the field is unset.

    Raises:
        ValueError: If the API returns a non-integer value.
    """
    return parse_forklift_controller_int_field(raw_value, "controller_max_populator_inflight")


def ensure_secure_shared_lock_dir(lock_dir: Path) -> None:
    """Validate permissions on a cross-worker shared lock directory.

    Opens the directory with ``O_NOFOLLOW`` and validates ownership via ``fstat`` on
    the resulting file descriptor, then sets mode with ``fchmod`` on that fd. This
    avoids acting on a path that was swapped to a symlink after ``mkdir``. A small
    mkdir-to-open window remains; that is acceptable for this pytest-xdist lock path
    under the user's temp directory in controlled CI.

    Args:
        lock_dir (Path): Directory used for pytest-xdist file locks.

    Raises:
        PermissionError: If the directory is a symlink or owned by another user.
    """
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        dir_fd = os.open(str(lock_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as err:
        raise PermissionError(
            f"Security error: cannot open shared directory {lock_dir} without following symlinks "
            f"({err}). This may indicate a hijack attempt."
        ) from err

    try:
        dir_stat = os.fstat(dir_fd)
        current_uid = os.getuid()
        if dir_stat.st_uid != current_uid:
            raise PermissionError(
                f"Security error: shared directory {lock_dir} is owned by uid {dir_stat.st_uid}, "
                f"expected current user uid {current_uid}. This may indicate a hijack attempt."
            )
        os.fchmod(dir_fd, 0o700)
    finally:
        os.close(dir_fd)


def get_forkliftcontroller_populator_inflight_lock_path() -> Path:
    """Return the cross-worker lock path for ForkliftController populator limit changes.

    Returns:
        Path: File lock path under a secured shared temp directory.
    """
    lock_dir = Path(tempfile.gettempdir()) / "pytest-shared-forklift"
    ensure_secure_shared_lock_dir(lock_dir=lock_dir)
    return lock_dir / "populator-inflight.lock"


def _is_not_found_error(err: DynamicApiError) -> bool:
    """Return whether a DynamicApiError indicates a missing resource."""
    return "NotFound" in str(err) or "404" in str(err)


def _is_already_exists_error(err: DynamicApiError) -> bool:
    """Return whether a DynamicApiError indicates resource already exists."""
    return "AlreadyExists" in str(err) or "409" in str(err)


def _extract_configmap_data(configmap_obj: Any) -> dict[str, str]:
    """Extract ConfigMap ``data`` as a dict from dynamic client objects."""
    data = getattr(configmap_obj, "data", None)
    if isinstance(data, dict):
        return data
    if isinstance(configmap_obj, dict):
        raw_data = configmap_obj.get("data")
        if isinstance(raw_data, dict):
            return raw_data
    return {}


@contextmanager
def cluster_forklift_controller_lock(
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
    lock_reason: str,
) -> Generator[None, None, None]:
    """Acquire a cluster-scoped lock for ForkliftController limit mutations.

    Uses a ConfigMap mutex in ``mtv_namespace`` so concurrent jobs on different runners
    cannot patch cluster-global ForkliftController limits at the same time.

    Args:
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where the lock ConfigMap is created.
        lock_reason (str): Human-readable reason included in timeout errors.

    Yields:
        None

    Raises:
        TimeoutError: If cluster lock acquisition times out.
        DynamicApiError: If lock operations fail with an unexpected API error.
    """
    lock_owner = f"{os.uname().nodename}:{os.getpid()}"
    configmap_resource = ocp_admin_client.resources.get(api_version="v1", kind="ConfigMap")
    acquired = False
    lock_state: dict[str, str] = {}

    def _try_acquire_cluster_lock() -> bool:
        nonlocal acquired, lock_state
        lock_data: dict[str, str] = {
            "owner": lock_owner,
            "reason": lock_reason,
            "created_epoch": str(time.time()),
        }
        lock_payload = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME, "namespace": mtv_namespace},
            "data": lock_data,
        }
        try:
            configmap_resource.create(namespace=mtv_namespace, body=lock_payload)
            acquired = True
            lock_state = lock_data
            LOGGER.debug(
                f"Acquired cluster lock {FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME} "
                f"for ForkliftController mutation ({lock_reason})"
            )
            return True
        except DynamicApiError as err:
            if not _is_already_exists_error(err):
                raise

        try:
            existing_lock = configmap_resource.get(
                name=FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME,
                namespace=mtv_namespace,
            )
        except DynamicApiError as err:
            if _is_not_found_error(err):
                return False
            raise

        lock_state = _extract_configmap_data(configmap_obj=existing_lock)
        LOGGER.debug(
            f"Cluster lock {FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME} is held by "
            f"{lock_state.get('owner')!r}; waiting for release"
        )
        return False

    try:
        for lock_acquired in TimeoutSampler(
            wait_timeout=POPULATOR_INFLIGHT_LOCK_TIMEOUT,
            sleep=2,
            func=_try_acquire_cluster_lock,
        ):
            if lock_acquired:
                yield
                return
    except TimeoutExpiredError as err:
        current_owner = lock_state.get("owner")
        current_reason = lock_state.get("reason")
        raise TimeoutError(
            f"Timeout ({POPULATOR_INFLIGHT_LOCK_TIMEOUT}s) waiting for cluster lock "
            f"{FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME} in namespace {mtv_namespace} "
            f"(owner={current_owner!r}, reason={current_reason!r}). "
            f"Another job may be running {lock_reason}."
        ) from err
    finally:
        if not acquired:
            return
        try:
            current_lock = configmap_resource.get(
                name=FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME,
                namespace=mtv_namespace,
            )
        except DynamicApiError as err:
            if _is_not_found_error(err):
                return
            raise

        current_lock_data = _extract_configmap_data(configmap_obj=current_lock)
        if current_lock_data.get("owner") != lock_owner:
            LOGGER.warning(
                f"Cluster lock {FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME} owner changed from "
                f"{lock_owner!r} to {current_lock_data.get('owner')!r}; skipping release delete"
            )
            return

        try:
            configmap_resource.delete(
                name=FORKLIFT_CONTROLLER_CLUSTER_LOCK_NAME,
                namespace=mtv_namespace,
            )
        except DynamicApiError as err:
            if not _is_not_found_error(err):
                raise


@contextmanager
def forklift_controller_lock(
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
    lock_reason: str,
    forklift_controller_name: str = "forklift-controller",
) -> Generator[ForkliftController, None, None]:
    """Yield ForkliftController under local and cluster-scoped locks.

    Args:
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where ForkliftController is installed.
        lock_reason (str): Human-readable reason included in timeout errors.
        forklift_controller_name (str): ForkliftController resource name.

    Yields:
        ForkliftController: ForkliftController resource after RUNNING condition is observed.

    Raises:
        TimeoutError: If lock acquisition times out.
        DynamicApiError: If cluster lock operations fail unexpectedly.
    """
    lock_path = get_forkliftcontroller_populator_inflight_lock_path()
    try:
        with filelock.FileLock(lock_path, timeout=POPULATOR_INFLIGHT_LOCK_TIMEOUT):
            with cluster_forklift_controller_lock(
                ocp_admin_client=ocp_admin_client,
                mtv_namespace=mtv_namespace,
                lock_reason=lock_reason,
            ):
                forklift_controller = ForkliftController(
                    client=ocp_admin_client,
                    name=forklift_controller_name,
                    namespace=mtv_namespace,
                    ensure_exists=True,
                )
                forklift_controller.wait_for_condition(
                    status=forklift_controller.Condition.Status.TRUE,
                    condition=forklift_controller.Condition.Type.RUNNING,
                    timeout=FORKLIFT_CONTROLLER_CONDITION_TIMEOUT,
                )
                yield forklift_controller
    except filelock.Timeout as err:
        raise TimeoutError(
            f"Timeout ({POPULATOR_INFLIGHT_LOCK_TIMEOUT}s) waiting for ForkliftController lock at {lock_path}. "
            f"Another worker may be running {lock_reason}."
        ) from err


def get_populator_inflight_from_deployment(deployment: Deployment) -> str | None:
    """Read MAX_POPULATOR_INFLIGHT from the populator controller deployment.

    Args:
        deployment (Deployment): Populator controller deployment resource.

    Returns:
        str | None: Configured in-flight limit, or None if the env var is absent.
    """
    containers = deployment.instance.spec.template.spec.containers
    for container in containers:
        for env_var in container.env or []:
            if env_var.name == MAX_POPULATOR_INFLIGHT_ENV:
                return env_var.value
    return None


def wait_for_populator_inflight_deployment(
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
    expected_limit: int,
) -> None:
    """Wait until the populator controller deployment applies the in-flight limit.

    ForkliftController spec changes propagate to the populator-controller Deployment
    asynchronously. Migration must not start until MAX_POPULATOR_INFLIGHT is active.

    Args:
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where the populator controller runs.
        expected_limit (int): Expected MAX_POPULATOR_INFLIGHT value.

    Raises:
        TimeoutError: If the deployment does not reach the expected limit in time.
    """
    expected_value = str(expected_limit)

    def _deployment_ready_with_limit() -> bool:
        current_deployment = Deployment(
            client=ocp_admin_client,
            name=POPULATOR_CONTROLLER_DEPLOYMENT,
            namespace=mtv_namespace,
            ensure_exists=True,
        )
        deployment_status = current_deployment.instance.status
        spec_replicas = current_deployment.instance.spec.replicas or 1
        available_replicas = 0
        if deployment_status:
            available_replicas = deployment_status.availableReplicas or 0
        if available_replicas < spec_replicas:
            return False
        return get_populator_inflight_from_deployment(deployment=current_deployment) == expected_value

    try:
        for ready in TimeoutSampler(
            wait_timeout=FORKLIFT_CONTROLLER_CONDITION_TIMEOUT,
            sleep=2,
            func=_deployment_ready_with_limit,
        ):
            if ready:
                LOGGER.info(
                    f"Populator controller deployment has {MAX_POPULATOR_INFLIGHT_ENV}={expected_value} "
                    "and is fully available"
                )
                return
    except TimeoutExpiredError as err:
        final_deployment = Deployment(
            client=ocp_admin_client,
            name=POPULATOR_CONTROLLER_DEPLOYMENT,
            namespace=mtv_namespace,
            ensure_exists=True,
        )
        current_limit = get_populator_inflight_from_deployment(deployment=final_deployment)
        raise TimeoutError(
            f"Timed out waiting for {POPULATOR_CONTROLLER_DEPLOYMENT} to apply "
            f"{MAX_POPULATOR_INFLIGHT_ENV}={expected_value} (current={current_limit!r})"
        ) from err


def get_deployment_populator_inflight_limit(
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
) -> int:
    """Read MAX_POPULATOR_INFLIGHT from the populator controller deployment.

    Args:
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where the populator controller runs.

    Returns:
        int: Configured in-flight limit from the deployment env var.

    Raises:
        ValueError: If MAX_POPULATOR_INFLIGHT is missing on the deployment.
    """
    deployment = Deployment(
        client=ocp_admin_client,
        name=POPULATOR_CONTROLLER_DEPLOYMENT,
        namespace=mtv_namespace,
        ensure_exists=True,
    )
    limit_str = get_populator_inflight_from_deployment(deployment=deployment)
    if limit_str is None:
        raise ValueError(
            f"{MAX_POPULATOR_INFLIGHT_ENV} not found on {POPULATOR_CONTROLLER_DEPLOYMENT} "
            f"before populator throttling test setup"
        )
    try:
        return int(limit_str)
    except (ValueError, TypeError) as err:
        raise ValueError(
            f"{MAX_POPULATOR_INFLIGHT_ENV} on {POPULATOR_CONTROLLER_DEPLOYMENT} has non-integer value {limit_str!r}"
        ) from err


def get_cr_populator_inflight_limit(forklift_controller: ForkliftController) -> int | None:
    """Return controller_max_populator_inflight from the ForkliftController CR as an integer.

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to read.

    Returns:
        int | None: Parsed limit, or None when the field is unset.

    Raises:
        ValueError: If the API returns a non-integer value.
    """
    raw_limit = getattr(forklift_controller.instance.spec, "controller_max_populator_inflight", None)
    return _controller_max_populator_inflight_as_int(raw_limit)


def _warn_if_cr_limit_leftover_from_crashed_run(
    cr_limit_int: int | None,
    test_limit: int,
    original_deployment_limit: int,
) -> None:
    """Log when the CR limit matches the test value but the deployment reports a different limit.

    Args:
        cr_limit_int (int | None): Parsed CR limit before patching.
        test_limit (int): Limit applied for the test.
        original_deployment_limit (int): MAX_POPULATOR_INFLIGHT value before the test.
    """
    if cr_limit_int == test_limit:
        LOGGER.warning(
            f"ForkliftController controller_max_populator_inflight already at test limit {test_limit} "
            f"but deployment reports {original_deployment_limit}; "
            "this may be leftover from a previous crashed run"
        )


def _ensure_forklift_controller_populator_limit(
    forklift_controller: ForkliftController,
    target_limit: int,
) -> None:
    """Patch ForkliftController populator limit when it differs from the target.

    Uses a non-restoring ResourceEditor update (``backup_resources=False``). Callers that
    need restore on exit must patch back explicitly, as ``populator_inflight_limit`` does
    in its ``finally`` block.

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to patch.
        target_limit (int): Desired controller_max_populator_inflight value.
    """
    if get_cr_populator_inflight_limit(forklift_controller=forklift_controller) == target_limit:
        return

    ResourceEditor(patches={forklift_controller: {"spec": {"controller_max_populator_inflight": target_limit}}}).update(
        backup_resources=False
    )
    forklift_controller.wait_for_condition(
        status=forklift_controller.Condition.Status.TRUE,
        condition=forklift_controller.Condition.Type.SUCCESSFUL,
        timeout=FORKLIFT_CONTROLLER_CONDITION_TIMEOUT,
    )


@contextmanager
def populator_inflight_limit(
    forklift_controller: ForkliftController,
    ocp_admin_client: DynamicClient,
    mtv_namespace: str,
    test_limit: int,
    original_deployment_limit: int,
) -> Generator[None, None, None]:
    """Temporarily patch ForkliftController populator in-flight limit and restore on exit.

    Patches the CR to ``test_limit`` without ResourceEditor auto-restore; ``finally``
    restores ``original_deployment_limit`` (from deployment MAX_POPULATOR_INFLIGHT at
    setup) and waits for the populator deployment to reconcile, including after test
    failures.

    Args:
        forklift_controller (ForkliftController): ForkliftController resource to patch.
        ocp_admin_client (DynamicClient): OpenShift admin client.
        mtv_namespace (str): Namespace where the populator controller runs.
        test_limit (int): Limit to apply for the test (e.g. POPULATOR_INFLIGHT_LIMIT).
        original_deployment_limit (int): MAX_POPULATOR_INFLIGHT value before the test.
    """
    cr_limit_int = get_cr_populator_inflight_limit(forklift_controller=forklift_controller)

    # Early return when CR and deployment already match the test and restore targets.
    if cr_limit_int == test_limit == original_deployment_limit:
        LOGGER.info(f"ForkliftController controller_max_populator_inflight already {test_limit}")
        wait_for_populator_inflight_deployment(
            ocp_admin_client=ocp_admin_client,
            mtv_namespace=mtv_namespace,
            expected_limit=test_limit,
        )
        yield
        return

    _warn_if_cr_limit_leftover_from_crashed_run(
        cr_limit_int=cr_limit_int,
        test_limit=test_limit,
        original_deployment_limit=original_deployment_limit,
    )

    LOGGER.info(f"Setting ForkliftController controller_max_populator_inflight from {cr_limit_int!r} to {test_limit}")
    try:
        _ensure_forklift_controller_populator_limit(
            forklift_controller=forklift_controller,
            target_limit=test_limit,
        )
        wait_for_populator_inflight_deployment(
            ocp_admin_client=ocp_admin_client,
            mtv_namespace=mtv_namespace,
            expected_limit=test_limit,
        )
        yield
    finally:
        pending_exc = sys.exc_info()
        try:
            _ensure_forklift_controller_populator_limit(
                forklift_controller=forklift_controller,
                target_limit=original_deployment_limit,
            )
            wait_for_populator_inflight_deployment(
                ocp_admin_client=ocp_admin_client,
                mtv_namespace=mtv_namespace,
                expected_limit=original_deployment_limit,
            )
        except (TimeoutError, ValueError) as err:
            LOGGER.exception(
                f"Failed to restore ForkliftController populator limit to {original_deployment_limit} during cleanup"
            )
            if pending_exc[0] is None:
                raise err
