import os
import json
import shutil
import subprocess
from shutil import copy2
from charmhelpers.core.host import (
    lsb_release,
    mkdir,
    service_running,
    service_stop,
    service_restart,
    init_is_systemd,
    CompareHostReleases,
)
from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    INFO,
    ERROR,
    config,
    is_relation_made,
    relation_ids,
)
from charmhelpers.fetch import (
    apt_upgrade,
    apt_update,
    apt_install,
)
from charmhelpers.contrib.network.ovs import (
    add_bridge,
    add_bridge_port,
    is_linuxbridge_interface,
    add_ovsbridge_linuxbridge,
    full_restart,
    enable_ipfix,
    disable_ipfix,
)
from charmhelpers.contrib.hahelpers.cluster import (
    get_hacluster_config,
)
from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    get_os_codename_install_source,
    make_assess_status_func,
    os_release,
    pause_unit,
    reset_os_release,
    resume_unit,
    os_application_version_set,
    CompareOpenStackReleases,
)

from charmhelpers.contrib.openstack.neutron import (
    determine_dkms_package
)

import charmhelpers.contrib.openstack.context as context
from charmhelpers.contrib.openstack.context import (
    SyslogContext,
    NeutronAPIContext,
    NetworkServiceContext,
    ExternalPortContext,
    PhyNICMTUContext,
    DataPortContext,
)
import charmhelpers.contrib.openstack.templating as templating
from charmhelpers.contrib.openstack.neutron import headers_package
from neutron_contexts import (
    CORE_PLUGIN, OVS, NSX, N1KV, OVS_ODL,
    NeutronGatewayContext,
    L3AgentContext,
    NovaMetadataContext,
)
from charmhelpers.contrib.openstack.neutron import (
    parse_bridge_mappings,
)

from copy import deepcopy


def valid_plugin():
    return config('plugin') in CORE_PLUGIN


NEUTRON_COMMON = 'neutron-common'
VERSION_PACKAGE = NEUTRON_COMMON

NEUTRON_CONF_DIR = '/etc/neutron'

NEUTRON_ML2_PLUGIN_CONF = \
    "/etc/neutron/plugins/ml2/ml2_conf.ini"
NEUTRON_OVS_AGENT_CONF = \
    "/etc/neutron/plugins/ml2/openvswitch_agent.ini"
NEUTRON_NVP_PLUGIN_CONF = \
    "/etc/neutron/plugins/nicira/nvp.ini"
NEUTRON_NSX_PLUGIN_CONF = \
    "/etc/neutron/plugins/vmware/nsx.ini"

NEUTRON_PLUGIN_CONF = {
    OVS: NEUTRON_ML2_PLUGIN_CONF,
    NSX: NEUTRON_NSX_PLUGIN_CONF,
}

NEUTRON_DHCP_AA_PROFILE = 'usr.bin.neutron-dhcp-agent'
NEUTRON_L3_AA_PROFILE = 'usr.bin.neutron-l3-agent'
NEUTRON_LBAAS_AA_PROFILE = 'usr.bin.neutron-lbaas-agent'
NEUTRON_LBAASV2_AA_PROFILE = 'usr.bin.neutron-lbaasv2-agent'
NEUTRON_METADATA_AA_PROFILE = 'usr.bin.neutron-metadata-agent'
NEUTRON_METERING_AA_PROFILE = 'usr.bin.neutron-metering-agent'
NOVA_API_METADATA_AA_PROFILE = 'usr.bin.nova-api-metadata'
NEUTRON_OVS_AA_PROFILE = 'usr.bin.neutron-openvswitch-agent'

APPARMOR_PROFILES = [
    NEUTRON_DHCP_AA_PROFILE,
    NEUTRON_L3_AA_PROFILE,
    NEUTRON_LBAAS_AA_PROFILE,
    NEUTRON_METADATA_AA_PROFILE,
    NEUTRON_METERING_AA_PROFILE,
    NOVA_API_METADATA_AA_PROFILE,
    NEUTRON_OVS_AA_PROFILE
]

