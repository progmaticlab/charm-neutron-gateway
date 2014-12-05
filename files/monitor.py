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

from neutron.openstack.common import log as logging

LOG = logging.getLogger(__name__)


class Pidfile(object):
    def __init__(self, pidfile, procname, uuid=None):
        self.pidfile = pidfile
        self.procname = procname
        self.uuid = uuid
        try:
            self.fd = os.open(pidfile, os.O_CREAT | os.O_RDWR)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            LOG.exception(_("Error while handling pidfile: %s"), pidfile)
            sys.exit(1)

    def __str__(self):
        return self.pidfile

    def unlock(self):
        if not not fcntl.flock(self.fd, fcntl.LOCK_UN):
            raise IOError(_('Unable to unlock pid file'))

    def write(self, pid):
        os.ftruncate(self.fd, 0)
        os.write(self.fd, "%d" % pid)
        os.fsync(self.fd)

    def read(self):
        try:
            pid = int(os.read(self.fd, 128))
            os.lseek(self.fd, 0, os.SEEK_SET)
            return pid
        except ValueError:
            return

    def is_running(self):
        pid = self.read()
        if not pid:
            return False

        cmdline = '/proc/%s/cmdline' % pid
        try:
            with open(cmdline, "r") as f:
                exec_out = f.readline()
            return self.procname in exec_out and (not self.uuid or
                                                  self.uuid in exec_out)
        except IOError:
            return False


class Daemon(object):
    """A generic daemon class.

    Usage: subclass the Daemon class and override the run() method
    """
    def __init__(self, pidfile, stdin='/dev/null', stdout='/dev/null',
                 stderr='/dev/null', procname='python', uuid=None):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.procname = procname
        self.pidfile = Pidfile(pidfile, procname, uuid)

    def _fork(self):
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError:
            LOG.exception(_('Fork failed'))
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
        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        stdin = open(self.stdin, 'r')
        stdout = open(self.stdout, 'a+')
        stderr = open(self.stderr, 'a+', 0)
        os.dup2(stdin.fileno(), sys.stdin.fileno())
        os.dup2(stdout.fileno(), sys.stdout.fileno())
        os.dup2(stderr.fileno(), sys.stderr.fileno())

        # write pidfile
        atexit.register(self.delete_pid)
        signal.signal(signal.SIGTERM, self.handle_sigterm)
        self.pidfile.write(os.getpid())
    def delete_pid(self):
        os.remove(str(self.pidfile))

    def handle_sigterm(self, signum, frame):
        sys.exit(0)

    def start(self):
        """Start the daemon."""

        if self.pidfile.is_running():
            self.pidfile.unlock()
            message = _('Pidfile %s already exist. Daemon already running?')
            LOG.error(message, self.pidfile)
            sys.exit(1)

        # Start the daemon
        self.daemonize()
        self.run()

    def run(self):
        """Override this method when subclassing Daemon.

        start() will call this method after the process has daemonized.
        """
        pass


class MonitorNeutronAgentsDaemon(Daemon):
    def __init__(self, check_interval=None):
        self.check_interval = check_interval
        log('Monitor Neutron Agent Loop Init')

    def get_env():
        env = {}
        with open('/etc/legacy_ha_env_data', 'r') as f:
            f.readline()
            data = f.split('=').strip()
            if data and data[0] and data[1]:
                env[data[0]] = env[data[1]]
            else:
                raise Exception("OpenStack env data uncomplete.")
        return env

    def reassign_agent_resources():
        ''' Use agent scheduler API to detect down agents and re-schedule '''
        env = get_env()
        if not env:
            log('Unable to re-assign resources at this time')
            return
        try:
            from quantumclient.v2_0 import client
        except ImportError:
            ''' Try to import neutronclient instead for havana+ '''
            from neutronclient.v2_0 import client

        auth_url = '%(auth_protocol)s://%(keystone_host)s:%(auth_port)s/v2.0' % env
        quantum = client.Client(username=env['service_username'],
                                password=env['service_password'],
                                tenant_name=env['service_tenant'],
                                auth_url=auth_url,
                                region_name=env['region'])

        partner_gateways = [unit_private_ip().split('.')[0]]
        for partner_gateway in relations_of_type(reltype='cluster'):
            gateway_hostname = get_hostname(partner_gateway['private-address'])
            partner_gateways.append(gateway_hostname.partition('.')[0])

        agents = quantum.list_agents(agent_type=DHCP_AGENT)
        dhcp_agents = []
        l3_agents = []
        networks = {}
        for agent in agents['agents']:
            if not agent['alive']:
                log('DHCP Agent %s down' % agent['id'])
                for network in \
                        quantum.list_networks_on_dhcp_agent(
                            agent['id'])['networks']:
                    networks[network['id']] = agent['id']
            else:
                if agent['host'].partition('.')[0] in partner_gateways:
                    dhcp_agents.append(agent['id'])

        agents = quantum.list_agents(agent_type=L3_AGENT)
        routers = {}
        for agent in agents['agents']:
            if not agent['alive']:
                log('L3 Agent %s down' % agent['id'])
                for router in \
                        quantum.list_routers_on_l3_agent(
                            agent['id'])['routers']:
                    routers[router['id']] = agent['id']
            else:
                if agent['host'].split('.')[0] in partner_gateways:
                    l3_agents.append(agent['id'])

        if len(dhcp_agents) == 0 or len(l3_agents) == 0:
            log('Unable to relocate resources, there are %s dhcp_agents and %s \
                 l3_agents in this cluster' % (len(dhcp_agents), len(l3_agents)))
            return

        index = 0
        for router_id in routers:
            agent = index % len(l3_agents)
            log('Moving router %s from %s to %s' %
                (router_id, routers[router_id], l3_agents[agent]))
            quantum.remove_router_from_l3_agent(l3_agent=routers[router_id],
                                                router_id=router_id)
            quantum.add_router_to_l3_agent(l3_agent=l3_agents[agent],
                                           body={'router_id': router_id})
            index += 1

        index = 0
        for network_id in networks:
            agent = index % len(dhcp_agents)
            log('Moving network %s from %s to %s' %
                (network_id, networks[network_id], dhcp_agents[agent]))
            quantum.remove_network_from_dhcp_agent(dhcp_agent=networks[network_id],
                                                   network_id=network_id)
            quantum.add_network_to_dhcp_agent(dhcp_agent=dhcp_agents[agent],
                                              body={'network_id': network_id})
            index += 1

    def run():
        log('Monitor Neutron Agent Loop Start')
        time.sleep(self.check_interval)
        reassign_agent_resources() 


def main():
    opts = [
    cfg.StrOpt('check_interval',
               default=15,
               help=_('Check Neutron Agents interval.')),
    ]

    cfg.CONF.register_cli_opts(opts)
    cfg.CONF(project='monitor_neutron_agents', default_config_files=[])

    monitor_daemon = MonitorNeutronAgentsDaemon(
            check_interval=cfg.CONF.check_interval)
    monitor_daemon.start()
