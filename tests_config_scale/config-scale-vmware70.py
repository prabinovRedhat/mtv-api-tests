global config
source_providers_list = [
    {
    "type": "vsphere",
    "version": "7.3",
    "fqdn": "rhev-node-13.rdu2.scalelab.redhat.com",
    "api_url": "https://rhev-node-13.rdu2.scalelab.redhat.com/sdk",
    "username": "administrator@vsphere.local",
    "password": "Power123!",
    "admin_username": "administrator@vsphere.local",
    "admin_password": "Power123!",
    "cluster_name": "MTV",
    "default": "True",
    "vddk_init_image": "quay.io/qiyuan1/test7",
    "networks": [{ "name": "VM Network" }, { "name": "Mgmt Network" }],
    "storages": [{ "name": "v2v_general_porpuse_FC_DC" }],
    "vm_folder": "warm-testing-3disks",
    "storage_class": "ocs-storagecluster-ceph-rbd",
    "storages.name": "v2v_general_porpuse_FC_DC",
    "source_provider_type": "vsphere",
    "source_provider_version": "7.3",
    "target_namespace": "mtv-api-test",
    "mtv_namespace": "openshift-mtv",
    "vm_name_search_pattern": "rhel79-50gb-70usage-vm-",
    "number_of_vms": 4,
    "warm_migration": False,
    "check_vms_signals": False,
    "turn_on_vms":  False,
    "create_scale_report": True,
    "plan_wait_timeout": 3600,
    "insecure_verify_skip": True
}
]
storage_class = "ocs-storagecluster-ceph-rbd"
source_provider_type = "vsphere"
source_provider_version = "7.3"
target_namespace = "mtv-api-testing"
mtv_namespace = "openshift-mtv"
vm_name_search_pattern = "rhel79-50gb-70usage-vm-"
number_of_vms = 2
warm_migration = False
check_vms_signals = False
turn_on_vms = False
create_scale_report = True
plan_wait_timeout = 3600


for _dir in dir():
    val = locals()[_dir]
    if type(val) not in [bool, list, dict, str, int]:
        continue

    if _dir in ["encoding", "py_file"]:
        continue

    config[_dir] = locals()[_dir]  # type: ignore # noqa: F821
