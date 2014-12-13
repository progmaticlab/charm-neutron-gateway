#! /bin/bash

logger "Start running ns_ovs_cleanup.sh..."
logger " ** "
logger " ** "

logger "CRM_notify_task: $CRM_notify_task"
logger "CRM_notify_desc: $CRM_notify_desc"
logger "CRM_notify_rsc: $CRM_notify_rsc"
logger "CRM_notify_node: $CRM_notify_node"
logger " ** "
logger " ** "

if [[ ${CRM_notify_task} == 'monitor' && ${CRM_notify_desc} == 'unknown error' && 
      $CRM_notify_rsc == 'res_PingCheck' ]]; then
    hostname=`hostname`
    logger "monitor error hostname: $CRM_notify_node"
    logger "hostname: $hostname"
    if [ $hostname == $CRM_notify_node ]; then
        logger "Cleaning up namespace and ovs on node $CRM_notify_node !"
        for ns in $(ip netns list |grep 'qrouter-'); do ip netns delete $ns; done;
        for ns in $(ip netns list |grep 'qdhcp-'); do ip netns delete $ns; done;
        neutron-ovs-cleanup
        logger "Cleaning done."
    else
        sudo crm_node -p
        if [ $? -ne 0 ]; then
            logger "Failed to executing command 'crm_node -p'."
            exit
        fi
        nodes=`sudo crm_node -p`   
        logger "Cluster partition nodes: $nodes"
        if [ ! -z "$nodes" ]; then
            for node in $nodes
            do
                if [ "$node" != "$CRM_notify_node" ]; then
                    logger "Executing monitor to reschedule Neutron agents..."
                    sudo python /usr/local/bin/monitor.py
                    break
                fi
            done
        fi 
    fi
fi
