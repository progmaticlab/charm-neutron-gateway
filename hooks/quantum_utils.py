import os
import shutil
import stat
import subprocess
from charmhelpers.core.host import (
    service_running,
    service_stop,
    service_restart,
    lsb_release,
    mkdir
)
from charmhelpers.core.hookenv import (
    log,
    ERROR,
    config,
    relations_of_type,
    unit_private_ip,
    is_relation_made,
)
from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    apt_install,
)
from charmhelpers.contrib.network.ovs import (
    add_bridge,
    add_bridge_port,
    full_restart
)
from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    get_os_codename_package,
    get_hostname
)

from charmhelpers.contrib.openstack.neutron import (
    determine_dkms_package
)

import charmhelpers.contrib.openstack.context as context
from charmhelpers.contrib.openstack.context import (
    SyslogContext
)
import charmhelpers.contrib.openstack.templating as templating
from charmhelpers.contrib.openstack.neutron import headers_package
from quantum_contexts import (
    CORE_PLUGIN, OVS, NVP, NSX, N1KV,
    NEUTRON, QUANTUM,
    networking_name,
    QuantumGatewayContext,
    NetworkServiceContext,
    L3AgentContext,
    ExternalPortContext,
    DataPortContext,
    remap_plugin
)

from copy import deepcopy


def valid_plugin():
    return config('plugin') in CORE_PLUGIN[networking_name()]

QUANTUM_CONF_DIR = '/etc/quantum'

QUANTUM_OVS_PLUGIN_CONF = \
    "/etc/quantum/plugins/openvswitch/ovs_quantum_plugin.ini"
QUANTUM_NVP_PLUGIN_CONF = \
    "/etc/quantum/plugins/nicira/nvp.ini"
QUANTUM_PLUGIN_CONF = {
    OVS: QUANTUM_OVS_PLUGIN_CONF,
    NVP: QUANTUM_NVP_PLUGIN_CONF,
}

NEUTRON_CONF_DIR = '/etc/neutron'

NEUTRON_OVS_PLUGIN_CONF = \
    "/etc/neutron/plugins/openvswitch/ovs_neutron_plugin.ini"
NEUTRON_ML2_PLUGIN_CONF = \
    "/etc/neutron/plugins/ml2/ml2_conf.ini"
NEUTRON_NVP_PLUGIN_CONF = \
    "/etc/neutron/plugins/nicira/nvp.ini"
NEUTRON_NSX_PLUGIN_CONF = \
    "/etc/neutron/plugins/vmware/nsx.ini"

NEUTRON_PLUGIN_CONF = {
    OVS: NEUTRON_OVS_PLUGIN_CONF,
    NVP: NEUTRON_NVP_PLUGIN_CONF,
    NSX: NEUTRON_NSX_PLUGIN_CONF,
}

QUANTUM_GATEWAY_PKGS = {
    OVS: [
        "quantum-plugin-openvswitch-agent",
        "quantum-l3-agent",
        "quantum-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        "nova-api-metadata"
    ],
    NVP: [
        "openvswitch-switch",
        "quantum-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        "nova-api-metadata"
    ]
}

NEUTRON_GATEWAY_PKGS = {
    OVS: [
        "neutron-plugin-openvswitch-agent",
        "openvswitch-switch",
        "neutron-l3-agent",
        "neutron-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        'python-oslo.config',  # Force upgrade
        "nova-api-metadata",
        "neutron-plugin-metering-agent",
        "neutron-lbaas-agent",
    ],
    NVP: [
        "neutron-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        'python-oslo.config',  # Force upgrade
        "nova-api-metadata"
    ],
    N1KV: [
        "neutron-plugin-cisco",
        "neutron-dhcp-agent",
        "python-mysqldb",
        "python-psycopg2",
        "nova-api-metadata",
        "neutron-common",
        "neutron-l3-agent"
    ]
}
NEUTRON_GATEWAY_PKGS[NSX] = NEUTRON_GATEWAY_PKGS[NVP]

GATEWAY_PKGS = {
    QUANTUM: QUANTUM_GATEWAY_PKGS,
    NEUTRON: NEUTRON_GATEWAY_PKGS,
}

EARLY_PACKAGES = {
    OVS: ['openvswitch-datapath-dkms'],
    NVP: [],
    N1KV: []
}

