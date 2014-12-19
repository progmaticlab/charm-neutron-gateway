#! /bin/bash

set -x
DEFAULT_PIDFILE="/tmp/monitor.pid"

function check_pid
{
    pid=`ps -aux | grep m\[o\]nitor.py | awk -F' ' '{print $2}'`
    if [ -n "$pid" ]; then
        logger "Monitor already running."
        return 0
    fi
    return 1
}
function clean_pid
{
    logger "Clean pid."
    pid=`ps -aux | grep m\[o\]nitor.py | awk -F' ' '{print $2}'`
    if [ ! -z $pid ]; then
        sudo kill -s 9 $pid
        logger "pid $pid is killed."
    fi
}

hostname=`uname -n`
if [[ $CRM_notify_node == $hostname ]]; then
    logger " ******************************************************************* "
    logger "CRM_notify_task: $CRM_notify_task, CRM_notify_desc: $CRM_notify_desc"
    logger "CRM_notify_rsc: $CRM_notify_rsc, CRM_notify_node: $CRM_notify_node"
    logger " ******************************************************************* "
fi

if [[ $CRM_notify_rsc == 'res_PingCheck' && ${CRM_notify_task} == 'start' && \
    $CRM_notify_node == $hostname ]]; then
    if [[ ${CRM_notify_desc} == 'OK' || ${CRM_notify_desc} == 'ok' ]]; then
        check_pid
        if [ $? -ne 0 ]; then
            logger "Executing monitor to reschedule Neutron agents..."
            sudo python /usr/local/bin/monitor.py --config-file /tmp/monitor.conf \
            --log-file /tmp/monitor.log >> /dev/null 2>&1 & echo $! 
            sleep 3
        fi
    fi
elif [[ $CRM_notify_rsc == 'res_PingCheck' && ${CRM_notify_task} == 'stop' ]]; then
    if [[ ${CRM_notify_desc} == 'OK' || ${CRM_notify_desc} == 'ok' ]]; then
        clean_pid
    fi 
elif [[ $CRM_notify_rsc == 'res_PingCheck' && ${CRM_notify_task} == 'monitor' ]]; then
    if [[ ${CRM_notify_desc} == 'unknown error' ]]; then
        logger "TODO"
    fi 
fi

