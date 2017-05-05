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

<a id="run"></a>
## Run this sample

1. If you don't already have them, install the following:

    - [Python](https://www.python.org/downloads/)
    - [Docker](https://docs.docker.com/engine/installation/)

1. We recommend that you use a Python [virtual environment](https://docs.python.org/3/tutorial/venv.html)
to run this example, but it's not mandatory.
You can initialize a virtualenv this way:

    ```
    pip install virtualenv
    virtualenv azurecontainer
    cd azurecontainer
    source bin/activate
    ```

1. Clone the repository.

    ```
    git clone https://github.com/v-iam/container-sample.git
    ```

1. Install the dependencies using pip.

    ```
    cd container-sample
    pip install -r requirements.txt
    ```

1. Create an Azure service principal either through
[Azure CLI](https://azure.microsoft.com/documentation/articles/resource-group-authenticate-service-principal-cli/),
[PowerShell](https://azure.microsoft.com/documentation/articles/resource-group-authenticate-service-principal/)
or [the portal](https://azure.microsoft.com/documentation/articles/resource-group-create-service-principal-portal/).

1. Export these environment variables into your current shell. 

    ```
    export AZURE_TENANT_ID={your tenant id}
    export AZURE_CLIENT_ID={your client id}
    export AZURE_CLIENT_SECRET={your client secret}
    export AZURE_SUBSCRIPTION_ID={your subscription id}
    ```

1. Run the sample.

    ```
    python example.py
    ```

<a id="example"></a>
## What does example.py do?

`container.py` goes through all the necessary steps to take a local Docker container,
add it to a private registry using Azure Container Registry,
and then deploy it to a cluster in the cloud using Azure Container Services.

At a high level, those steps are as follows:

1.  Create a resource group.

    The process that `container.py` implements requires creating several different resources
    in Azure, so it creates a resource group to keep them organized and separate from
    other resources you may have, as well as allowing you to clean up after it easily by deleting
    the resource group.

1.  Create a storage account.

    Several steps of this process require an Azure storage account, one of the resources
    mentioned in the previous step, to hold persistent data.

1.  Create a container registry.

    An Azure Container Registry is private storage for you or your organization's Docker containers.
    This sample code will create one for you.

1.  Create a container service.

    To go along with the registry mentioned in the previous step, this code creates a container
    service to manage deployment of containers to a cluster of virtual machines.
