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

if [[ ${CRM_notify_task} == 'start' && $CRM_notify_rsc == 'res_PingCheck' ]]; then
    if [[ ${CRM_notify_desc} == 'OK' ]]; then
        hostname=`hostname`
        logger "monitor error hostname: $CRM_notify_node"
        logger "hostname: $hostname"
        logger "Executing monitor to reschedule Neutron agents..."
        sudo python /usr/local/bin/monitor.py
    fi
fi