LEGACY_HA_TEMPLATE_FILES = 'files'
LEGACY_FILES_MAP = {
    'monitor_neutron_ha.sh': {
        'path': '/usr/lib/ocf/resource.d/canonical',
        'permission': stat.S_IEXEC
    },
    'monitor.py': {
        'path': '/usr/local/bin/',
        'permission': stat.S_IEXEC
    },
    'monitor.conf': {
        'path': '/tmp',
        'permission': None
    }
}


def get_early_packages():
    '''Return a list of package for pre-install based on configured plugin'''
    if config('plugin') in [OVS]:
        pkgs = determine_dkms_package()
    else:
        return []

    # ensure headers are installed build any required dkms packages
    if [p for p in pkgs if 'dkms' in p]:
        return pkgs + [headers_package()]
    return pkgs


def get_packages():
    '''Return a list of packages for install based on the configured plugin'''
    plugin = remap_plugin(config('plugin'))
    packages = deepcopy(GATEWAY_PKGS[networking_name()][plugin])
    if (get_os_codename_install_source(config('openstack-origin'))
            >= 'icehouse' and plugin == 'ovs'
            and lsb_release()['DISTRIB_CODENAME'] < 'utopic'):
        # NOTE(jamespage) neutron-vpn-agent supercedes l3-agent for icehouse
        # but openswan was removed in utopic.
        packages.remove('neutron-l3-agent')
        packages.append('neutron-vpn-agent')
        packages.append('openswan')
    return packages


def get_common_package():
    if get_os_codename_package('quantum-common', fatal=False) is not None:
        return 'quantum-common'
    else:
        return 'neutron-common'

EXT_PORT_CONF = '/etc/init/ext-port.conf'
TEMPLATES = 'templates'

QUANTUM_CONF = "/etc/quantum/quantum.conf"
QUANTUM_L3_AGENT_CONF = "/etc/quantum/l3_agent.ini"
QUANTUM_DHCP_AGENT_CONF = "/etc/quantum/dhcp_agent.ini"
QUANTUM_METADATA_AGENT_CONF = "/etc/quantum/metadata_agent.ini"

NEUTRON_CONF = "/etc/neutron/neutron.conf"
NEUTRON_L3_AGENT_CONF = "/etc/neutron/l3_agent.ini"
NEUTRON_DHCP_AGENT_CONF = "/etc/neutron/dhcp_agent.ini"
NEUTRON_DNSMASQ_CONF = "/etc/neutron/dnsmasq.conf"
NEUTRON_METADATA_AGENT_CONF = "/etc/neutron/metadata_agent.ini"
NEUTRON_METERING_AGENT_CONF = "/etc/neutron/metering_agent.ini"
NEUTRON_LBAAS_AGENT_CONF = "/etc/neutron/lbaas_agent.ini"
NEUTRON_VPNAAS_AGENT_CONF = "/etc/neutron/vpn_agent.ini"
NEUTRON_FWAAS_CONF = "/etc/neutron/fwaas_driver.ini"

NOVA_CONF_DIR = '/etc/nova'
NOVA_CONF = "/etc/nova/nova.conf"

NOVA_CONFIG_FILES = {
    NOVA_CONF: {
        'hook_contexts': [context.SharedDBContext(ssl_dir=NOVA_CONF_DIR),
                          context.PostgresqlDBContext(),
                          NetworkServiceContext(),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['nova-api-metadata']
    },
}

QUANTUM_SHARED_CONFIG_FILES = {
    QUANTUM_DHCP_AGENT_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['quantum-dhcp-agent']
    },
    QUANTUM_METADATA_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          QuantumGatewayContext()],
        'services': ['quantum-metadata-agent']
    },
}
QUANTUM_SHARED_CONFIG_FILES.update(NOVA_CONFIG_FILES)

NEUTRON_SHARED_CONFIG_FILES = {
    NEUTRON_DHCP_AGENT_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_DNSMASQ_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_METADATA_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          QuantumGatewayContext()],
        'services': ['neutron-metadata-agent']
    },
}
NEUTRON_SHARED_CONFIG_FILES.update(NOVA_CONFIG_FILES)

QUANTUM_OVS_CONFIG_FILES = {
    QUANTUM_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=QUANTUM_CONF_DIR),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['quantum-l3-agent',
                     'quantum-dhcp-agent',
                     'quantum-metadata-agent',
                     'quantum-plugin-openvswitch-agent']
    },
    QUANTUM_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          QuantumGatewayContext()],
        'services': ['quantum-l3-agent']
    },
    QUANTUM_OVS_PLUGIN_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['quantum-plugin-openvswitch-agent']
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': []
    }
}
QUANTUM_OVS_CONFIG_FILES.update(QUANTUM_SHARED_CONFIG_FILES)

