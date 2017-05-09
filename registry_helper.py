import json
import os
import subprocess
import tarfile
import tempfile
from collections import namedtuple
from contextlib import contextmanager

import docker

from azure.mgmt.containerregistry import (
    ContainerRegistryManagementClient,
)
from azure.mgmt.containerregistry.models import (
    RegistryCreateParameters,
    StorageAccountParameters,
    Sku as ContainerRegistrySku,
    SkuTier as ContainerRegistrySkuName
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

