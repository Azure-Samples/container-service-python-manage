"""
- Create ACR account (ACR SDK)
- Download keys (ACR SDK)
- "docker login" ACR (like Karthik does)
- docker build && docker push (push a local image to ACR)
- Create container on ACS using the ACR link (like the CLI line Karthik does)
- "requests.get" the newly created RestAPI.
"""

import io
import json
import os
import tarfile
import tempfile
import subprocess
import sys
from collections import namedtuple
from contextlib import contextmanager
from subprocess import PIPE

import docker
import requests
from haikunator import Haikunator

from azure.common.credentials import ServicePrincipalCredentials
from azure.mgmt.containerregistry import (
    ContainerRegistryManagementClient,
)
from azure.mgmt.containerregistry.models import (
    RegistryCreateParameters,
    StorageAccountParameters,
    Sku as ContainerRegistrySku,
    SkuTier as ContainerRegistrySkuName
)

from azure.mgmt.compute.containerservice import ContainerServiceClient
from azure.mgmt.compute.containerservice.models import (
    ContainerService,
    ContainerServiceAgentPoolProfile,
    ContainerServiceLinuxProfile,
    ContainerServiceMasterProfile,
    ContainerServiceOrchestratorProfile,
    ContainerServiceSshConfiguration,
    ContainerServiceSshPublicKey,
    ContainerServiceVMSizeTypes,
)

from azure.mgmt.resource.features import FeatureClient
from azure.mgmt.resource.resources import ResourceManagementClient

from azure.mgmt.storage import (
    StorageManagementClient,
)
from azure.mgmt.storage.models import (
    StorageAccountCreateParameters,
    Sku as StorageAccountSku,
    SkuName as StorageSkuName,
    Kind as StorageKind
)

from azure.storage.file import FileService

from msrestazure.azure_exceptions import CloudError


ClientArgs = namedtuple('ClientArgs', ['credentials', 'subscription_id'])
LoginCredentials = namedtuple('LoginCredentials', ['user', 'password'])


@contextmanager
def working_dir(path):
    starting_path = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(starting_path)


class ResourceHelper(object):
    def __init__(self, client_data, location,
                 default_name='containersample-group',
                 resource_group=None):
        self.location = location
        self.default_name = default_name
        self.resource_client = ResourceManagementClient(*client_data)
        self._resource_group = resource_group

    @property
    def group(self):
        if self._resource_group is None:
            print('Ensuring resource group...')
            resource_group = self.resource_client.resource_groups.create_or_update(
                self.default_name,
                {'location': self.location}
            )
            print('Got resource group:', resource_group.name)
            self._resource_group = resource_group
        return self._resource_group


class StorageHelper(object):
    def __init__(self, client_data, resource_helper,
                 account=None,
                 default_name='containersample',
                 default_share='share'):
        self.default_name = default_name
        self.default_share = default_share
        self._account = account
        self._key = os.environ.get('AZURE_STORAGE_KEY')
        self.resource_helper = resource_helper
        self.client = StorageManagementClient(*client_data)

    @property
    def account(self):
        if self._account is None:
            print('Creating storage account...')
            # OK to create storage account even if it already exists
            storage_creation = self.client.storage_accounts.create(
                self.resource_helper.group.name,
                self.default_name,
                StorageAccountCreateParameters(
                    sku=StorageAccountSku(StorageSkuName.standard_lrs),
                    kind=StorageKind.storage,
                    location=self.resource_helper.group.location,
                )
            )
            storage = storage_creation.result()
            print('Got storage account:', storage.name)
            self._account = storage
        return self._account

    @property
    def key(self):
        """Get the first storage key."""
        if self._key is None:
            storage_keys = self.client.storage_accounts.list_keys(
                self.resource_helper.group.name,
                self.account.name
            )
            self._key = next(iter(storage_keys.keys)).value
        return self._key

    def upload_file(self, path):
        file_service = FileService(
            account_name=self.account.name,
            account_key=self.key,
        )
        file_service.create_share(self.default_share)
        file_service.create_file_from_path(
            self.default_share,
            None,
            os.path.basename(path),
            path,
        )
        return '/'.join([self.default_share, os.path.basename(path)])


