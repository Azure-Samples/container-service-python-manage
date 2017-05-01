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


class StorageHelper(object):
    def __init__(self, credentials, subscription_id,
                 account=None,
                 default_name='containersample'):
        self.key = os.environ.get('AZURE_STORAGE_KEY')
        self.default_name = default_name
        self.account = account
        self.client = StorageManagementClient(
            credentials,
            subscription_id,
        )

    def ensure_resources(self, resource_group):
        self.account = self.account or self._create_storage_account(resource_group)
        self.key = self.key or self._get_key(resource_group)

    def _create_storage_account(self, resource_group):
        print('Creating storage account...')
        # OK to create storage account even if it already exists
        storage_creation = self.client.storage_accounts.create(
            resource_group.name,
            self.default_name,
            StorageAccountCreateParameters(
                location=resource_group.location,
                sku=StorageAccountSku(StorageSkuName.standard_lrs),
                kind=StorageKind.storage,
            )
        )
        storage_creation.wait()
        storage = storage_creation.result()
        print('Got storage account:', storage.name)
        return storage

    def _get_key(self, resource_group):
        """Get the first storage key."""
        storage_keys = self.client.storage_accounts.list_keys(
            resource_group.name,
            self.account.name
        )
        return next(iter(storage_keys.keys)).value


class Deployer(object):
    def __init__(self, credentials, subscription_id,
                 default_name='containersample',
                 resource_group=None,
                 storage_account=None,
                 container_registry=None):
        self.default_name = default_name
        self.resource_group = resource_group
        self.storage = StorageHelper(credentials, subscription_id, account=storage_account)
        self.container_registry = container_registry

        self.dns_prefix = Haikunator().haikunate()
        self.location = 'South Central US'

        self.resource_client = ResourceManagementClient(
            credentials,
            subscription_id
        )
        self.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.registry_client = ContainerRegistryManagementClient(
            credentials,
            subscription_id
        )
        self.container_client = ContainerServiceClient(
            credentials,
            subscription_id
        )

    def ensure_resources(self):
        self.resource_group = self.resource_group or self._create_resource_group()
        self.storage.ensure_resources(self.resource_group)
        self.container_registry = self.container_registry or self._create_container_registry()

    def _create_resource_group(self):
        print('Ensuring resource group...')
        resource_group_name = self.default_name + '-group'
        resource_group = self.resource_client.resource_groups.create_or_update(
            resource_group_name,
            {'location': self.location}
        )
        print('Got resource group:', resource_group.name)
        return resource_group

    def _create_container_registry(self):
        print('Creating container registry...')
        registry_ops = self.registry_client.registries
        try:
            registry = registry_ops.get(
                self.resource_group.name,
                self.default_name,
            )
        except CloudError:
            # try to create registry
            registry_creation = registry_ops.create(
                self.resource_group.name,
                self.default_name,
                RegistryCreateParameters(
                    location=self.location,
                    sku=ContainerRegistrySku(ContainerRegistrySkuName.basic),
                    storage_account=StorageAccountParameters(
                        self.storage.account.name,
                        self.storage.key
                    )
                )
            )
            registry_creation.wait()
            registry = registry_creation.result()
        print('Got container registry:', registry.name)
        return registry

    def _get_ssh_config(self, key_path=None):
        key_path = key_path or os.path.join(os.environ['HOME'], '.ssh', 'id_rsa.pub')
        with io.open(key_path) as key_file:
            return ContainerServiceSshConfiguration(
                [
                    ContainerServiceSshPublicKey(key_file.read())
                ]
            )

    def create_acs_container(self):
        # Create container on ACS using the ACR link (like the CLI line Karthik does)
        container_ops = self.container_client.container_services

        container_service = ContainerService(
            self.location,
            ContainerServiceMasterProfile(
                self.dns_prefix,
                count=1
            ),
            ContainerServiceAgentPoolProfile(
                'container-sample',
                'Standard_D1_v2',
                self.dns_prefix
            ),
            # linux_profile
            ContainerServiceLinuxProfile(
                'container-sample',
                self._get_ssh_config()
            )
        )


def download_acr_keys():
    pass


def push_to_acr():
    # "docker login" ACR (like Karthik does)
    # docker build && docker push (push a local image to ACR)
    pass


def request_against_container():
    # "requests.get" the newly created RestAPI.
    pass


def main():
    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID'],
    )

    deployer = Deployer(
        credentials,
        os.environ['AZURE_SUBSCRIPTION_ID'],
    )
    deployer.ensure_resources()


if __name__ == '__main__':
    sys.exit(main())
