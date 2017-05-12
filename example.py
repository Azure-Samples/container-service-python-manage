"""Deploy a Docker container from an Azure registry to a cluster.
"""

import argparse
import io
import os
import subprocess
import sys
from collections import namedtuple

import requests

from azure.common.credentials import ServicePrincipalCredentials

from deployers.container_deployer import ContainerDeployer
from deployers.acr_container_deployer import ACRContainerDeployer


DEFAULT_DOCKER_IMAGE = 'mesosphere/simple-docker'


ClientArgs = namedtuple('ClientArgs', ['credentials', 'subscription_id'])


def set_up_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--image', default=DEFAULT_DOCKER_IMAGE,
        help='Docker image to deploy.'
    )
    parser.add_argument(
        '--use-acr', action='store_const', dest='deployer',
        default=ContainerDeployer, const=ACRContainerDeployer,
        help='Add the image to an Azure Container Registry and deploy from there.'
    )
    return parser


def main():
    parser = set_up_parser()
    args = parser.parse_args()

    credentials = ServicePrincipalCredentials(
        client_id=os.environ['AZURE_CLIENT_ID'],
        secret=os.environ['AZURE_CLIENT_SECRET'],
        tenant=os.environ['AZURE_TENANT_ID'],
    )

    deployer = args.deployer(
        ClientArgs(
            credentials,
            os.environ['AZURE_SUBSCRIPTION_ID'],
        ),
        args.image,
    )
    deployer.deploy()
    print('\nContacting ACS cluster. Response:')
    print(requests.get('http://{}'.format(deployer.public_ip())).text)

if __name__ == '__main__':
    sys.exit(main())
