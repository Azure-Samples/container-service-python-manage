---
services: azure-resource-manager
platforms: python
author: v-iam
---

# Deploy and connect to a Docker container in an Azure cluster

This sample is an end-to-end scenario for getting started
with Azure Container Registry (ACR)
and Azure Container Service (ACS)
using the [Azure SDK for Python](http://azure-sdk-for-python.readthedocs.io/en/latest/).

**On this page**

- [Run this sample](#run)
- [What does container.py do?](#example)
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
    with [cmder](http://cmder.net) if you want a more featureful console as well.)

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

    ```
    python container.py
    ```

<a id="example"></a>
## What does container.py do?

`container.py` goes through all the necessary steps to take a local Docker container,
add it to a private registry using Azure Container Registry,
and then deploy it to a cluster in the cloud using Azure Container Services.

At a high level, those steps are as follows:

1.  Create a resource group.

    The process that `container.py` implements
    requires creating several different resources in Azure.
    The [`ResourceHelper`](resource_helper.py) class creates a resource group
    to keep them organized and separate from other resources you may have,
    as well as allowing you to clean up after it easily by deleting the resource group.

1.  Create a storage account.

    Several steps of this process require an Azure storage account,
    one of the resources mentioned in the previous step,
    to hold persistent data.
    The [`StorageHelper`](storage_helper.py) class can be used to manage a
    storage account with a specific name (and create one if it doesn't exist already).

1.  Create an Azure Container Registry.

    An Azure Container Registry is private storage
    for you or your organization's Docker containers.
    The [`ContainerHelper`](container_helper.py) class creates one for you.

1.  Create a file share and upload ACR credentials into it.

    To allow all the VMs in your cluster to access your Docker login credentials,
    you can put them in a file share in an Azure storage account.
    [`StorageHelper`](storage_helper.py) creates a share
    in the resource group from the first step.
    See [this documentation]( https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare#create-a-file-share-on-microsoft-azure)
    for information on other ways of doing this.

1.  Upload ACR credentials into the file share.

    After the file share is created,
    [`ContainerHelper`](container_helper.py) uploads the credentials into it.
    See [this documentation](https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-acr)
    for details on this process.

1.  Push your Docker image to the container registry.

    By default, `container.py` attempts to push the local image `mesosphere/simple-docker`
    that you pulled in [Run this sample](#run) above. If you'd rather use a different
    one, you can specify it using the `--image` option.

1.  Create a container service.

    To go along with the registry mentioned in the previous step,
    [`ContainerHelper`] also creates a container service,
    with [DC/OS](https://dcos.io) as the orchestrator,
    to manage deployment of containers to a cluster of virtual machines.

1.  Mount the file share with the Docker credentials in the cluster.

    To make sure every machine in the cluster can access the Docker credentials,
    they must have access to the file share they were uploaded to.
    To do this, `container.py` connects to the cluster's master machine,
    and from there to each other node in the cluster,
    and runs a script from each to mount the share.
    See [this documentation](https://docs.microsoft.com/en-us/azure/container-service/container-service-dcos-fileshare#mount-the-share-in-your-cluster) for details on this process.

1.  Deploy the image from ACR into the cluster.

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

<a id="troubleshooting"></a>
## Notes and troubleshooting