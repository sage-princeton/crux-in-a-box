"""Native administration of explicitly scoped standalone Elastic IPs."""
import argparse
import json

from botocore.exceptions import BotoCoreError, ClientError

from .aws import AWS, one
from .spec import Resource


def manage(ec2, command, *, name=None, allocation=None, show_all=False, confirmed=False):
    if command == 'list':
        return ec2.describe_addresses(**({} if show_all else {'Filters':[{'Name':'tag:CruxRole','Values':['shared-eip']}]}))['Addresses']
    if not allocation and not name:
        raise ValueError('Provide an allocation ID or a unique name')
    current = one(ec2.describe_addresses(**({'AllocationIds':[allocation]} if allocation else
        {'Filters':[{'Name':'tag:Name','Values':[name]}]}))['Addresses'])
    if current and not any(t['Key']=='CruxRole' and t['Value']=='shared-eip' for t in current.get('Tags', [])):
        raise ValueError('Address is not a standalone shared allocation')
    if command == 'create':
        if not name or allocation:
            raise ValueError('Create requires --name and no allocation ID')
        return current or ec2.allocate_address(Domain='vpc', TagSpecifications=[{'ResourceType':'elastic-ip',
            'Tags':[{'Key':'Name','Value':name},{'Key':'CruxRole','Value':'shared-eip'}]}])
    if not current or current.get('AssociationId') or not confirmed:
        raise ValueError('Delete requires an existing, detached shared allocation and --yes')
    ec2.release_address(AllocationId=current['AllocationId'])
    return {'released':current['AllocationId']}


def main():
    parser = argparse.ArgumentParser(description='Manage standalone shared Elastic IPs with explicit AWS scope')
    parser.add_argument('command',choices=['list','create','delete'])
    for key in ('profile','region','account'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--name')
    parser.add_argument('--allocation-id')
    parser.add_argument('--all',action='store_true')
    parser.add_argument('--yes',action='store_true')
    args = parser.parse_args()
    try:
        resource = Resource.parse({'id':'addresses','type':'aws.elastic_ip',
            'config':{key:getattr(args,key) for key in ('profile','region','account')}})
        ec2 = AWS().session(resource).client('ec2')
        result = manage(ec2,args.command,name=args.name,allocation=args.allocation_id,show_all=args.all,confirmed=args.yes)
        print(json.dumps(result,indent=2,default=str))
    except (ValueError,BotoCoreError,ClientError) as error:
        parser.exit(2,f'crux-addresses: {error}\n')


if __name__ == '__main__':
    main()
