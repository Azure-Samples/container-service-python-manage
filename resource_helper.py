from azure.mgmt.resource.resources import ResourceManagementClient


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

    def list_resources(self):
        return self.resource_client.resource_groups.list_resources(self.default_name)

    def get_by_id(self, resource_id):
        return self.resource_client.resources.get_by_id(resource_id, '2017-04-01')

