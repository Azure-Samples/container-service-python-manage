import io
import json
import os
import tarfile
import tempfile
from collections import namedtuple
from contextlib import contextmanager

import subprocess
from subprocess import PIPE

import requests
from haikunator import Haikunator
from sshtunnel import SSHTunnelForwarder

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

