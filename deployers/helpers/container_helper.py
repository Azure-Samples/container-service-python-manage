from contextlib import contextmanager
import io
import json
import os
import subprocess
from subprocess import PIPE
import sys
import traceback

import requests
from haikunator import Haikunator
from sshtunnel import SSHTunnelForwarder, HandlerSSHTunnelForwarderError

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


class ContainerServiceHelper(object):
    def __init__(self, client_data, resource_helper, name, docker_tag):
        self.resources = resource_helper
        self.name = name
        self.docker_tag = docker_tag
        self._container_service = None
        self.container_client = ContainerServiceClient(*client_data)

    @property
    def container_service(self):
        container_ops = self.container_client.container_services

        if self._container_service is None:
            try:
                self._container_service = container_ops.get(
                    self.resources.group.name,
                    self.name,
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
                            name=self.name,
                            vm_size='Standard_D1_v2',
                            dns_prefix='agent' + self.dns_prefix,
                        )
                    ],
                    linux_profile=ContainerServiceLinuxProfile(
                        self.name,
                        self._get_ssh_config(),
                    ),
                    orchestrator_profile=ContainerServiceOrchestratorProfile(
                        orchestrator_type='DCOS',
                    )
                )

                container_service_creation = container_ops.create_or_update(
                    resource_group_name=self.resources.group.name,
                    container_service_name=self.name,
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
            self.name,
            self.master_ssh_address()
        )

    def ssh_tunnel_args(self, remote_host='127.0.0.1', local_host='127.0.0.1',
                        remote_port=80, local_port=8001):
        return dict(
            ssh_address_or_host=(self.master_ssh_address(), 2200),
            ssh_username=self.name,
            remote_bind_address=(remote_host, remote_port),
            local_bind_address=(local_host, local_port),
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

    def deployment_id(self):
        return self.docker_tag.split('/')[-1]

    def marathon_deploy_params(self, private_registry_helper=None):
        params = {
            "id": self.deployment_id(),
            "container": {
                "type": "DOCKER",
                "docker": {
                    "image": self.docker_tag,
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
        }
        if private_registry_helper:
            params["uris"] = [
                "file:///mnt/{}/{}".format(
                    private_registry_helper.storage.default_share,
                    private_registry_helper.credentials_file_name
                )
            ]
        return params

    def deploy_container_from_registry(self, docker_tag, private_registry_helper=None):
        tunnel_remote_port = 80
        tunnel_local_port = 8001
        tunnel_host = '127.0.0.1'
        try:
            with SSHTunnelForwarder(**self.ssh_tunnel_args(
                remote_host=tunnel_host,
                local_host=tunnel_host,
                remote_port=tunnel_remote_port,
                local_port=tunnel_local_port,
            )) as tunnel:
                print('Attempting to deploy Docker image {}'.format(self.docker_tag))
                response = requests.post(
                    'http://{}:{}/marathon/v2/apps'.format(*tunnel.local_bind_address),
                    json=self.marathon_deploy_params(docker_tag, private_registry_helper)
                )
        except HandlerSSHTunnelForwarderError:
            traceback.print_exc()
            print('Opening SSH tunnel failed.')
            print('Please try the following command in a terminal:')
            print('ssh -N -L {local_host}:{local_port}:{remote_host}:{remote_port} {addr}'.format(
                remote_host=tunnel_host,
                remote_port=tunnel_remote_port,
                local_host=tunnel_host,
                local_port=tunnel_local_port,
                addr=self.master_ssh_address(),
            ))
            sys.exit(1)
        content = json.loads(response.text)
        print('Deployment request successful.')
        if 'deployments' in content:
            print('Deployments: ', content['deployments'])
        else:
            print(content)

