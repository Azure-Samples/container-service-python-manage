from azure.mgmt.resource.resources import ResourceManagementClient


class ResourceHelper(object):
    """A helper class to manage details for a single resource group.

    Instantiate ResourceHelper with a name for the resource group
    through the default_name kwarg. Thereafter, use the .group
    property to get the ResourceGroup object with the given name
    for the client_data credentials provided. If no such group
    exists, ResourceHelper will create one for you.
    """
    def __init__(self, client_data, location,
                 default_name='containersample-group',
                 resource_group=None):
        self.location = location
        self.default_name = default_name
        self.resource_client = ResourceManagementClient(*client_data)
        self._resource_group = resource_group

    @property
    def group(self):
        """Return this helper's ResourceGroup object.

        Look for the resource group for this object's client whose name
        matches default_name, and return it.
        If no such group exists, create it first.
        """
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
        """List resources in this helper's resource group."""
        return self.resource_client.resource_groups.list_resources(self.default_name)

    def get_by_id(self, resource_id):
        """Get a resource by id from this helper's resource group."""
        return self.resource_client.resources.get_by_id(resource_id, '2017-04-01')

