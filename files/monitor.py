# Copyright 2012 New Dream Network, LLC (DreamHost)
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import atexit
import fcntl
import os
import signal
import sys
import subprocess
import time

from oslo.config import cfg
from neutron.agent.linux import ovs_lib
from neutron.agent.linux import ip_lib
from neutron.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class Daemon(object):
    """A generic daemon class.

    Usage: subclass the Daemon class and override the run() method
    """
    def __init__(self, stdin='/dev/null', stdout='/dev/null',
                 stderr='/dev/null', procname='python', uuid=None):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.procname = procname

    def _fork(self):
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError:
            LOG.exception('Fork failed')
            sys.exit(1)

    def daemonize(self):
        """Daemonize process by doing Stevens double fork."""
        # fork first time
        self._fork()

        # decouple from parent environment
        os.chdir("/")
        os.setsid()
        os.umask(0)
        # fork second time
        self._fork()

        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        stdin = open(self.stdin, 'r')
        stdout = open(self.stdout, 'a+')
        stderr = open(self.stderr, 'a+', 0)
        os.dup2(stdin.fileno(), sys.stdin.fileno())
        os.dup2(stdout.fileno(), sys.stdout.fileno())
        os.dup2(stderr.fileno(), sys.stderr.fileno())

        signal.signal(signal.SIGTERM, self.handle_sigterm)

    def handle_sigterm(self, signum, frame):
        sys.exit(0)

    def start(self):
        """Start the daemon."""
        self.daemonize()
        self.run()

    def run(self):
        """Override this method when subclassing Daemon.

        start() will call this method after the process has daemonized.
        """
        pass


