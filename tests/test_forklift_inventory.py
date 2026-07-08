"""Unit tests for forklift_inventory module."""

from unittest.mock import Mock, patch
import pytest
from timeout_sampler import TimeoutExpiredError

from libs.forklift_inventory import VsphereForkliftInventory


@pytest.fixture
def mock_vsphere_inventory():
    """Create a mocked VsphereForkliftInventory instance."""
    with patch("libs.forklift_inventory.Route"):
        client = Mock()
        inventory = VsphereForkliftInventory(client=client, provider_name="test-provider", namespace="test-namespace")
        return inventory


def test_hosts_property(mock_vsphere_inventory):
    """Test hosts property calls correct API endpoint."""
    mock_vsphere_inventory.provider_url_path = "vsphere/test-provider-id"
    mock_vsphere_inventory._request = Mock(return_value=[{"id": "host1", "name": "test-host"}])

    hosts = mock_vsphere_inventory.hosts

    mock_vsphere_inventory._request.assert_called_once_with(url_path="vsphere/test-provider-id/hosts")
    assert hosts == [{"id": "host1", "name": "test-host"}]


def test_wait_for_hosts_success(mock_vsphere_inventory):
    """Test wait_for_hosts succeeds when hosts are available."""
    mock_hosts = [{"id": "host1", "name": "test-host"}]

    with patch.object(mock_vsphere_inventory, "hosts", mock_hosts):
        with patch("libs.forklift_inventory.TimeoutSampler") as mock_sampler:
            mock_sampler.return_value = [mock_hosts]

            result = mock_vsphere_inventory.wait_for_hosts(timeout=60, sleep=5)

            assert result == mock_hosts


def test_wait_for_hosts_timeout(mock_vsphere_inventory):
    """Test wait_for_hosts raises TimeoutExpiredError on timeout."""
    with patch.object(mock_vsphere_inventory, "hosts", []):
        with patch("libs.forklift_inventory.TimeoutSampler") as mock_sampler:
            mock_sampler.side_effect = TimeoutExpiredError("timeout")

            with pytest.raises(TimeoutExpiredError, match="No hosts appeared in Forklift inventory"):
                mock_vsphere_inventory.wait_for_hosts(timeout=60)


def test_wait_for_hosts_empty_response(mock_vsphere_inventory):
    """Test wait_for_hosts continues waiting when hosts list is empty."""
    filled_hosts = [{"id": "host1", "name": "test-host"}]

    with patch.object(mock_vsphere_inventory, "hosts", filled_hosts):
        with patch("libs.forklift_inventory.TimeoutSampler") as mock_sampler:
            # Mock TimeoutSampler to yield None first (empty), then filled_hosts
            mock_sampler.return_value = [None, filled_hosts]

            result = mock_vsphere_inventory.wait_for_hosts()

            # Should get the non-empty result
            assert result == filled_hosts