NEUTRON_OVS_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                               ''.format(NEUTRON_OVS_AA_PROFILE))
NEUTRON_DHCP_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                ''.format(NEUTRON_DHCP_AA_PROFILE))
NEUTRON_L3_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                              ''.format(NEUTRON_L3_AA_PROFILE))
NEUTRON_LBAAS_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                 ''.format(NEUTRON_LBAAS_AA_PROFILE))
NEUTRON_LBAASV2_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                   ''.format(NEUTRON_LBAASV2_AA_PROFILE))
NEUTRON_METADATA_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                    ''.format(NEUTRON_METADATA_AA_PROFILE))
NEUTRON_METERING_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                    ''.format(NEUTRON_METERING_AA_PROFILE))
NOVA_API_METADATA_AA_PROFILE_PATH = ('/etc/apparmor.d/{}'
                                     ''.format(NOVA_API_METADATA_AA_PROFILE))

GATEWAY_PKGS = {
    OVS: [
        "neutron-plugin-openvswitch-agent",
        "openvswitch-switch",
        "neutron-l3-agent",
        "neutron-dhcp-agent",
        'python-mysqldb',
        'python-psycopg2',
        'python-oslo.config',  # Force upgrade
        "nova-api-metadata",
        "neutron-metering-agent",
        "neutron-lbaas-agent",
    ],
    NSX: [
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
    ],
    OVS_ODL: [
        "openvswitch-switch",
        "neutron-l3-agent",
        "neutron-dhcp-agent",
        "nova-api-metadata",
        "neutron-metering-agent",
        "neutron-lbaas-agent",
    ],
}

EARLY_PACKAGES = {
    OVS: ['openvswitch-datapath-dkms'],
    NSX: [],
    N1KV: [],
    OVS_ODL: [],
}

LEGACY_HA_TEMPLATE_FILES = 'files'
LEGACY_FILES_MAP = {
    'neutron-ha-monitor.py': {
        'path': '/usr/local/bin/',
        'permissions': 0o755
    },
    'neutron-ha-monitor.conf': {
        'path': '/var/lib/juju-neutron-ha/',
    },
    'NeutronAgentMon': {
        'path': '/usr/lib/ocf/resource.d/canonical',
        'permissions': 0o755
    },
}
LEGACY_RES_MAP = ['res_monitor']
L3HA_PACKAGES = ['keepalived', 'conntrack']

# The interface is said to be satisfied if anyone of the interfaces in the
# list has a complete context.
REQUIRED_INTERFACES = {
    'messaging': ['amqp'],
    'neutron-plugin-api': ['neutron-plugin-api'],
    'network-service': ['quantum-network-service'],
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
    plugin = config('plugin')
    packages = deepcopy(GATEWAY_PKGS[plugin])
    cmp_os_source = CompareOpenStackReleases(os_release('neutron-common'))
    cmp_host_release = CompareHostReleases(lsb_release()['DISTRIB_CODENAME'])
    if plugin == OVS:
        if (cmp_os_source >= 'icehouse' and cmp_os_source < 'mitaka' and
                cmp_host_release < 'utopic'):
            # NOTE(jamespage) neutron-vpn-agent supercedes l3-agent for
            # icehouse but openswan was removed in utopic.
            packages.remove('neutron-l3-agent')
            packages.append('neutron-vpn-agent')
            packages.append('openswan')
        if cmp_os_source >= 'liberty':
            # Switch out mysql driver
            packages.remove('python-mysqldb')
            packages.append('python-pymysql')
        if cmp_os_source >= 'mitaka':
            # Switch out to actual ovs agent package
            packages.remove('neutron-plugin-openvswitch-agent')
            packages.append('neutron-openvswitch-agent')
        if cmp_os_source >= 'kilo':
            packages.append('python-neutron-fwaas')
    if plugin in (OVS, OVS_ODL):
        if cmp_os_source >= 'newton':
            # LBaaS v1 dropped in newton
            packages.remove('neutron-lbaas-agent')
            packages.append('neutron-lbaasv2-agent')
    packages.extend(determine_l3ha_packages())

    return packages


def determine_l3ha_packages():
    if use_l3ha():
        return L3HA_PACKAGES
    return []


def use_l3ha():
    return NeutronAPIContext()()['enable_l3ha']


EXT_PORT_CONF = '/etc/init/ext-port.conf'
PHY_NIC_MTU_CONF = '/etc/init/os-charm-phy-nic-mtu.conf'
STOPPED_SERVICES = ['os-charm-phy-nic-mtu', 'ext-port']

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
        'hook_contexts': [NetworkServiceContext(),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.WorkerConfigContext(),
                          context.ZeroMQContext(),
                          context.NotificationDriverContext(),
                          NovaMetadataContext()],
        'services': ['nova-api-metadata']
    },
    NOVA_API_METADATA_AA_PROFILE_PATH: {
        'services': ['nova-api-metadata'],
        'hook_contexts': [
            context.AppArmorContext(NOVA_API_METADATA_AA_PROFILE)
        ],
    },
}

