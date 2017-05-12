from .helpers.resource_helper import ResourceHelper
from .helpers.container_helper import ContainerHelper


class ContainerDeployer(object):
    def __init__(self, client_data, docker_image,
                 default_name='containersample',
                 location='South Central US',
                 resource_group=None):
        self.default_name = default_name
        self.docker_image = docker_image
        self.resources = ResourceHelper(client_data, location, resource_group=resource_group)
        self.resources.resource_client.providers.register('Microsoft.ContainerRegistry')
        self.resources.resource_client.providers.register('Microsoft.ContainerService')
        self.container_service = ContainerHelper(client_data, self.resources)

    def deploy(self):
        self.container_service.deploy_container_from_registry(self.docker_image)

    def public_ip(self):
        for item in self.resources.list_resources():
            if 'agent-ip' in item.name.lower():
                return self.resources.get_by_id(item.id).properties['ipAddress']

