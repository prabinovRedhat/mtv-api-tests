"""
Copy-offload utility constants.

This module contains constants used for copy-offload functionality validation.
"""

# Supported storage vendors for copy-offload functionality
# Immutable tuple to prevent accidental modification
SUPPORTED_VENDORS = (
    "ontap",
    "vantara",
    "primera3par",
    "pureFlashArray",
    "powerflex",
    "powermax",
    "powerstore",
    "infinibox",
    "flashsystem",
)

# MTV-696 baseline: force visible populator throttling signals in the dedicated
# populator-throttling test class.
POPULATOR_INFLIGHT_LIMIT = 2

# MTV-777 baseline: VM scheduler throttling target in the VM-throttling class.
VM_INFLIGHT_LIMIT = 1
# MTV-777 companion value: keep populator limit higher while testing VM limit so
# VM inflight is the primary bottleneck under test.
VM_THROTTLE_POPULATOR_INFLIGHT = 3

SOURCE_HOST_LABEL = "sourceHost"
PVC_NAME_LABEL = "cdi.kubevirt.io/storage.populator.pvcPrimeName"
POPULATOR_THROTTLED_EVENT_REASON = "PopulatorThrottled"
FORKLIFT_CONTROLLER_NAME = "forklift-controller"
