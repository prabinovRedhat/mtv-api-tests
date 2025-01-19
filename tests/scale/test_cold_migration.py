import pytest as pytest
from utilities.mtv_migration import migrate_vms



@pytest.mark.scale
def test_mtv_migration_scale(
    target_namespace,
    plans_scale,
    source_provider,
    source_provider_data,
    destination_provider,
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
        target_namespace=target_namespace,
    )

