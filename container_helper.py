import io
import json
import os
import tarfile
import tempfile
from collections import namedtuple
from contextlib import contextmanager

import subprocess
from subprocess import PIPE

import docker
import requests
from haikunator import Haikunator
from sshtunnel import SSHTunnelForwarder

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
    """Switch the working dir to a given dir temporarily."""
    starting_path = os.getcwd()
    os.chdir(path)
    yield
    os.chdir(starting_path)


class ContainerRegistryHelper(object):
    def __init__(self, client_data, resource_helper, storage,
                 registry=None,
                 default_name='containersample'):
        self.resources = resource_helper
        self.storage = storage
        self.default_name = default_name
        self._registry = registry
        self._credentials = None
        self.credentials_file_name = 'docker.tar.gz'
        self.registry_client = ContainerRegistryManagementClient(*client_data)

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
                registry = registry_creation.result()
            self._registry = registry
            print('Got container registry:', registry.name)
        return self._registry

    @property
    def credentials(self):
        if self._credentials is None:
            all_credentials = self.registry_client.registries.list_credentials(
                self.resources.group.name,
                self.registry.name,
            )
            first_password = next(iter(all_credentials.passwords)).value
            self._credentials = LoginCredentials(
                all_credentials.username,
                first_password,
            )
        return self._credentials

    def get_docker_repo_tag(self, image_name_in_repo):
        return '/'.join([
            self.registry.login_server,
            self.credentials.user,
            image_name_in_repo,
        ])

    @contextmanager
    def docker_session(self):
        """Log in and out of a Docker registry inside a with block.

        This uses the Docker CLI rather than the Python module,
        as the module claims not to modify the Docker config.json,
        which we need for credential distribution to the cluster.
        """
        print('Logging into Docker registry...')
        subprocess.check_call([
            'docker', 'login',
            '-u', self.credentials.user,
            '-p', self.credentials.password,
            self.registry.login_server,
        ])
        yield docker.APIClient()
        print('Logging out of Docker registry.')
        subprocess.check_call(['docker', 'logout', self.registry.login_server])

    def _push_to_registry(self, docker_client, image_name, image_name_in_repo):
        print('Pushing image {}...'.format(image_name))
        repository_tag = self.get_docker_repo_tag(image_name_in_repo)
        docker_client.tag(
            image_name,
            repository=repository_tag,
        )
        for stream_line in docker_client.push(repository=repository_tag,
                                              stream=True):
            for line in stream_line.decode('utf-8').strip().split('\n'):
                print(json.dumps(json.loads(line)))
        print('Push finished.')

    def _upload_docker_creds(self):
        """Upload credentials for a Docker registry to an Azure share.

        Official docs on this process:
        https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-acr

        This relies on Docker storing credentials in ~/.docker/config.json.
        That doesn't happen if there is a "credsStore" entry there.
        You need to remove it!
        """
        print('Uploading Docker credentials...')
        with tempfile.TemporaryDirectory() as temp_dir:
            creds_path = os.path.join(temp_dir, self.credentials_file_name)
            with tarfile.open(creds_path, mode='w:gz') as creds_file:
                with working_dir(os.environ['HOME']):
                    creds_file.add('.docker')
            share_path = self.storage.upload_file(creds_path)
        print('Docker credentials uploaded to share at', share_path)

    def setup_image(self, image_name, image_name_in_repo):
        """Push an image to a registry and put the registry credentials on a share."""
        with self.docker_session() as docker_client:
            self._push_to_registry(docker_client, image_name, image_name_in_repo)
            self._upload_docker_creds()


class ContainerHelper(object):
    def __init__(self, client_data, resource_helper,
                 container_service=None,
                 default_name='containersample'):
        self.resources = resource_helper
        self.default_name = default_name
        self.dns_prefix = Haikunator().haikunate()
        self._container_service = container_service
        self.container_client = ContainerServiceClient(*client_data)

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
                    location=self.resources.group.location,
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
        return self.container_service.master_profile.fqdn

    def master_ssh_login(self):
        return '{}@{}'.format(
            self.default_name,
            self.master_ssh_address()
        )

    def ssh_tunnel_args(self, host='localhost', port=80):
        return dict(
            ssh_address_or_host=(self.master_ssh_address(), 2200),
            ssh_username=self.default_name,
            remote_bind_address=(host, port),
            local_bind_address=(host, port),
            ssh_pkey=self.get_key_path(),
        )

    @contextmanager
    def cluster_ssh(self):
        try:
            cmd = ['ssh', '-i', self.get_key_path(), self.master_ssh_login()]
            print('Connecting to cluster:', ' '.join(cmd))
            proc = subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE)
        except subprocess.CalledProcessError:
            print('Your SSH connection to the cluster was unsuccessful. '
                  'Try `ssh {}` to confirm that you can do so '
                  'without any prompts.'.format(self.master_ssh_login()))
            raise
        yield proc
        proc.terminate()

    def deploy_container_from_registry(self, docker_tag, registry_helper):
        with SSHTunnelForwarder(**self.ssh_tunnel_args()) as tunnel:
            print('Attempting to deploy Docker image {}'.format(docker_tag))
            response = requests.post(
                'http://{}:{}/marathon/v2/apps'.format(*tunnel.local_bind_address),
                json={
                    "id": docker_tag.split('/')[-1],
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
                            registry_helper.storage.default_share,
                            registry_helper.credentials_file_name
                        )
                    ]
                }
            )
        content = json.loads(response.text)
        if 'deployments' in content:
            print('Deployments: ', content['deployments'])
        else:
            print(content)

