---
services: azure-resource-manager
platforms: python
author: v-iam
---

# Deploy and connect to a Docker container in an Azure cluster

This sample is an end-to-end scenario for getting started
with Azure Container Service (ACS)
and, optionally, Azure Container Registry (ACR)
using the [Azure SDK for Python](http://azure-sdk-for-python.readthedocs.io/en/latest/).

**On this page**

- [Run this sample](#run)
- [What does container.py do?](#example)
- [How is the code laid out?](#code)
- [Notes and troubleshooting](#troubleshooting)

<a id="run"></a>

## Run this sample

1.  If you don't already have them, install the following:

    - [Python](https://www.python.org/downloads/)
    - [Docker](https://docs.docker.com/engine/installation/)

    You also need the following command-line tools,
    which are probably standard on Linux and OS X systems,
    but may need to be installed specially on Windows.
    (For this purpose, you might want to use the bash emulation included in
    [git-for-windows](https://git-for-windows.github.io),
    with [cmder](http://cmder.net) if you want a more featureful console as well.
    Using the [Windows Subsystem for Linux](https://msdn.microsoft.com/en-us/commandline/wsl/about) is an option too,
    but [there are some potential difficulties](#wsl) you should be aware of.)

    - Standard POSIX tools, specifically `chmod`
    - The [OpenSSH](http://www.openssh.com) suite of tools, specifically `ssh`,
      `scp`, `ssh-agent` and `ssh-add`

1.  We recommend that you use a Python [virtual environment](https://docs.python.org/3/tutorial/venv.html)
    to run this example, but it's not mandatory.
    You can initialize a virtualenv this way:

    ```
    pip install virtualenv
    virtualenv azurecontainer
    cd azurecontainer
    source bin/activate
    ```

1.  Clone the repository.

    ```
    git clone https://github.com/v-iam/container-sample.git
    ```

1.  Install the dependencies using pip.

    ```
    cd container-sample
    pip install -r requirements.txt
    ```

1.  Create an Azure service principal either through
[Azure CLI](https://azure.microsoft.com/documentation/articles/resource-group-authenticate-service-principal-cli/),
[PowerShell](https://azure.microsoft.com/documentation/articles/resource-group-authenticate-service-principal/)
or [the portal](https://azure.microsoft.com/documentation/articles/resource-group-create-service-principal-portal/).

    Retrieve the application ID (a.k.a. client ID),
    authentication key (a.k.a. client secret),
    tenant ID and subscription ID from the Azure portal for use
    in the next step.
    [This document](https://docs.microsoft.com/en-us/azure/azure-resource-manager/resource-group-create-service-principal-portal#get-application-id-and-authentication-key)
    describes where to find them (besides the subscription ID,
    which is in the "Overview" section of the "Subscriptions" blade.)

1.  Export these environment variables into your current shell. 

    ```
    export AZURE_TENANT_ID={your tenant id}
    export AZURE_CLIENT_ID={your client id}
    export AZURE_CLIENT_SECRET={your client secret}
    export AZURE_SUBSCRIPTION_ID={your subscription id}
    ```

1.  `container.py` requires a local Docker image.
    To get a sample one, use the following command:

    ```
    docker pull mesosphere/simple-docker
    ```

1.  Run the sample.
    The basic version just deploys the local image to ACS:

    ```
    python example.py
    ```

    The advanced version will push the image to ACR before deploying it:

    ```
    python example.py --use-acr
    ```

<a id="example"></a>

## What does example.py do?

`example.py` goes through all the necessary steps to take a Docker container,
optionally add it to a private registry using Azure Container Registry,
and then deploy it to a cluster in the cloud using Azure Container Services.

At a high level, those steps are as follows.
The ones marked with [ACR] are optional
and happen only if you use an Azure Container Registry
by specifying the `--use-acr` option.

1.  Create a resource group.

    The process that `container.py` implements
    requires creating several different resources in Azure.
    The [`ResourceHelper`](deployers/helpers/resource_helper.py) class creates a resource group
    to keep them organized and separate from other resources you may have,
    as well as allowing you to clean up after it easily by deleting the resource group.

1.  [ACR] Create a storage account.

    Several steps of this process require an Azure storage account,
    one of the resources mentioned in the previous step,
    to hold persistent data.
    The [`StorageHelper`](deployers/helpers/advanced/storage_helper.py) class can be used to manage a
    storage account with a specific name (and create one if it doesn't exist already).

1.  [ACR] Create an Azure Container Registry.

    An Azure Container Registry is private storage
    for you or your organization's Docker containers.
    The [`ContainerHelper`](deployers/helpers/container_helper.py) class creates one for you.

1.  [ACR] Create a file share and upload ACR credentials into it.

    To allow all the VMs in your cluster to access your Docker login credentials,
    you can put them in a file share in an Azure storage account.
    [`StorageHelper`](deployers/helpers/advanced/storage_helper.py) creates a share
    in the resource group from the first step.
    See [this documentation]( https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare#create-a-file-share-on-microsoft-azure)
    for information on other ways of doing this.

1.  [ACR] Upload ACR credentials into the file share.

    After the file share is created,
    [`RegistryHelper`](deployers/helpers/advanced/registry_helper.py)
    uploads the credentials into it.
    See [this documentation](https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-acr)
    for details on this process.

    Please also see [this note](#docker-creds) to make sure this step does the right thing!

1.  [ACR] Push your Docker image to the container registry.

    By default, `container.py` attempts to push the local image `mesosphere/simple-docker`
    that you pulled in [Run this sample](#run) above. If you'd rather use a different
    one, you can specify it using the `--image` option.

1.  Create a container service.

    To go along with the registry mentioned in the previous step,
    [`ContainerHelper`](deployers/helpers/container_helper.py) also creates a container service,
    with [DC/OS](https://dcos.io) as the orchestrator,
    to manage deployment of containers to a cluster of virtual machines.

1.  [ACR] Mount the file share with the Docker credentials in the cluster.

    To make sure every machine in the cluster can access the Docker credentials,
    they must have access to the file share they were uploaded to.
    To do this, `container.py` connects to the cluster's master machine,
    and from there to each other node in the cluster,
    and runs a script from each to mount the share.
    See [this documentation](https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare#mount-the-share-in-your-cluster) for details on this process.

1.  Deploy the image into the cluster.

    The final step in the process is the actual deployment of the image.
    This requires a SSH tunnel, as described in
    [this documentation](https://docs.microsoft.com/en-us/azure/container-service/container-service-connect#connect-to-a-dcos-or-swarm-cluster).
    Once the tunnel is set up, deployment requires only a single POST request against
    the [Marathon](https://mesosphere.github.io/marathon/) REST API.
    The request contents are derived from the example in
    [this documentation]( https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-acr#deploy-an-image-from-acr-with-marathon).

1.  Connect to the cluster.

    For demonstration purposes, `container.py` makes a simple request against the
    cluster's public IP address and prints the output.
    If you used the sample Docker container `mesosphere/simple-docker`,
    that output should be a basic HTML string:

    ```html
    <html>
        <body>
        <h1> Hello brave new world! </h1>
        </body>
    </html>
    ```

    If you see this, it means the tutorial ran successfully. You just connected to a
    Docker container running a Web server on a cluster in the Azure cloud!

<a id="code"></a>

## How is the code laid out?

The top-level script `example.py` is just the entry point for this example.
Most of the logic is in two deployers and several helpers.
The simple example uses the `deployers.container_deployer.ContainerDeployer` class
and the top-level helper classes in the `deployers.helpers` package.

The advanced example, which adds Azure Container Registry support,
uses the `deployers.acr_container_deployer.ACRContainerDeployer` class
and the helpers in `deployers.helpers.advanced` as well as those from one level up.

Additionally, there are some helper scripts
in the `deployers/scripts` subdirectory.
These are used only by the advanced example.

<a id="troubleshooting"></a>

## Notes and troubleshooting

### Running a container from ACR locally

If you have an image in an Azure Container Registry
and want to run it on your local machine,
you can do so using the Docker command line interface:

```
docker login -u <acr_username> -p <acr_password> <acr_server>
docker run -it <acr_server>/<container>:<tag>
```

To find the information you need to fill in,
refer to [this documentation](https://docs.microsoft.com/en-us/azure/container-registry/container-registry-get-started-portal#manage-registry-settings).

You can find more information about interacting with an ACR
through the command line
[here](https://docs.microsoft.com/en-us/azure/container-registry/container-registry-get-started-docker-cli).

### SSH configuration

Your first attempt to non-interactively connect to your Azure cluster
may fail because you need to verify the host.

If this happens, you'll see a message like this before the Python traceback:

```
The authenticity of host '<SOME_URL> (<SOME_IP>)' can't be established.
ECDSA key fingerprint is SHA256:<SOME_GIBBERISH>.
Are you sure you want to continue connecting (yes/no)?
Host key verification failed.
```

To resolve this, ssh to `<SOME_URL>` manually
and confirm the connection
after doing any necessary verification.
Then the host will be stored in your `known_hosts` file
and you should be able to connect non-interactively in the future.

<a id="docker-creds"></a>

### Docker credential storing

In the "Upload Docker credentials into the file share" step,
the example zips up the `.docker` directory so that it can be used
in deployment of the Docker container.
This works because the `docker login` command edits `.docker/config.json`
to add the login credentials to it,
so that they can be used to pull the image for the container
from the private registry.

On Windows, at least, and probably OS X as well,
Docker will attempt to use the OS's credential store
for your container registry login information,
rather than storing it in your `.docker/config.json` file.
This is probably more secure in general,
but it prevents the credential upload from working correctly
since it expects the credentials to be in `config.json`.

To make this work, you must edit `.docker/config.json`
and remove the "credsStore" entry from the JSON there.
(Make sure that what you leave is still valid JSON!)

<a id="wsl"></a>

### Docker and WSL (Windows Subsystem for Linux)

In principle, container.py should work on WSL
(a.k.a. "Bash on Ubuntu on Windows")
but in practice there are some difficulties.

The following are some issues you might run into,
with less-than-thoroughly detailed possible solutions.
If you're not comfortable implementing those solutions
based on the descriptions given,
using WSL might not be the right route for you.

1.  The Docker daemon does not work with WSL,
    but the client does.
    So you can install Docker for Windows
    as if you were not using WSL,
    and then install just the client binary for WSL.
    (The Windows binary is not compatible with WSL.)

1.  As of this writing,
    the best way to obtain the Docker client alone
    is to [download a binary release](https://github.com/moby/moby/releases)
    and put it somewhere on your `PATH`.
    For example, to get version v17.05.0-ce,
    do the following:

    ```
    wget https://get.docker.com/builds/Linux/x86_64/docker-17.05.0-ce.tgz
    tar -xzvf docker-17.05.0-ce.tgz
    cp docker/docker $LOCATION_ON_PATH
    ```

1.  Builds of WSL prior to 14936 have
    [an issue](https://github.com/Microsoft/BashOnWindows/issues/157)
    preventing SSH tunnelling into the cluster's master node from working.
    As a workaround,
    you can add an entry for the master node
    in `~/.ssh/config`
    with the parameter `AddressFamily inet`.

### Cleaning up

This example does not clean up after itself:
after it finishes running,
all the Azure entities it created will still exist.
To clean up,
simply delete the resource group it created.
By default that group is named 'containersample-group'.

## More information

Here are some helpful links:

- [Azure Python Development Center](https://azure.microsoft.com/develop/python/)
- [Azure Container Registry documentation](https://azure.microsoft.com/en-us/services/container-registry/)
- [Azure Container Service documentation](https://azure.microsoft.com/en-us/services/container-service/)

If you don't have a Microsoft Azure subscription you can get a FREE trial account [here](http://go.microsoft.com/fwlink/?LinkId=330212).

---

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

