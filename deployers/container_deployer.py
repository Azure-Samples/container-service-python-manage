from .helpers.resource_helper import ResourceHelper
from .helpers.container_helper import ContainerServiceHelper


class ContainerDeployer(object):
    def __init__(self, client_data, docker_image,
                 location='South Central US',
                 container_service='containersample',
                 resource_group='containersample-group',
                 **kw):
        self.docker_image = docker_image
        self.resources = ResourceHelper(client_data, location, resource_group)
        self.resources.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.resources.resource_client.providers.register('Microsoft.ContainerService')
        self.container_service = ContainerServiceHelper(client_data,
                                                        self.resources,
                                                        container_service,
                                                        self.docker_image)

    def deploy(self):
        self.container_service.deploy_container_from_registry()

    def public_ip(self):
        for item in self.resources.list_resources():
            name = item.name.lower()
            if 'agent-ip' in name and self.container_service.dns_prefix in name:
                return self.resources.get_by_id(item.id).properties['ipAddress']