NEUTRON_OVS_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-openvswitch-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-plugin-vpn-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          QuantumGatewayContext()],
        'services': ['neutron-l3-agent']
    },
    NEUTRON_METERING_AGENT_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-plugin-metering-agent',
                     'neutron-metering-agent']
    },
    NEUTRON_LBAAS_AGENT_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-lbaas-agent']
    },
    NEUTRON_VPNAAS_AGENT_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-plugin-vpn-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_FWAAS_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-l3-agent']
    },
    NEUTRON_OVS_PLUGIN_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    NEUTRON_ML2_PLUGIN_CONF: {
        'hook_contexts': [QuantumGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': []
    }
}
NEUTRON_OVS_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

QUANTUM_NVP_CONFIG_FILES = {
    QUANTUM_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=QUANTUM_CONF_DIR),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['quantum-dhcp-agent', 'quantum-metadata-agent']
    },
}
QUANTUM_NVP_CONFIG_FILES.update(QUANTUM_SHARED_CONFIG_FILES)

NEUTRON_NVP_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['neutron-dhcp-agent', 'neutron-metadata-agent']
    },
}
NEUTRON_NVP_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_N1KV_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          QuantumGatewayContext(),
                          SyslogContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          QuantumGatewayContext()],
        'services': ['neutron-l3-agent']
    },
}
NEUTRON_N1KV_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

CONFIG_FILES = {
    QUANTUM: {
        NVP: QUANTUM_NVP_CONFIG_FILES,
        OVS: QUANTUM_OVS_CONFIG_FILES,
    },
    NEUTRON: {
        NSX: NEUTRON_NVP_CONFIG_FILES,
        NVP: NEUTRON_NVP_CONFIG_FILES,
        OVS: NEUTRON_OVS_CONFIG_FILES,
        N1KV: NEUTRON_N1KV_CONFIG_FILES,
    },
}


def register_configs():
    ''' Register config files with their respective contexts. '''
    release = get_os_codename_install_source(config('openstack-origin'))
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)

    plugin = remap_plugin(config('plugin'))
    name = networking_name()
    if plugin == 'ovs':
        # NOTE: deal with switch to ML2 plugin for >= icehouse
        drop_config = NEUTRON_ML2_PLUGIN_CONF
        if release >= 'icehouse':
            drop_config = NEUTRON_OVS_PLUGIN_CONF
        if drop_config in CONFIG_FILES[name][plugin]:
            CONFIG_FILES[name][plugin].pop(drop_config)

    if is_relation_made('amqp-nova'):
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp-nova',
            relation_prefix='nova')
    else:
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp')
    CONFIG_FILES[name][plugin][NOVA_CONF][
        'hook_contexts'].append(amqp_nova_ctxt)
    for conf in CONFIG_FILES[name][plugin]:
        configs.register(conf,
                         CONFIG_FILES[name][plugin][conf]['hook_contexts'])
    return configs


def stop_services():
    name = networking_name()
    svcs = set()
    for ctxt in CONFIG_FILES[name][config('plugin')].itervalues():
        for svc in ctxt['services']:
            svcs.add(svc)
    for svc in svcs:
        service_stop(svc)


def restart_map():
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    _map = {}
    plugin = config('plugin')
    name = networking_name()
    for f, ctxt in CONFIG_FILES[name][plugin].iteritems():
        svcs = []
        for svc in ctxt['services']:
            svcs.append(svc)
        if svcs:
            _map[f] = svcs
    return _map


INT_BRIDGE = "br-int"
EXT_BRIDGE = "br-ex"
DATA_BRIDGE = 'br-data'

DHCP_AGENT = "DHCP Agent"
L3_AGENT = "L3 Agent"


# TODO: make work with neutron
def reassign_agent_resources():
    ''' Use agent scheduler API to detect down agents and re-schedule '''
    env = NetworkServiceContext()()
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

    partner_gateways = get_quantum_gateway_cluster_nodes()
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


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def do_openstack_upgrade():
    """
    Perform an upgrade.  Takes care of upgrading packages, rewriting
    configs, database migrations and potentially any other post-upgrade
    actions.
    """
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)
    log('Performing OpenStack upgrade to %s.' % (new_os_rel))

    configure_installation_source(new_src)
    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update(fatal=True)
    apt_upgrade(options=dpkg_opts,
                fatal=True, dist=True)
    apt_install(get_early_packages(), fatal=True)
    apt_install(get_packages(), fatal=True)

    # set CONFIGS to load templates from new release
    configs = register_configs()
    configs.write_all()
    [service_restart(s) for s in services()]
    return configs


