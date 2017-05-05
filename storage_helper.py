"""Streamline interacting with a single Azure storage account."""

import os

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


class StorageHelper(object):
    """Handle details related to a single storage account and share.

    Instantiate this object with information sufficient to
    uniquely identify a storage account and share within it.
    Then .account can be used to retrieve the Azure SDK for Python
    object corresponding to the account, and .key can be used to
    get an access key for it.

    For both those properties, if the value mentioned doesn't exist,
    it will be created upon first property access.
    """
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
        """Return the managed StorageAccounts object.
        
        If no such account exists, create it first.
        """
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
        """Get the first available storage key.

        This will crash if there are no available storage keys,
        which is unlikely since two are created along with a storage account.
        """
        if self._key is None:
            storage_keys = self.client.storage_accounts.list_keys(
                self.resource_helper.group.name,
                self.account.name
            )
            self._key = next(iter(storage_keys.keys)).value
        return self._key

    def upload_file(self, path):
        """Upload a file into the default share on the storage account.

        If the share doesn't exist, create it first.
        """
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