NEUTRON_SHARED_CONFIG_FILES = {
    NEUTRON_DHCP_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_DNSMASQ_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-dhcp-agent']
    },
    NEUTRON_METADATA_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          context.WorkerConfigContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-metadata-agent']
    },
    NEUTRON_DHCP_AA_PROFILE_PATH: {
        'services': ['neutron-dhcp-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_DHCP_AA_PROFILE)
        ],
    },
    NEUTRON_LBAAS_AA_PROFILE_PATH: {
        'services': ['neutron-lbaas-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_LBAAS_AA_PROFILE)
        ],
    },
    NEUTRON_LBAASV2_AA_PROFILE_PATH: {
        'services': ['neutron-lbaasv2-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_LBAASV2_AA_PROFILE)
        ],
    },
    NEUTRON_METADATA_AA_PROFILE_PATH: {
        'services': ['neutron-metadata-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_METADATA_AA_PROFILE)
        ],
    },
    NEUTRON_METERING_AA_PROFILE_PATH: {
        'services': ['neutron-metering-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_METERING_AA_PROFILE)
        ],
    },
}
NEUTRON_SHARED_CONFIG_FILES.update(NOVA_CONFIG_FILES)

NEUTRON_OVS_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.ZeroMQContext(),
                          context.WorkerConfigContext(),
                          context.NotificationDriverContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-openvswitch-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_METERING_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-metering-agent',
                     'neutron-metering-agent']
    },
    NEUTRON_LBAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-lbaas-agent']
    },
    NEUTRON_VPNAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-vpn-agent']
    },
    NEUTRON_FWAAS_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_ML2_PLUGIN_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    NEUTRON_OVS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-openvswitch-agent']
    },
    NEUTRON_OVS_AA_PROFILE_PATH: {
        'services': ['neutron-plugin-openvswitch-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_OVS_AA_PROFILE)
        ],
    },
    NEUTRON_L3_AA_PROFILE_PATH: {
        'services': ['neutron-l3-agent', 'neutron-vpn-agent'],
        'hook_contexts': [
            context.AppArmorContext(NEUTRON_L3_AA_PROFILE)
        ],
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': ['ext-port']
    },
    PHY_NIC_MTU_CONF: {
        'hook_contexts': [PhyNICMTUContext()],
        'services': ['os-charm-phy-nic-mtu']
    }
}
NEUTRON_OVS_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_OVS_ODL_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          SyslogContext(),
                          context.ZeroMQContext(),
                          context.WorkerConfigContext(),
                          context.NotificationDriverContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent',
                     'neutron-plugin-metering-agent',
                     'neutron-metering-agent',
                     'neutron-lbaas-agent',
                     'neutron-vpn-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    NEUTRON_METERING_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-plugin-metering-agent',
                     'neutron-metering-agent']
    },
    NEUTRON_LBAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-lbaas-agent']
    },
    NEUTRON_VPNAAS_AGENT_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-vpn-agent']
    },
    NEUTRON_FWAAS_CONF: {
        'hook_contexts': [NeutronGatewayContext()],
        'services': ['neutron-l3-agent', 'neutron-vpn-agent']
    },
    EXT_PORT_CONF: {
        'hook_contexts': [ExternalPortContext()],
        'services': ['ext-port']
    },
    PHY_NIC_MTU_CONF: {
        'hook_contexts': [PhyNICMTUContext()],
        'services': ['os-charm-phy-nic-mtu']
    }
}
NEUTRON_OVS_ODL_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_NSX_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          context.WorkerConfigContext(),
                          SyslogContext()],
        'services': ['neutron-dhcp-agent', 'neutron-metadata-agent']
    },
}
NEUTRON_NSX_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

