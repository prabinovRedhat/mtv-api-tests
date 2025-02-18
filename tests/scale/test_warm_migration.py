import pytest as pytest
from utilities.mtv_migration import migrate_vms, get_cutover_value



@pytest.mark.warmscale
def test_mtv_migration_scale_warm(
    target_namespace,
    plans_scale,
    source_provider,
    source_provider_data,
    destination_provider,
    precopy_interval_forkliftcontroller,
    network_migration_map,
    storage_migration_map,
):
    migrate_vms(
        source_provider=source_provider,
        destination_provider=destination_provider,
        plans=plans_scale,
        network_migration_map=network_migration_map,
        storage_migration_map=storage_migration_map,
        source_provider_data=source_provider_data,
        cut_over=get_cutover_value(),
        target_namespace=target_namespace,
    )


