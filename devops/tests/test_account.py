"""Named installation discovery, recovery and ownership regression tests."""
from unittest.mock import Mock

import pytest
from crux_devops.account import discover, ensure_stack


def client(stack=None):
    """Return a CloudFormation fixture listing at most one named installation."""
    result=Mock()
    result.get_paginator.return_value.paginate.return_value=[{'Stacks':[stack] if stack else []}]
    result.describe_stacks.return_value={'Stacks':[{'Outputs':[{'OutputKey':'VpcId','OutputValue':'vpc-test'}]}]}
    return result


def test_create_uses_shared_installation_tag_and_returns_outputs():
    """Setup creates discoverable infrastructure without a laptop ownership file."""
    api=client()
    assert ensure_stack(api,{},[])=={'VpcId':'vpc-test'}
    assert api.create_stack.call_args.kwargs['Tags']==[{'Key':'CruxInstallation','Value':'crux-account'}]


@pytest.mark.parametrize("status", ["CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"])
def test_retry_from_another_laptop_does_not_recreate_installation(status):
    """Identical setup needs only AWS resources, never a saved local receipt."""
    api=client({'StackName':'crux-account','StackStatus':status,
                'Tags':[{'Key':'CruxInstallation','Value':'crux-account'}],'Parameters':[]})
    api.get_template.return_value={'TemplateBody':{}}
    ensure_stack(api,{},[])
    api.create_stack.assert_not_called()
    api.update_stack.assert_not_called()


def test_foreign_stack_is_not_adopted():
    """An untagged name collision does not authorize installation updates."""
    api=client({'StackName':'crux-account','StackStatus':'CREATE_COMPLETE','Tags':[]})
    with pytest.raises(ValueError,match='ownership'):
        ensure_stack(api,{},[])
    api.create_stack.assert_not_called()


def test_deploy_reads_live_named_stack_outputs():
    """Deploy resolves current cloud output values without a local account file."""
    session=Mock()
    cf=Mock()
    ssm=Mock()
    ssm.get_parameter.return_value={'Parameter':{'Value':'ami-test'}}
    session.client.side_effect=lambda service: cf if service=='cloudformation' else ssm
    cf.describe_stacks.return_value={'Stacks':[{'StackName':'crux-account','StackStatus':'CREATE_COMPLETE',
        'Tags':[{'Key':'CruxInstallation','Value':'crux-account'}], 'Parameters':[],
        'Outputs':[{'OutputKey':key,'OutputValue':value} for key,value in {
            'VpcId':'vpc-new','AvailabilityZone':'us-east-1a','SubnetId':'subnet-new','GroupId':'sg-new',
            'KeyName':'crux-account-operator','InstanceProfile':'profile-new'}.items()]}]}
    values=discover(session,'explicit-profile','123456789012')
    assert values['box']['subnet_id']=='subnet-new'
    cf.describe_stacks.assert_called_once_with(StackName='crux-account')
