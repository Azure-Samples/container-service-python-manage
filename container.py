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
import sys
from collections import namedtuple

import docker
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
from msrestazure.azure_exceptions import CloudError


ClientData = namedtuple('ClientArgs', ['credentials', 'subscription_id'])


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
                 default_name='containersample'):
        self.default_name = default_name
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
                    location=self.resource_helper.group.location,
                    sku=StorageAccountSku(StorageSkuName.standard_lrs),
                    kind=StorageKind.storage,
                )
            )
            storage_creation.wait()
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


class DockerHelper(object):
    def __init__(self, client_data, resource_helper, storage,
                 registry=None,
                 default_name='containersample'):
        self.resources = resource_helper
        self.storage = storage
        self.default_name = default_name
        self.docker_client = docker.from_env()
        self.dns_prefix = Haikunator().haikunate()
        self._registry = registry
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

    def login_to_registry(self):
        print('Logging into Docker registry...')
        registry_credentials = self.registry_client.registries.list_credentials(
            self.resources.group.name,
            self.registry.name,
        )
        first_password = next(iter(registry_credentials.passwords)).value
        self.docker_client.login(
            username=registry_credentials.username,
            password=first_password,
            registry=self.registry.login_server,
        )
        print('Login successful.')

    def create_acs_container(self):
        # Create container on ACS using the ACR link (like the CLI line Karthik does)
        container_ops = self.container_client.container_services

        container_service = ContainerService(
            self.storage.account.location,
            ContainerServiceMasterProfile(
                dns_prefix=self.dns_prefix,
                count=1
            ),
            [
                ContainerServiceAgentPoolProfile(
                    name=self.default_name,
                    vm_size='Standard_D1_v2',
                    dns_prefix=self.dns_prefix,
                )
            ],
            # linux_profile
            ContainerServiceLinuxProfile(
                self.default_name,
                self._get_ssh_config()
            )
        )

        container_service_creation = container_ops.create_or_update(
            resource_group_name=self.resources.group.name,
            container_service_name=self.default_name,
            parameters=container_service,
        )
        container_service_creation.wait()
        print(container_service_creation.result())

    def _get_ssh_config(self, key_path=None):
        key_path = key_path or os.path.join(os.environ['HOME'], '.ssh', 'id_rsa.pub')
        with io.open(key_path) as key_file:
            return ContainerServiceSshConfiguration(
                [
                    ContainerServiceSshPublicKey(key_file.read())
                ]
            )


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

    def deploy(self):
        self.docker.create_acs_container()


def main():
    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID'],
    )

    deployer = Deployer(
        ClientData(
            credentials,
            os.environ['AZURE_SUBSCRIPTION_ID'],
        )
    )
    deployer.deploy()


if __name__ == '__main__':
    sys.exit(main())