NEUTRON_N1KV_CONFIG_FILES = {
    NEUTRON_CONF: {
        'hook_contexts': [context.AMQPContext(ssl_dir=NEUTRON_CONF_DIR),
                          NeutronGatewayContext(),
                          context.WorkerConfigContext(),
                          SyslogContext()],
        'services': ['neutron-l3-agent',
                     'neutron-dhcp-agent',
                     'neutron-metadata-agent']
    },
    NEUTRON_L3_AGENT_CONF: {
        'hook_contexts': [NetworkServiceContext(),
                          L3AgentContext(),
                          NeutronGatewayContext()],
        'services': ['neutron-l3-agent']
    },
}
NEUTRON_N1KV_CONFIG_FILES.update(NEUTRON_SHARED_CONFIG_FILES)

CONFIG_FILES = {
    NSX: NEUTRON_NSX_CONFIG_FILES,
    OVS: NEUTRON_OVS_CONFIG_FILES,
    N1KV: NEUTRON_N1KV_CONFIG_FILES,
    OVS_ODL: NEUTRON_OVS_ODL_CONFIG_FILES
}

SERVICE_RENAMES = {
    'icehouse': {
        'neutron-plugin-metering-agent': 'neutron-metering-agent',
    },
    'mitaka': {
        'neutron-plugin-openvswitch-agent': 'neutron-openvswitch-agent',
    },
}


# Override file for systemd
SYSTEMD_NOVA_OVERRIDE = (
    '/etc/systemd/system/nova-api-metadata.service.d/override.conf'
)


def install_systemd_override():
    '''
    Install systemd override files for nova-api-metadata
    and reload systemd daemon if required.
    '''
    if init_is_systemd() and not os.path.exists(SYSTEMD_NOVA_OVERRIDE):
        mkdir(os.path.dirname(SYSTEMD_NOVA_OVERRIDE))
        shutil.copy(os.path.join('files',
                                 os.path.basename(SYSTEMD_NOVA_OVERRIDE)),
                    SYSTEMD_NOVA_OVERRIDE)
        subprocess.check_call(['systemctl', 'daemon-reload'])


def remap_service(service_name):
    '''
    Remap service names based on openstack release to deal
    with changes to packaging

    :param service_name: name of service to remap
    :returns: remapped service name or original value
    '''
    source = os_release('neutron-common')
    for rename_source in SERVICE_RENAMES:
        if (source >= rename_source and
                service_name in SERVICE_RENAMES[rename_source]):
            service_name = SERVICE_RENAMES[rename_source][service_name]
    return service_name


def resolve_config_files(plugin, release):
    '''
    Resolve configuration files and contexts

    :param plugin: shortname of plugin e.g. ovs
    :param release: openstack release codename
    :returns: dict of configuration files, contexts
              and associated services
    '''
    config_files = deepcopy(CONFIG_FILES)
    drop_config = []
    cmp_os_release = CompareOpenStackReleases(release)
    if plugin == OVS:
        # NOTE: deal with switch to ML2 plugin for >= icehouse
        drop_config = [NEUTRON_OVS_AGENT_CONF]
        if cmp_os_release >= 'mitaka':
            # ml2 -> ovs_agent
            drop_config = [NEUTRON_ML2_PLUGIN_CONF]

    # Use MAAS1.9 for MTU and external port config on xenial and above
    if CompareHostReleases(lsb_release()['DISTRIB_CODENAME']) >= 'xenial':
        drop_config.extend([EXT_PORT_CONF, PHY_NIC_MTU_CONF])

    # Rename to lbaasv2 in newton
    if cmp_os_release < 'newton':
        drop_config.extend([NEUTRON_LBAASV2_AA_PROFILE_PATH])
    else:
        drop_config.extend([NEUTRON_LBAAS_AA_PROFILE_PATH])

    for _config in drop_config:
        if _config in config_files[plugin]:
            config_files[plugin].pop(_config)

    if is_relation_made('amqp-nova'):
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp-nova',
            relation_prefix='nova')
    else:
        amqp_nova_ctxt = context.AMQPContext(
            ssl_dir=NOVA_CONF_DIR,
            rel_name='amqp')
    config_files[plugin][NOVA_CONF][
        'hook_contexts'].append(amqp_nova_ctxt)
    return config_files


