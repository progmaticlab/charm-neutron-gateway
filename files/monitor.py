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
import time

from oslo.config import cfg
import logging as LOG


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
    def __init__(self, check_interval=None):
        super(MonitorNeutronAgentsDaemon, self).__init__()
        self.check_interval = check_interval
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
            else:
                dhcp_agents.append(agent['id'])

        agents = quantum.list_agents(agent_type=L3_AGENT)
        routers = {}
        for agent in agents['agents']:
            if not agent['alive']:
                LOG.info('L3 Agent %s down' % agent['id'])
                for router in \
                        quantum.list_routers_on_l3_agent(
                            agent['id'])['routers']:
                    routers[router['id']] = agent['id']
            else:
                l3_agents.append(agent['id'])

        if len(dhcp_agents) == 0 or len(l3_agents) == 0:
            LOG.info('Unable to relocate resources, there are %s dhcp_agents '
                     'and %s l3_agents in this cluster' % (len(dhcp_agents),
                                                           len(l3_agents)))
            return

        index = 0
        for router_id in routers:
            agent = index % len(l3_agents)
            LOG.info('Moving router %s from %s to %s' %
                     (router_id, routers[router_id], l3_agents[agent]))
            quantum.remove_router_from_l3_agent(l3_agent=routers[router_id],
                                                router_id=router_id)
            quantum.add_router_to_l3_agent(l3_agent=l3_agents[agent],
                                           body={'router_id': router_id})
            index += 1

        index = 0
        for network_id in networks:
            agent = index % len(dhcp_agents)
            LOG.info('Moving network %s from %s to %s' %
                     (network_id, networks[network_id], dhcp_agents[agent]))
            quantum.remove_network_from_dhcp_agent(
                dhcp_agent=networks[network_id], network_id=network_id)
            quantum.add_network_to_dhcp_agent(dhcp_agent=dhcp_agents[agent],
                                              body={'network_id': network_id})
            index += 1

    def run(self):
        LOG.info('Monitor Neutron Agent Loop Start')
        self.reassign_agent_resources()


if __name__ == '__main__':
    opts = [
        cfg.StrOpt('check_interval',
                   default=15,
                   help='Check Neutron Agents interval.'),
        cfg.StrOpt('log_file',
                   default='/var/log/monitor.log',
                   help='log file'),
    ]

    cfg.CONF.register_cli_opts(opts)
    cfg.CONF(project='monitor_neutron_agents', default_config_files=[])

    LOG.basicConfig(filename=cfg.CONF.log_file, level=LOG.INFO)
    monitor_daemon = MonitorNeutronAgentsDaemon(
        check_interval=cfg.CONF.check_interval)
    monitor_daemon.start()
