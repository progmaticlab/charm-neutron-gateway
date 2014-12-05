#! /bin/bash

if [[ ${CRM_notify_task} == 'monitor' && ${CRM_notify_desc} == 'unknown error' && 
      $CRM_notify_rsc == 'res_PingCheck' ]]; then
    hostname=`hostname`
    if [ $hostname == $CRM_notify_node ]; then
        echo "Cleaning up namespace and ovs on node $CRM_notify_node !"
        for ns in $(ip netns list |grep 'qrouter-'); do ip netns delete $ns; done;
        for ns in $(ip netns list |grep 'qdhcp-'); do ip netns delete $ns; done;
        neutron-ovs-cleanup
        echo "Cleaning done."
    fi
fi