def configure_ovs():
    if config('plugin') == OVS:
        if not service_running('openvswitch-switch'):
            full_restart()
        add_bridge(INT_BRIDGE)
        add_bridge(EXT_BRIDGE)
        ext_port_ctx = ExternalPortContext()()
        if ext_port_ctx and ext_port_ctx['ext_port']:
            add_bridge_port(EXT_BRIDGE, ext_port_ctx['ext_port'])

        add_bridge(DATA_BRIDGE)
        data_port_ctx = DataPortContext()()
        if data_port_ctx and data_port_ctx['data_port']:
            add_bridge_port(DATA_BRIDGE, data_port_ctx['data_port'],
                            promisc=True)


def get_dns_host():
    dns_hosts = []
    try:
        output = subprocess.check_output(['grep', 'nameserver',
                                          '/etc/resolv.conf'])
        nameservers = output.split('\n')
        dns_hosts = [(ns.split(' ')[1].split('\n')[0].strip() + ' ')
                     for ns in nameservers if ns.startswith('nameserver')
                     and ns.split(' ')[1]]
    except Exception:
        log('Failed to get nameserver from resolv.conf !', level=ERROR)

    if config('dns_hosts'):
        dnss = config('dns_hosts').split(' ')
        for dns in dnss:
            dns_hosts.append(dns)

    return ' '.join(dns_hosts)


def get_quantum_gateway_cluster_nodes():
    partner_gateways = get_hostname(unit_private_ip())
    for partner_gateway in relations_of_type(reltype='cluster'):
        gateway_hostname = get_hostname(partner_gateway['private-address'])
        partner_gateways.append(gateway_hostname.partition('.')[0])
    return partner_gateways


def copy_file(source_dir, des_dir, f, f_mod=None, update=False):
    if not os.path.isdir(des_dir):
        mkdir(des_dir)
        log('Directory created at: %s' % des_dir)

    if not os.path.isfile(os.path.join(des_dir, f)) or update:
        try:
            source_f = os.path.join(source_dir, f)
            des_f = os.path.join(des_dir, f)
            shutil.copy2(source_f, des_dir)
            if f_mod:
                os.chmod(des_f, f_mod)
        except IOError:
            log('Failed to copy file from %s to %s.' %
                (source_f, des_dir), level=ERROR)
            raise


def remove_file(des_dir, f):
    if not os.path.isdir(des_dir):
        log('Directory %s already removed.' % des_dir)

    f = os.path.join(des_dir, f)
    if os.path.isfile(f):
        try:
            os.remove(f)
        except IOError:
            log('Failed to remove file %s.' % f, level=ERROR)


def get_external_agent_f():
    agent = 'monitor_neutron_ha.sh'
    exec_dir = '/usr/lib/ocf/resource.d/canonical'
    return os.path.join(exec_dir, agent)


def init_canonical_ping_file(update=False):
    f = 'ping'
    exec_dir = '/usr/lib/ocf/resource.d/canonical'
    copy_file(LEGACY_HA_TEMPLATE_FILES, exec_dir,
              f, update=update)


def install_legacy_ha_files(update=False):
    for f, p in LEGACY_FILES_MAP.iteritems():
        copy_file(LEGACY_HA_TEMPLATE_FILES, p['path'], f,
                  p['permission'], update=update)


def remove_legacy_ha_files():
    for f, p in LEGACY_FILES_MAP.iteritems():
        remove_file(p['path'], f)


def update_legacy_ha_files(update=False):
    if config('ha-legacy-mode'):
        install_legacy_ha_files(update=update)
    else:
        remove_legacy_ha_files()


def cache_env_data():
    env = NetworkServiceContext()()
    if not env:
        log('Unable to get NetworkServiceContext at this time', level=ERROR)
        return

    no_envrc = False
    envrc_f = '/etc/legacy_ha_envrc'
    if os.path.isfile(envrc_f):
        with open(envrc_f, 'r') as f:
            data = f.read()
        data = data.strip().split('\n')

        diff = False
        for line in data:
            k = line.split('=')[0]
            v = line.split('=')[1]
            if k not in env or v != env[k]:
                diff = True
                break
    else:
        no_envrc = True

    if no_envrc or diff:
        with open(envrc_f, 'w') as f:
            for k, v in env.items():
                f.write(''.join([k, '=', v, '\n']))