def register_configs(release=None):
    '''
    Register config files with their respective contexts.

    :param release: string containing the openstack release to use
                    over automatic detection based on installed pkgs.
    '''
    release = release or os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    configs = templating.OSConfigRenderer(templates_dir=TEMPLATES,
                                          openstack_release=release)
    for conf in config_files[plugin]:
        configs.register(conf,
                         config_files[plugin][conf]['hook_contexts'])
    return configs


def stop_services():
    release = os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    svcs = set()
    for ctxt in config_files[config('plugin')].values():
        for svc in ctxt['services']:
            svcs.add(remap_service(svc))
    for svc in svcs:
        service_stop(svc)


def restart_map(release=None):
    '''
    Determine the correct resource map to be passed to
    charmhelpers.core.restart_on_change() based on the services configured.

    :param release: string containing the openstack release to use
                    over automatic detection based on installed pkgs.
    :returns: dict: A dictionary mapping config file to lists of services
                    that should be restarted when file changes.
    '''
    release = release or os_release('neutron-common')
    plugin = config('plugin')
    config_files = resolve_config_files(plugin, release)
    _map = {}
    enable_vpn_agent = 'neutron-vpn-agent' in get_packages()
    for f, ctxt in config_files[plugin].items():
        svcs = set()
        for svc in ctxt['services']:
            svcs.add(remap_service(svc))
        if not enable_vpn_agent and 'neutron-vpn-agent' in svcs:
            svcs.remove('neutron-vpn-agent')
        if 'neutron-vpn-agent' in svcs and 'neutron-l3-agent' in svcs:
            svcs.remove('neutron-l3-agent')
        if (CompareOpenStackReleases(release) >= 'newton' and
                'neutron-lbaas-agent' in svcs):
            svcs.remove('neutron-lbaas-agent')
            svcs.add('neutron-lbaasv2-agent')
        if svcs:
            _map[f] = list(svcs)
    return _map


INT_BRIDGE = "br-int"
EXT_BRIDGE = "br-ex"


def services():
    ''' Returns a list of services associate with this charm '''
    _services = []
    for v in restart_map().values():
        _services = _services + v
    return list(set(_services))


def do_openstack_upgrade(configs):
    """
    Perform an upgrade.  Takes care of upgrading packages, rewriting
    configs, database migrations and potentially any other post-upgrade
    actions.
    """
    new_src = config('openstack-origin')
    new_os_rel = get_os_codename_install_source(new_src)
    log('Performing OpenStack upgrade to %s.' % (new_os_rel))

    configure_installation_source(new_src)

    # NOTE(jamespage):
    # Write-out new openstack release configuration files prior to upgrading
    # to avoid having to restart services immediately after upgrade.
    configs = register_configs(new_os_rel)
    configs.write_all()

    dpkg_opts = [
        '--option', 'Dpkg::Options::=--force-confnew',
        '--option', 'Dpkg::Options::=--force-confdef',
    ]
    apt_update(fatal=True)
    apt_upgrade(options=dpkg_opts,
                fatal=True, dist=True)
    # The cached version of os_release will now be invalid as the pkg version
    # should have changed during the upgrade.
    reset_os_release()
    apt_install(get_early_packages(), fatal=True)
    apt_install(get_packages(), fatal=True)


