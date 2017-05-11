"""Deploy a Docker container from an Azure registry to a cluster.
"""

import argparse
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
from registry_helper import ContainerRegistryHelper


DEFAULT_DOCKER_IMAGE = 'mesosphere/simple-docker'


ClientArgs = namedtuple('ClientArgs', ['credentials', 'subscription_id'])


def set_up_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--image', default=DEFAULT_DOCKER_IMAGE,
        help='Docker image to deploy.'
    )
    return parser


class Deployer(object):
    def __init__(self, client_data,
                 default_name='containersample',
                 location='South Central US',
                 docker_image=DEFAULT_DOCKER_IMAGE,
                 resource_group=None,
                 storage_account=None,
                 container_registry=None):
        self.default_name = default_name
        self.docker_image = docker_image
        self.resources = ResourceHelper(client_data, location, resource_group=resource_group)
        self.resources.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.resources.resource_client.providers.register('Microsoft.ContainerService')
        self.storage = StorageHelper(client_data, self.resources, account=storage_account)
        self.container_registry = ContainerRegistryHelper(
            client_data,
            self.resources,
            self.storage,
            container_registry
        )
        self.container_service = ContainerHelper(client_data, self.resources)

    def _format_proc_output(self, header, output):
        if output:
            print(
                header,
                '\n'.join([
                    '    {}'.format(line)
                    for line in output.decode('utf-8').split('\n')
                ]),
                sep='\n',
                end='\n\n'
            )

    def scp_to_container_master(self, local_path, remote_path):
        address = self.container_service.master_ssh_login()
        try:
            subprocess.check_output([
                'scp',
                local_path,
                '{}:./{}'.format(address, remote_path)
            ])
        except subprocess.CalledProcessError:
            print('It looks like an scp command failed.')
            print('You might need to verify the host key manually.')
            print('Please run the following command to do so:')
            print('ssh {}'.format(address))
            raise

    def mount_shares(self):
        print('Mounting file share on all machines in cluster...')
        key_file = os.path.basename(self.container_service.get_key_path())
        # https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare
        with io.open('cifsMountTemplate.sh') as cifsMount_template, \
             io.open('cifsMount.sh', 'w', newline='\n') as cifsMount:
            cifsMount.write(
                cifsMount_template.read().format(
                    storageacct=self.storage.account.name,
                    sharename=self.storage.default_share,
                    username=self.container_registry.default_name,
                    password=self.storage.key,
                )
            )
        self.scp_to_container_master('cifsMount.sh', '')
        self.scp_to_container_master('mountShares.sh', '')
        self.scp_to_container_master(self.container_service.get_key_path(), key_file)
        with self.container_service.cluster_ssh() as proc:
            proc.stdin.write('chmod 600 {}\n'.format(key_file).encode('ascii'))
            proc.stdin.write(b'eval ssh-agent -s\n')
            proc.stdin.write('ssh-add {}\n'.format(key_file).encode('ascii'))
            mountShares_cmd = 'sh mountShares.sh ~/{}\n'.format(key_file)
            print('Running mountShares on remote master. Cmd:', mountShares_cmd, sep='\n')
            proc.stdin.write(mountShares_cmd.encode('ascii'))
            out, err = proc.communicate(input=b'exit\n')
        self._format_proc_output('Stdout:', out)
        self._format_proc_output('Stderr:', err)

    def deploy(self):
        registry_image_name = self.docker_image.split('/')[-1]
        self.container_registry.setup_image(self.docker_image, registry_image_name)
        self.mount_shares()
        self.container_service.deploy_container_from_registry(
            self.container_registry.get_docker_repo_tag(registry_image_name),
            self.container_registry
        )

    def public_ip(self):
        for item in self.resources.list_resources():
            if 'agent-ip' in item.name.lower():
                return self.resources.get_by_id(item.id).properties['ipAddress']


def main():
    parser = set_up_parser()
    args = parser.parse_args()

    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID'],
    )

    deployer = Deployer(
        ClientArgs(
            credentials,
            os.environ['AZURE_SUBSCRIPTION_ID'],
        ),
        docker_image=args.image,
    )
    deployer.deploy()
    print(requests.get('http://{}'.format(deployer.public_ip())).text)

if __name__ == '__main__':
    sys.exit(main())
