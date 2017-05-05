"""
- Create ACR account (ACR SDK)
- Download keys (ACR SDK)
- "docker login" ACR (like Karthik does)
- docker build && docker push (push a local image to ACR)
- Create container on ACS using the ACR link (like the CLI line Karthik does)
- "requests.get" the newly created RestAPI.
"""

import io
import os
import subprocess
import sys
from collections import namedtuple

import requests

from azure.common.credentials import ServicePrincipalCredentials

from resource_helper import ResourceHelper
from storage_helper import StorageHelper
from container_helper import ContainerHelper


ClientArgs = namedtuple('ClientArgs', ['credentials', 'subscription_id'])


class Deployer(object):
    def __init__(self, client_data,
                 default_name='containersample',
                 location='South Central US',
                 resource_group=None,
                 storage_account=None,
                 container_registry=None):
        self.default_name = default_name
        self.resources = ResourceHelper(client_data, location, resource_group=resource_group)
        self.resources.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.resources.resource_client.providers.register('Microsoft.ContainerService')
        self.storage = StorageHelper(client_data, self.resources, account=storage_account)
        self.container = ContainerHelper(client_data, self.resources, self.storage,
                                         registry=container_registry)

    def mount_shares(self):
        key_file = os.path.basename(self.container.get_key_path())
        # https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare
        with io.open('cifsMountTemplate.sh') as cifsMount_template, \
             io.open('cifsMount.sh', 'w', newline='\n') as cifsMount:
            cifsMount.write(
                cifsMount_template.read().format(
                    storageacct=self.storage.account.name,
                    sharename=self.storage.default_share,
                    username=self.container.default_name,
                    password=self.storage.key,
                )
            )
        # Might already have a python module (scp) for this
        subprocess.check_output([
            'scp',
            'cifsMount.sh',
            '{}:./'.format(self.container.master_ssh_address()),
        ])
        subprocess.check_output([
            'scp',
            'mountShares.sh',
            '{}:./'.format(self.container.master_ssh_address()),
        ])
        subprocess.check_output([
            'scp',
            self.container.get_key_path(),
            '{}:./{}'.format(self.container.master_ssh_address(), key_file),
        ])
        with self.container.cluster_ssh() as proc:
            proc.stdin.write('chmod 600 {}\n'.format(key_file).encode('ascii'))
            proc.stdin.write(b'eval ssh-agent -s\n')
            proc.stdin.write('ssh-add {}\n'.format(key_file).encode('ascii'))
            mountShares_cmd = 'sh mountShares.sh ~/{}\n'.format(key_file)
            print('Running mountShares on remote master. Cmd:', mountShares_cmd, sep='\n')
            proc.stdin.write(mountShares_cmd.encode('ascii'))
            out, err = proc.communicate(input=b'exit\n')
        if out:
            print('Stdout: ', out.decode('utf-8'), sep='\n', end='\n\n')
        if err:
            print('Stderr: ', err.decode('utf-8'), sep='\n', end='\n\n')

    def deploy(self):
        self.container.push_to_registry('mesosphere/simple-container', 'simple-container')
        self.mount_shares()
        self.container.deploy_container('simple-container')

    def public_ip(self):
        for item in self.resources.list_resources():
            if 'agent-ip' in item.name.lower():
                return self.resources.get_by_id(item.id).properties['ipAddress']


def main():
    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID'],
    )

    deployer = Deployer(
        ClientArgs(
            credentials,
            os.environ['AZURE_SUBSCRIPTION_ID'],
        )
    )
    deployer.deploy()
    print(requests.get('http://{}'.format(deployer.public_ip())).text)

if __name__ == '__main__':
    sys.exit(main())
