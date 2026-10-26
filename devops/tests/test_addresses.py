from unittest.mock import Mock

import pytest
from crux_devops.addresses import manage


def test_create_recovers_named_address_without_allocating_twice():
    ec2=Mock()
    ec2.describe_addresses.return_value={'Addresses':[{'AllocationId':'eipalloc-123','Tags':[{'Key':'CruxRole','Value':'shared-eip'}]}]}
    assert manage(ec2,'create',name='shared')['AllocationId']=='eipalloc-123'
    ec2.allocate_address.assert_not_called()


@pytest.mark.parametrize('address',[
    {'AllocationId':'eipalloc-123','AssociationId':'association','Tags':[{'Key':'CruxRole','Value':'shared-eip'}]},
    {'AllocationId':'eipalloc-123','Tags':[{'Key':'CruxRole','Value':'run'}]},
])
def test_delete_refuses_attached_or_run_owned_addresses(address):
    ec2=Mock()
    ec2.describe_addresses.return_value={'Addresses':[address]}
    with pytest.raises(ValueError):
        manage(ec2,'delete',allocation='eipalloc-123',confirmed=True)
    ec2.release_address.assert_not_called()
