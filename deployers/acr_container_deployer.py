import io
import os
import subprocess
import sys
import traceback

from .container_deployer import ContainerDeployer
from .helpers.advanced.storage_helper import StorageHelper
from .helpers.advanced.registry_helper import ContainerRegistryHelper

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), 'scripts')


class ACRContainerDeployer(ContainerDeployer):
    def __init__(self, client_data, docker_image,
                 default_name='containersample',
                 location='South Central US',
                 resource_group=None,
                 storage_account=None,
                 container_registry=None):
        super().__init__(client_data, docker_image,
                         default_name=default_name,
                         location=location,
                         resource_group=resource_group)
        self.storage = StorageHelper(client_data, self.resources, account=storage_account)
        self.container_registry = ContainerRegistryHelper(
            client_data,
            self.resources,
            self.storage,
            container_registry
        )

    def _format_proc_output(self, header, output):
        if output:
            print(
                header,
                '\n'.join([
                    '    {}'.format(line)
                    for line in output.decode('utf-8').split('\n')
                ]),
                sep='\n',
                end='\n\n'
            )

    def scp_to_container_master(self, local_path, remote_path):
        address = self.container_service.master_ssh_login()
        try:
            subprocess.check_output([
                'scp',
                local_path,
                '{}:./{}'.format(address, remote_path)
            ])
        except subprocess.CalledProcessError:
            traceback.print_exc()
            print('It looks like an scp command failed.')
            print('Make sure you can ssh into the server without prompts.')
            print('Please run the following command to try it:')
            print('ssh {}'.format(address))
            sys.exit(1)

    def mount_shares(self):
        print('Mounting file share on all machines in cluster...')
        key_file = os.path.basename(self.container_service.get_key_path())
        # https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare
        with io.open(os.path.join(SCRIPTS_DIR, 'cifsMountTemplate.sh')) as cifsMount_template, \
             io.open(os.path.join(SCRIPTS_DIR, 'cifsMount.sh'), 'w', newline='\n') as cifsMount:
            cifsMount.write(
                cifsMount_template.read().format(
                    storageacct=self.storage.account.name,
                    sharename=self.storage.default_share,
                    username=self.container_registry.default_name,
                    password=self.storage.key,
                )
            )
        self.scp_to_container_master(os.path.join(SCRIPTS_DIR, 'cifsMount.sh'), '')
        self.scp_to_container_master(os.path.join(SCRIPTS_DIR, 'mountShares.sh'), '')
        self.scp_to_container_master(self.container_service.get_key_path(), key_file)
        with self.container_service.cluster_ssh() as proc:
            proc.stdin.write('chmod 600 {}\n'.format(key_file).encode('ascii'))
            proc.stdin.write(b'eval ssh-agent -s\n')
            proc.stdin.write('ssh-add {}\n'.format(key_file).encode('ascii'))
            mountShares_cmd = 'sh mountShares.sh ~/{}\n'.format(key_file)
            print('Running mountShares on remote master. Cmd:', mountShares_cmd, sep='\n')
            proc.stdin.write(mountShares_cmd.encode('ascii'))
            out, err = proc.communicate(input=b'exit\n')
        self._format_proc_output('Stdout:', out)
        self._format_proc_output('Stderr:', err)

    def deploy(self):
        registry_image_name = self.docker_image.split('/')[-1]
        self.container_registry.setup_image(self.docker_image, registry_image_name)
        self.mount_shares()
        self.container_service.deploy_container_from_registry(
            self.container_registry.get_docker_repo_tag(registry_image_name),
            self.container_registry
        )

