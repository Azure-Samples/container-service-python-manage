# mountShares.sh
# This file must have LF (UNIX-style) line endings!

keyfile=$1

sh cifsMount.sh

# Install jq used for the next command
sudo apt-get install -y jq

# Get the IP address of each node using the mesos API and store it inside a file called nodes
curl http://leader.mesos:1050/system/health/v1/nodes | jq '.nodes[].host_ip' | sed 's/\"//g' | sed '/172/d' > nodes

# From the previous file created, run our script to mount our share on each node
while read line; do
    ssh `whoami`@$line -o StrictHostKeyChecking=no -i ${keyfile} < ./cifsMount.sh
done < nodes