class DockerHelper(object):
    def __init__(self, client_data, resource_helper, storage,
                 registry=None,
                 container_service=None,
                 default_name='containersample'):
        self.resources = resource_helper
        self.storage = storage
        self.default_name = default_name
        self.docker_client = docker.APIClient()
        self.dns_prefix = Haikunator().haikunate()
        self._registry = registry
        self._container_service = container_service
        self._registry_credentials = None
        self.credentials_file_name = 'docker.tar.gz'
        self.registry_client = ContainerRegistryManagementClient(*client_data)
        self.container_client = ContainerServiceClient(*client_data)

    @property
    def registry(self):
        if self._registry is None:
            print('Creating container registry...')
            registry_ops = self.registry_client.registries
            try:
                registry = registry_ops.get(
                    self.resources.group.name,
                    self.default_name,
                )
            except CloudError:
                # try to create registry
                registry_creation = registry_ops.create(
                    self.resources.group.name,
                    self.default_name,
                    RegistryCreateParameters(
                        location=self.storage.account.location,
                        sku=ContainerRegistrySku(ContainerRegistrySkuName.basic),
                        admin_user_enabled=True,
                        storage_account=StorageAccountParameters(
                            self.storage.account.name,
                            self.storage.key,
                        ),
                    )
                )
                registry_creation.wait()
                registry = registry_creation.result()
            self._registry = registry
            print('Got container registry:', registry.name)
        return self._registry

    @property
    def registry_credentials(self):
        if self._registry_credentials is None:
            all_credentials = self.registry_client.registries.list_credentials(
                self.resources.group.name,
                self.registry.name,
            )
            first_password = next(iter(all_credentials.passwords)).value
            self._registry_credentials = LoginCredentials(
                all_credentials.username,
                first_password,
            )
        return self._registry_credentials

    def _get_docker_repo_tag(self, image_name_in_repo):
        return '/'.join([
            self.registry.login_server,
            self.registry_credentials.user,
            image_name_in_repo,
        ])

    def push_to_registry(self, image_name, image_name_in_repo):
        # This relies on Docker storing credentials in ~/.docker/config.json.
        # That doesn't happen if there is a "credsStore" entry there.
        # You need to remove it!
        print('Logging into Docker registry...')
        self.docker_client.login(
            username=self.registry_credentials.user,
            password=self.registry_credentials.password,
            registry=self.registry.login_server,
        )
        print('Login successful.')
        print('Pushing image {}...'.format(image_name))
        repository_tag = self._get_docker_repo_tag(image_name_in_repo)
        self.docker_client.tag(
            image_name,
            repository=repository_tag,
        )
        for line in self.docker_client.push(repository=repository_tag,
                                            stream=True):
            print(line)
        print('Push finished.')
        # https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-acr
        print('Uploading Docker credentials...')
        with tempfile.TemporaryDirectory() as temp_dir:
            creds_path = os.path.join(temp_dir, self.credentials_file_name)
            with tarfile.open(creds_path, mode='w:gz') as creds_file:
                with working_dir(os.environ['HOME']):
                    creds_file.add('.docker')
            share_path = self.storage.upload_file(creds_path)
        print('Docker credentials uploaded to share at', share_path)

    @property
    def container_service(self):
        container_ops = self.container_client.container_services

        if self._container_service is None:
            try:
                self._container_service = container_ops.get(
                    self.resources.group.name,
                    self.default_name,
                )
            except CloudError:
                container_service = ContainerService(
                    location=self.storage.account.location,
                    master_profile=ContainerServiceMasterProfile(
                        dns_prefix='master' + self.dns_prefix,
                        count=1
                    ),
                    agent_pool_profiles=[
                        ContainerServiceAgentPoolProfile(
                            name=self.default_name,
                            vm_size='Standard_D1_v2',
                            dns_prefix='agent' + self.dns_prefix,
                        )
                    ],
                    linux_profile=ContainerServiceLinuxProfile(
                        self.default_name,
                        self._get_ssh_config(),
                    ),
                    orchestrator_profile=ContainerServiceOrchestratorProfile(
                        orchestrator_type='DCOS',
                    )
                )

                container_service_creation = container_ops.create_or_update(
                    resource_group_name=self.resources.group.name,
                    container_service_name=self.default_name,
                    parameters=container_service,
                )
                self._container_service = container_service_creation.result()
        return self._container_service

    def get_key_path(self):
        return os.path.join(os.environ['HOME'], '.ssh', 'id_rsa')

    def _get_ssh_config(self, key_path=None):
        key_path = key_path or '{}.pub'.format(self.get_key_path())
        with io.open(key_path) as key_file:
            return ContainerServiceSshConfiguration(
                [
                    ContainerServiceSshPublicKey(key_file.read())
                ]
            )

    def master_ssh_address(self):
        url = self.container_service.master_profile.fqdn
        user = self.default_name
        return '{}@{}'.format(user, url)

    @contextmanager
    def cluster_tunnel(self, host='localhost', port=80):
        """
        """
        address = self.master_ssh_address()
        print(address)
        try:
            cmd = [
                'ssh',
                '-fNL', '{1}:{0}:{1}'.format(host, port),
                '-p', '2200',
                '-i', self.get_key_path(),
                address,
            ]
            print('Opening SSH tunnel. Command:', ' '.join(cmd), sep='\n')
            proc = subprocess.Popen(cmd, stdin=PIPE)
        except subprocess.CalledProcessError:
            print('Your SSH connection to the cluster was unsuccessful. '
                  'Try `ssh {}` to confirm that you can do so '
                  'without any prompts.'.format(address))
            raise
        yield 'http://{}:{}'.format(host, port)
        proc.communicate(input=b'exit\n')
        proc.terminate()

    @contextmanager
    def cluster_ssh(self):
        address = self.master_ssh_address()
        try:
            cmd = ['ssh', '-i', self.get_key_path(), address]
            print('Connecting to cluster:', ' '.join(cmd))
            proc = subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE)
        except subprocess.CalledProcessError:
            print('Your SSH connection to the cluster was unsuccessful. '
                  'Try `ssh {}` to confirm that you can do so '
                  'without any prompts.'.format(address))
            raise
        yield proc
        proc.terminate()

    def deploy_container(self, image_name_in_repo):
        with self.cluster_tunnel() as dcos_endpoint:
            docker_tag = self._get_docker_repo_tag(image_name_in_repo)
            print('Attempting to deploy Docker image {}'.format(docker_tag))
            response = requests.post(
                '{}/marathon/v2/apps'.format(dcos_endpoint),
                # json={
                #     "id": image_name_in_repo,
                #     "cpus": 0.1,
                #     "mem": 65,
                #     "acceptedResourceRoles": [
                #         "slave_public",
                #     ],
                #     "instances": 1,
                #     "container": {
                #         "type": "DOCKER",
                #         "docker": {
                #             "image": docker_tag,
                #             "network": "BRIDGE",
                #             "portMappings": [
                #                 {
                #                     "containerPort": 9200,
                #                     "hostPort": 80,
                #                     "protocol": "tcp"
                #                 }
                #             ]
                #         },
                #         "forcePullImage": True
                #     },
                #     # "labels": {
                #     #     "HAPROXY_GROUP": "external",
                #     #     "HAPROXY_0_VHOST": self.container_service.master_profile.fqdn,
                #     #     "HAPROXY_0_MODE": "http"
                #     # },
                #     "uris":  [
                #         "file:///mnt/{}/{}".format(
                #             self.storage.default_share,
                #             self.credentials_file_name
                #         )
                #     ]
                # }
                json={
                    "id": image_name_in_repo,
                    "container": {
                        "type": "DOCKER",
                        "docker": {
                            "image": docker_tag,
                            "network": "BRIDGE",
                            "portMappings": [
                                {
                                    "hostPort": 80,
                                    "containerPort": 80,
                                    "protocol": "tcp"
                                }
                            ]
                        }
                    },
                    "acceptedResourceRoles": ["slave_public"],
                    "instances": 1,
                    "cpus": 0.1,
                    "mem": 64,
                    "uris":  [
                        "file:///mnt/{}/{}".format(
                            self.storage.default_share,
                            self.credentials_file_name
                        )
                    ]
                }
            )
            content = json.loads(response.text)
            print('Deployments: ', content['deployments'])