def configure_ovs():
    if config('plugin') in [OVS, OVS_ODL]:
        if not service_running('openvswitch-switch'):
            full_restart()
        add_bridge(INT_BRIDGE)
        add_bridge(EXT_BRIDGE)
        ext_port_ctx = ExternalPortContext()()
        if ext_port_ctx and ext_port_ctx['ext_port']:
            add_bridge_port(EXT_BRIDGE, ext_port_ctx['ext_port'])

        portmaps = DataPortContext()()
        bridgemaps = parse_bridge_mappings(config('bridge-mappings'))
        for br in bridgemaps.values():
            add_bridge(br)
            if not portmaps:
                continue

            for port, _br in portmaps.items():
                if _br == br:
                    if not is_linuxbridge_interface(port):
                        add_bridge_port(br, port, promisc=True)
                    else:
                        add_ovsbridge_linuxbridge(br, port)

        target = config('ipfix-target')
        bridges = [INT_BRIDGE, EXT_BRIDGE]
        bridges.extend(bridgemaps.values())

        if target:
            for bridge in bridges:
                disable_ipfix(bridge)
                enable_ipfix(bridge, target)
        else:
            # NOTE: removing ipfix setting from a bridge is idempotent and
            #       will pass regardless of the existence of the setting
            for bridge in bridges:
                disable_ipfix(bridge)

        # Ensure this runs so that mtu is applied to data-port interfaces if
        # provided.
        service_restart('os-charm-phy-nic-mtu')


def copy_file(src, dst, perms=None, force=False):
    """Copy file to destination and optionally set permissionss.

    If destination does not exist it will be created.
    """
    if not os.path.isdir(dst):
        log('Creating directory %s' % dst, level=DEBUG)
        mkdir(dst)

    fdst = os.path.join(dst, os.path.basename(src))
    if not os.path.isfile(fdst) or force:
        try:
            copy2(src, fdst)
            if perms:
                os.chmod(fdst, perms)
        except IOError:
            log('Failed to copy file from %s to %s.' % (src, dst), level=ERROR)
            raise


def remove_file(path):
    if not os.path.isfile(path):
        log('File %s does not exist.' % path, level=INFO)
        return

    try:
        os.remove(path)
    except IOError:
        log('Failed to remove file %s.' % path, level=ERROR)


def install_legacy_ha_files(force=False):
    for f, p in LEGACY_FILES_MAP.items():
        srcfile = os.path.join(LEGACY_HA_TEMPLATE_FILES, f)
        copy_file(srcfile, p['path'], p.get('permissions', None), force=force)


def remove_legacy_ha_files():
    for f, p in LEGACY_FILES_MAP.items():
        remove_file(os.path.join(p['path'], f))


def update_legacy_ha_files(force=False):
    if config('ha-legacy-mode'):
        install_legacy_ha_files(force=force)
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


def stop_neutron_ha_monitor_daemon():
    try:
        cmd = ['pgrep', '-f', 'neutron-ha-monitor.py']
        res = subprocess.check_output(cmd).decode('UTF-8')
        pid = res.strip()
        if pid:
            subprocess.call(['sudo', 'kill', '-9', pid])
    except subprocess.CalledProcessError as e:
        log('Faild to kill neutron-ha-monitor daemon, %s' % e, level=ERROR)


def cleanup_ovs_netns():
    try:
        subprocess.call('neutron-ovs-cleanup')
        subprocess.call('neutron-netns-cleanup')
    except subprocess.CalledProcessError as e:
        log('Faild to cleanup ovs and netns, %s' % e, level=ERROR)


def get_optional_interfaces():
    """Return the optional interfaces that should be checked if the relavent
    relations have appeared.
    :returns: {general_interface: [specific_int1, specific_int2, ...], ...}
    """
    optional_interfaces = {}
    if relation_ids('ha'):
        optional_interfaces['ha'] = ['cluster']
    return optional_interfaces


