import io
import json
import os
import platform
import tarfile
import tempfile
import signal
from collections import namedtuple
from contextlib import contextmanager

import subprocess
from subprocess import PIPE

import docker
import requests
from haikunator import Haikunator

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
)

from msrestazure.azure_exceptions import CloudError


LoginCredentials = namedtuple('LoginCredentials', ['user', 'password'])


@contextmanager
def working_dir(path):
    starting_path = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(starting_path)


class ContainerHelper(object):
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
        # Might have a Python module (sshtunnel) for this
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
            # Create a new console window on Windows, because the background ssh
            # process seems to be unkillable in Python through normal means.
            # Opening a new console at least makes it explicit that it persists
            # after this script finishes running.
            proc = subprocess.Popen(cmd, stdin=PIPE, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except subprocess.CalledProcessError:
            print('Your SSH tunnel to the cluster was unsuccessful. '
                  'Try `ssh {}` to confirm that you can do so '
                  'without any prompts.'.format(address))
            raise
        yield 'http://{}:{}'.format(host, port)
        if platform.system() == 'Windows':
            os.kill(proc.pid, signal.CTRL_C_EVENT)
        else:
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
        if 'deployments' in content:
            print('Deployments: ', content['deployments'])
        else:
            print(content)