class MonitorNeutronAgentsDaemon(Daemon):
    def __init__(self):
        super(MonitorNeutronAgentsDaemon, self).__init__()
        logging.setup('Neuron-HA-Monitor')
        LOG.info('Monitor Neutron Agent Loop Init')
        self.env = {}

    def get_env(self):
        envrc_f = '/etc/legacy_ha_envrc'
        envrc_f_m = False
        if os.path.isfile(envrc_f):
            ctime = time.ctime(os.stat(envrc_f).st_ctime)
            mtime = time.ctime(os.stat(envrc_f).st_mtime)
            if ctime != mtime:
                envrc_f_m = True

            if not self.env or envrc_f_m:
                with open(envrc_f, 'r') as f:
                    for line in f:
                        data = line.strip().split('=')
                        if data and data[0] and data[1]:
                            self.env[data[0]] = data[1]
                        else:
                            raise Exception("OpenStack env data uncomplete.")
        return self.env

    def get_hostname(self):
        return str(subprocess.check_output(['uname', '-n'])).strip()

    def get_root_helper(self):
        return 'sudo'

    def unplug_device(self, conf, device):
        try:
            device.link.delete()
        except RuntimeError:
            root_helper = self.get_root_helper()
            # Maybe the device is OVS port, so try to delete
            bridge_name = ovs_lib.get_bridge_for_iface(root_helper, device.name)
            if bridge_name:
                bridge = ovs_lib.OVSBridge(bridge_name, root_helper)
                bridge.delete_port(device.name)
            else:
                LOG.debug(_('Unable to find bridge for device: %s'), device.name)

    def cleanup_dhcp(self, networks):
        namespaces = []
        for network, agent in networks.iteritems():
            namespaces.append('qdhcp-' + network)

        if namespaces:
            LOG.info('Namespaces: %s is going to be deleted.' % namespaces)
            destroy_namespaces(namespaces)

    def cleanup_router(self, routers):
        namespaces = []
        for router, agent in routers.iteritems():
            namespaces.append('qrouter-' + router)

        if namespaces:
            LOG.info('Namespaces: %s is going to be deleted.' % namespaces)
            destroy_namespaces(namespaces)

    def destroy_namespaces(self, namespaces):
        try:
            root_helper = self.get_root_helper()
            for namespace in namespaces:
                ip = ip_lib.IPWrapper(root_helper, namespace)
                if ip.netns.exists(namespace):
                    for device in ip.get_devices(exclude_loopback=True):
                         unplug_device(device)

            ip.garbage_collect_namespace()
        except Exception:
            LOG.exception(_('Error unable to destroy namespace: %s'), namespace) 
    def is_same_host(self, host):
        return str(host).strip() == self.get_hostname()

    def l3_agents_reschedule(self, l3_agents, routers, quantum):
        if not self.is_same_host(l3_agents[0]['host']): 
            LOG.info('Only the first l3 agent %s could reschedule. '
                     % l3_agents[0]['host'])
            return

        index = 0
        for router_id in routers:
            agent = index % len(l3_agents)
            LOG.info('Moving router %s from %s to %s' %
                     (router_id, routers[router_id], l3_agents[agent]['id']))
            quantum.remove_router_from_l3_agent(l3_agent=routers[router_id],
                                                router_id=router_id)
            quantum.add_router_to_l3_agent(l3_agent=l3_agents[agent]['id'],
                                           body={'router_id': router_id})
            index += 1

    def dhcp_agents_reschedule(self, dhcp_agents, networks, quantum):
        if not is_same_host(dhcp_agents[0]['host']):
            LOG.info('Only the first dhcp agent %s could reschedule. '
                     % dhcp_agents[0]['host'])
            return

        index = 0
        for network_id in networks:
            agent = index % len(dhcp_agents)
            LOG.info('Moving network %s from %s to %s' %
                     (network_id, networks[network_id], dhcp_agents[agent]['id']))
            quantum.remove_network_from_dhcp_agent(
                dhcp_agent=networks[network_id], network_id=network_id)
            quantum.add_network_to_dhcp_agent(dhcp_agent=dhcp_agents[agent]['id'],
                                              body={'network_id': network_id})
            index += 1
        
    def reassign_agent_resources(self):
        ''' Use agent scheduler API to detect down agents and re-schedule '''
        DHCP_AGENT = "DHCP Agent"
        L3_AGENT = "L3 Agent"
        env = self.get_env()
        if not env:
            LOG.info('Unable to re-assign resources at this time')
            return
        try:
            from quantumclient.v2_0 import client
        except ImportError:
            ''' Try to import neutronclient instead for havana+ '''
            from neutronclient.v2_0 import client

        auth_url = '%(auth_protocol)s://%(keystone_host)s:%(auth_port)s/v2.0' \
                   % env
        quantum = client.Client(username=env['service_username'],
                                password=env['service_password'],
                                tenant_name=env['service_tenant'],
                                auth_url=auth_url,
                                region_name=env['region'])

        partner_gateways = []

        agents = quantum.list_agents(agent_type=DHCP_AGENT)
        dhcp_agents = []
        l3_agents = []
        networks = {}
        for agent in agents['agents']:
            if not agent['alive']:
                LOG.info('DHCP Agent %s down' % agent['id'])
                for network in \
                        quantum.list_networks_on_dhcp_agent(
                            agent['id'])['networks']:
                    networks[network['id']] = agent['id']
                    if is_same_host(agent['host']):
                        self.cleanup_dhcp(networks)
            else:
                dhcp_agents.append(agent)
                LOG.info('Active dhcp agents: %s' % dhcp_agents)
    
        agents = quantum.list_agents(agent_type=L3_AGENT)
        routers = {}
        for agent in agents['agents']:
            if not agent['alive']:
                LOG.info('L3 Agent %s down' % agent['id'])
                for router in \
                        quantum.list_routers_on_l3_agent(
                            agent['id'])['routers']:
                    routers[router['id']] = agent['id']
                    if is_same_host(agent['host']):
                        self.cleanup_router(routers)
            else:
                l3_agents.append(agent)
                LOG.info('Active l3 agents: %s' % l3_agents)

        if not networks and not routers:
            LOG.info('No networks and routers hosted on failed agents.')
            return

        if len(dhcp_agents) == 0 and len(l3_agents) == 0:
            LOG.error('Unable to relocate resources, there are %s dhcp_agents '
                      'and %s l3_agents in this cluster' % (len(dhcp_agents),
                                                           len(l3_agents)))
            return

        if len(l3_agents) != 0:
            self.l3_agents_reschedule(l3_agents, routers)

        if len(dhcp_agents) != 0:
            self.dhcp_agents_reschedule(dhcp_agents, networks)

    def run(self):
        while True:
            LOG.info('Monitor Neutron HA Agent Loop Start')
            self.reassign_agent_resources()
            LOG.info('sleep %s' % cfg.CONF.check_interval)
            time.sleep(float(cfg.CONF.check_interval))


if __name__ == '__main__':
    opts = [
        cfg.StrOpt('check_interval',
                   default=15,
                   help='Check Neutron Agents interval.'),
    ]

    cfg.CONF.register_cli_opts(opts)
    cfg.CONF(project='monitor_neutron_agents', default_config_files=[])
    logging.setup('Neuron-HA-Monitor')
    monitor_daemon = MonitorNeutronAgentsDaemon()
    monitor_daemon.start()
