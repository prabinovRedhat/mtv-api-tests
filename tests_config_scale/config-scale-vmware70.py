global config
source_providers_list = [
    {
    "type": "vsphere",
    "version": "7.0.3",
    "fqdn": "rhev-node-13.rdu2.scalelab.redhat.com",
    "api_url": "https://rhev-node-13.rdu2.scalelab.redhat.com/sdk",
    "username": "administrator@vsphere.local",
    "password": "Power123!",
    "admin_username": "administrator@vsphere.local",
    "admin_password": "Power123!",
    "cluster_name": "MTV",
    "default": "True",
    "vddk_init_image": "quay.io/rh-openshift-mtv/vddk-init-image:7.0.3",
    "host_list": [
            {
                "migration_host_id": "host-1004",
                "migration_host_ip": "10.1.38.137",
                "user": "root",
                "password": "<REDACTED>8!",
                "default": "True",
            }
    ],
    "networks": [{ "name": "VM Network" }, { "name": "Mgmt Network" }],
    "storages": [{ "name": "v2v_general_porpuse_FC_DC" }],
    "vm_folder": "warm-testing-3disks",
    "storage_class": "ocs-storagecluster-ceph-rbd",
    "storages.name": "v2v_general_porpuse_FC_DC",
    "source_provider_type": "vsphere",
    "source_provider_version": "7.0.3",
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
source_provider_version = "7.0.3"
vm_name_search_pattern = "rhel79-50gb-70usage-vm-"
create_scale_report = True
plan_wait_timeout = 3600

insecure_verify_skip = "true"
number_of_vms = 1
warm_migration = False
check_vms_signals = True
target_namespace = "mtv-api-tests"
mtv_namespace = "openshift-mtv"
list_of_vms_csv = ""
turn_on_vms = False
remote_ocp_cluster = ""
snapshots_interval = 2
mins_before_cutover = 5
skip_migration = False
matrix_test = True
release_test = False
target_ocp_version = "4.17"
mount_root = ""


for _dir in dir():
    val = locals()[_dir]
    if type(val) not in [bool, list, dict, str, int]:
        continue

    if _dir in ["encoding", "py_file"]:
        continue

    config[_dir] = locals()[_dir]  # type: ignore # noqa: F821
