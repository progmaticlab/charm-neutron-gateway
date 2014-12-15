#! /bin/bash

logger " ** "
logger "Start running ns_ovs_cleanup.sh..."
logger " ** "

logger "CRM_notify_task: $CRM_notify_task"
logger "CRM_notify_desc: $CRM_notify_desc"
logger "CRM_notify_rsc: $CRM_notify_rsc"
logger "CRM_notify_node: $CRM_notify_node"
logger " ** "

set -x
CRM_notify_task="stop"
CRM_notify_desc="OK"
CRM_notify_rsc="res_PingCheck"

DEFAULT_PIDFILE="/tmp/monitor.pid"

function check_pid
{
    pid=`ps -aux | grep m\[o\]nitor.py | awk -F' ' '{print $2}'`
    if [ -n "$pid" ]; then
        if [ ! -f $DEFAULT_PIDFILE ]; then
            echo $pid > $DEFAULT_PIDFILE
        fi
        logger "Monitor already running."
        return 0
    fi
    return 1
}
function clean_pid
{
    logger "Clean pid."
    if [ -f $DEFAULT_PIDFILE ]; then
        pid=`cat $DEFAULT_PIDFILE`
        if [ ! -z $pid ]; then
            sudo kill -s 9 $pid
            rm -f $DEFAULT_PIDFILE
            logger "pidfile $DEFAULT_PIDFILE is removed."
        fi
    else
        pid=`ps -aux | grep m\[o\]nitor.py | awk -F' ' '{print $2}'`
        if [ ! -z $pid ]; then
            sudo kill -s 9 $pid
            logger "pid $pid is killed."
        fi
    fi  
}

#if [[ ${CRM_notify_task} == 'start' && $CRM_notify_rsc == 'res_PingCheck' ]]; then
if [[ $CRM_notify_rsc == 'res_PingCheck' && ${CRM_notify_task} == 'start' ]]; then
    if [[ ${CRM_notify_desc} == 'OK' || ${CRM_notify_desc} == 'ok' ]]; then
        check_pid
        if [ $? -ne 0 ]; then
            logger "Executing monitor to reschedule Neutron agents..."
            #sudo python /usr/local/bin/monitor.py  >> /dev/null 2>&1 & echo $! > $DEFAULT_PIDFILE
            sudo python monitor.py  >> /dev/null 2>&1 & echo $! 
            sleep 3
            pid=`ps -aux | grep m\[o\]nitor.py | awk -F' ' '{print $2}'`
            if [ ! -z "$pid" ]; then
                echo $pid > $DEFAULT_PIDFILE  
            fi
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