def check_optional_relations(configs):
    """Check that if we have a relation_id for high availability that we can
    get the hacluster config.  If we can't then we are blocked.

    This function is called from assess_status/set_os_workload_status as the
    charm_func and needs to return either "unknown", "" if there is no problem
    or the status, message if there is a problem.

    :param configs: an OSConfigRender() instance.
    :return 2-tuple: (string, string) = (status, message)
    """
    if relation_ids('ha'):
        try:
            get_hacluster_config()
        except:
            return ('blocked',
                    'hacluster missing configuration: '
                    'vip, vip_iface, vip_cidr')

    # return 'unknown' as the lowest priority to not clobber an existing
    # status.
    return 'unknown', ''


def assess_status(configs):
    """Assess status of current unit
    Decides what the state of the unit should be based on the current
    configuration.
    SIDE EFFECT: calls set_os_workload_status(...) which sets the workload
    status of the unit.
    Also calls status_set(...) directly if paused state isn't complete.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    assess_status_func(configs)()
    os_application_version_set(VERSION_PACKAGE)


def assess_status_func(configs):
    """Helper function to create the function that will assess_status() for
    the unit.
    Uses charmhelpers.contrib.openstack.utils.make_assess_status_func() to
    create the appropriate status function and then returns it.
    Used directly by assess_status() and also for pausing and resuming
    the unit.

    NOTE: REQUIRED_INTERFACES is augmented with the optional interfaces
    depending on the current config before being passed to the
    make_assess_status_func() function.

    NOTE(ajkavanagh) ports are not checked due to race hazards with services
    that don't behave sychronously w.r.t their service scripts.  e.g.
    apache2.
    @param configs: a templating.OSConfigRenderer() object
    @return f() -> None : a function that assesses the unit's workload status
    """
    required_interfaces = REQUIRED_INTERFACES.copy()
    required_interfaces.update(get_optional_interfaces())
    active_services = [s for s in services() if s not in STOPPED_SERVICES]
    return make_assess_status_func(
        configs, required_interfaces,
        charm_func=check_optional_relations,
        services=active_services, ports=None)


def pause_unit_helper(configs):
    """Helper function to pause a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.pause_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(pause_unit, configs)


def resume_unit_helper(configs):
    """Helper function to resume a unit, and then call assess_status(...) in
    effect, so that the status is correctly updated.
    Uses charmhelpers.contrib.openstack.utils.resume_unit() to do the work.
    @param configs: a templating.OSConfigRenderer() object
    @returns None - this function is executed for its side-effect
    """
    _pause_resume_helper(resume_unit, configs)


def _pause_resume_helper(f, configs):
    """Helper function that uses the make_assess_status_func(...) from
    charmhelpers.contrib.openstack.utils to create an assess_status(...)
    function that can be used with the pause/resume of the unit
    @param f: the function to be used with the assess_status(...) function
    @returns None - this function is executed for its side-effect
    """
    active_services = [s for s in services() if s not in STOPPED_SERVICES]
    # TODO(ajkavanagh) - ports= has been left off because of the race hazard
    # that exists due to service_start()
    f(assess_status_func(configs),
      services=active_services,
      ports=None)


def configure_apparmor():
    '''Configure all apparmor profiles for the local unit'''
    profiles = deepcopy(APPARMOR_PROFILES)
    cmp_os_source = CompareOpenStackReleases(os_release('neutron-common'))
    if cmp_os_source >= 'newton':
        profiles.remove(NEUTRON_LBAAS_AA_PROFILE)
        profiles.append(NEUTRON_LBAASV2_AA_PROFILE)
    for profile in profiles:
        context.AppArmorContext(profile).setup_aa_profile()


VENDORDATA_FILE = '/etc/nova/vendor_data.json'


def write_vendordata(vdata):
    try:
        json_vdata = json.loads(vdata)
    except (TypeError, json.decoder.JSONDecodeError) as e:
        log('Error decoding vendor-data. {}'.format(e), level=ERROR)
        return False
    with open(VENDORDATA_FILE, 'w') as vdata_file:
        vdata_file.write(json.dumps(json_vdata, sort_keys=True, indent=2))
    return True