class Deployer(object):
    def __init__(self, client_data,
                 default_name='containersample',
                 location='South Central US',
                 resource_group=None,
                 storage_account=None,
                 container_registry=None):
        self.default_name = default_name
        self.resources = ResourceHelper(client_data, location)
        self.resources.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.resources.resource_client.providers.register('Microsoft.ContainerService')
        self.storage = StorageHelper(client_data, self.resources, account=storage_account)
        self.docker = DockerHelper(client_data, self.resources, self.storage,
                                   registry=container_registry)

    def mount_shares(self):
        key_file = os.path.basename(self.docker.get_key_path())
        # https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare
        with io.open('cifsMountTemplate.sh') as cifsMount_template, \
             io.open('cifsMount.sh', 'w', newline='\n') as cifsMount:
            cifsMount.write(
                cifsMount_template.read().format(
                    storageacct=self.storage.account.name,
                    sharename=self.storage.default_share,
                    username=self.docker.default_name,
                    password=self.storage.key,
                )
            )
        subprocess.check_output([
            'scp',
            'cifsMount.sh',
            '{}:./'.format(self.docker.master_ssh_address()),
        ])
        subprocess.check_output([
            'scp',
            'mountShares.sh',
            '{}:./'.format(self.docker.master_ssh_address()),
        ])
        subprocess.check_output([
            'scp',
            self.docker.get_key_path(),
            '{}:./{}'.format(self.docker.master_ssh_address(), key_file),
        ])
        with self.docker.cluster_ssh() as proc:
            proc.stdin.write('chmod 600 {}\n'.format(key_file).encode('ascii'))
            proc.stdin.write(b'eval ssh-agent -s\n')
            proc.stdin.write('ssh-add {}\n'.format(key_file).encode('ascii'))
            mountShares_cmd = 'sh mountShares.sh {}\n'.format(
                '~/{}'.format(key_file),
            )
            print('Running mountShares on remote master. Cmd:', mountShares_cmd, sep='\n')
            proc.stdin.write(mountShares_cmd.encode('ascii'))
            out, err = proc.communicate(input=b'exit\n')
        if out:
            print('Stdout: ', out.decode('utf-8'), sep='\n', end='\n\n')
        if err:
            print('Stderr: ', err.decode('utf-8'), sep='\n', end='\n\n')

    def deploy(self):
        self.docker.push_to_registry('mesosphere/simple-docker', 'simple-docker')
        self.mount_shares()
        self.docker.deploy_container('simple-docker')


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


if __name__ == '__main__':
    sys.exit(main())
