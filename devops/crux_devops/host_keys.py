"""Read an owned Box's public SSH host key through authenticated Systems Manager."""
import os
import re
import time


def trust_box(aws, lifecycle, path):
    """Write the owned Box's authenticated host key to a private known-hosts file.

    :param aws: Explicit-profile AWS session registry.
    :param lifecycle: Run or controller lifecycle containing a ready Box.
    :param path: Dedicated known-hosts file for this deployment.
    :raises RuntimeError: SSM registration or the read-only command times out.
    """
    state, context = lifecycle.observe(), lifecycle.context
    box = next(r for r in lifecycle.run.resources if r.id == lifecycle.run.box)
    output = state['resources'][box.id]['outputs']
    driver = lifecycle.drivers[box.type]
    if not output or driver.quiescent(box, output, context):
        return
    driver.verify(box, output, context)
    host = output['public_ip']
    for resource in lifecycle.run.resources:
        if resource.type == 'aws.address_attachment' and resource.config['box'] == box.id:
            host = state['resources'][resource.id]['outputs']['public_ip']
    instance = output['instance_id']
    ssm = aws.session(box).client('ssm')
    for attempt in range(120):
        nodes = ssm.describe_instance_information(Filters=[{'Key':'InstanceIds','Values':[instance]}])['InstanceInformationList']
        if any(node['PingStatus'] == 'Online' for node in nodes):
            break
        if attempt == 119:
            raise RuntimeError('Box did not register with SSM; inspect its instance role and agent')
        time.sleep(5)
    command = ssm.send_command(InstanceIds=[instance],DocumentName='AWS-RunShellScript',
                              Parameters={'commands':['cat /etc/ssh/ssh_host_ed25519_key.pub']},
                              TimeoutSeconds=60)['Command']['CommandId']
    for attempt in range(60):
        try:
            result = ssm.get_command_invocation(CommandId=command,InstanceId=instance)
        except ssm.exceptions.InvocationDoesNotExist:
            result = {'Status':'Pending'}
        if result['Status'] == 'Success':
            break
        if result['Status'] not in ('Pending','InProgress','Delayed') or attempt == 59:
            raise RuntimeError('Could not retrieve the Box SSH host key through SSM')
        time.sleep(2)
    key = result['StandardOutputContent'].strip()
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',key):
        raise ValueError('SSM returned an invalid SSH host key')
    lifecycle.drivers[box.type].verify(box,output,context)
    descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,'w') as stream:
        stream.write(host+' '+' '.join(key.split()[:2])+'\n')
