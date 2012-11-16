import subprocess
import shutil
from utils import juju_log as log, install, start, stop

try:
    from quantumclient.v2_0 import client
except ImportError:
    install('python-quantumclient')
    from quantumclient.v2_0 import client

OVS = "ovs"
NVP = "nvp"

OVS_PLUGIN = \
    "quantum.plugins.openvswitch.ovs_quantum_plugin.OVSQuantumPluginV2"
NVP_PLUGIN = \
    "quantum.plugins.nicira.nicira_nvp_plugin.QuantumPlugin.NvpPluginV2"
CORE_PLUGIN = {
    OVS: OVS_PLUGIN,
    NVP: NVP_PLUGIN
    }

OVS_PLUGIN_CONF = \
    "/etc/quantum/plugins/openvswitch/ovs_quantum_plugin.ini"
NVP_PLUGIN_CONF = \
    "/etc/quantum/plugins/nicira/nvp.ini"
PLUGIN_CONF = {
    OVS: OVS_PLUGIN_CONF,
    NVP: NVP_PLUGIN_CONF
    }

GATEWAY_PKGS = {
    OVS: [
        "quantum-plugin-openvswitch",
        "quantum-plugin-openvswitch-agent",
        "quantum-l3-agent",
        "quantum-dhcp-agent",
        'quantum-server',
        'python-mysqldb'
        ],
    NVP: [
        "quantum-plugin-nicira"
        ]
    }

PLUGIN_PKGS = {
    OVS: [
        "quantum-plugin-openvswitch-agent"
        ]
    }

PLUGIN_AGENT = {
    OVS: [
        "quantum-plugin-openvswitch-agent"
        ]
    }

GATEWAY_AGENTS = {
    OVS: [
        "quantum-plugin-openvswitch-agent",
        "quantum-server",
        "quantum-l3-agent",
        "quantum-dhcp-agent"
        ]
    }

DB_USER = "quantum"
QUANTUM_DB = "quantum"
KEYSTONE_SERVICE = "quantum"

QUANTUM_CONF = "/etc/quantum/quantum.conf"
L3_AGENT_CONF = "/etc/quantum/l3_agent.ini"
QUANTUM_API_CONF = "/etc/quantum/api-paste.ini"
DHCP_AGENT_CONF = "/etc/quantum/dhcp_agent.ini"

RABBIT_USER = "nova"
RABBIT_VHOST = "nova"


def add_bridge(name):
    status = subprocess.check_output(["ovs-vsctl", "show"])
    if "Bridge {}".format(name) not in status:
        log('INFO', 'Creating bridge {}'.format(name))
        subprocess.check_call(["ovs-vsctl", "add-br", name])


def del_bridge(name):
    status = subprocess.check_output(["ovs-vsctl", "show"])
    if "Bridge {}".format(name) in status:
        log('INFO', 'Deleting bridge {}'.format(name))
        subprocess.check_call(["ovs-vsctl", "del-br", name])


def add_bridge_port(name, port):
    status = subprocess.check_output(["ovs-vsctl", "show"])
    if ("Bridge {}".format(name) in status and
        "Interface \"{}\"".format(port) not in status):
        log('INFO',
            'Adding port {} to bridge {}'.format(port, name))
        subprocess.check_call(["ovs-vsctl", "add-port", name, port])
        subprocess.check_call(["ip", "link", "set", port, "up"])


def del_bridge_port(name, port):
    status = subprocess.check_output(["ovs-vsctl", "show"])
    if ("Bridge {}".format(name) in status and
        "Interface \"{}\"".format(port) in status):
        log('INFO',
            'Deleting port {} from bridge {}'.format(port, name))
        subprocess.check_call(["ovs-vsctl", "del-port", name, port])
        subprocess.check_call(["ip", "link", "set", port, "down"])


QEMU_CONF = '/etc/libvirt/qemu.conf'


def configure_libvirt():
    log('INFO',
        'Configuring default permissions in libvirt-bin')
    shutil.copyfile('files/qemu.conf',
                    QEMU_CONF)
    stop('libvirt-bin')
    start('libvirt-bin')


EXT_BRIDGE = 'br-ex'
INT_BRIDGE = 'br-int'


def configure_ext_net(username,
                      password,
                      tenant,
                      url,
                      ext_net_name,
                      gateway_ip,
                      default_gateway,
                      cidr,
                      start_floating_ip,
                      end_floating_ip):

    ext_net_len = cidr.split('/')[1]
    quantum = client.Client(username=username,
                            password=password,
                            tenant_name=tenant,
                            auth_url=url)

    networks = quantum.list_networks(name=ext_net_name)
    if len(networks['networks']) == 0:
        log('INFO',
            'Configuring external bridge')
        network_msg = {
            'network': {
                'name': ext_net_name,
                'router:external': True
            }
        }
        log('INFO',
            'Creating new external network definition: {}'
                .format(ext_net_name))
        network = quantum.create_network(network_msg)
        log('INFO',
            'New external network created: {}'
                .format(network['network']['id']))

        subnet_msg = {
            'subnet': {
                'name': '{}_subnet'.format(ext_net_name),
                'network_id': network['network']['id'],
                'enable_dhcp': False,
                'gateway_ip': default_gateway,
                'cidr': cidr,
                'ip_version': 4,
                'allocation_pools': [
                        {
                        'start': start_floating_ip,
                        'end': end_floating_ip
                        }
                 ]
            }
        }
        log('INFO',
            'Creating new subnet for {}'.format(ext_net_name))
        subnet = quantum.create_subnet(subnet_msg)
        log('INFO',
            'New subnet created: {}'.format(subnet['subnet']['id']))

        log('INFO',
            'Creating provider router for external network access')
        router = quantum.create_router({'router': {'name': 'provider-router'}})
        log('INFO',
            'New router created: {}'.format(router['router']['id']))

        log('INFO',
            'Plugging router into ext_net')
        router = \
            quantum.add_gateway_router(
                                router=router['router']['id'],
                                body={'network_id': network['network']['id']}
                                )
        log('INFO',
            'Router connected to ext_net')

    log('INFO',
        'Configuring external bridge connectivity')
    subprocess.check_call(['ip', 'addr', 'flush',
                           'dev', EXT_BRIDGE])
    subprocess.check_call(['ip', 'addr', 'add',
                           '{}/{}'.format(gateway_ip, ext_net_len),
                           'dev', EXT_BRIDGE])
    subprocess.check_call(['ip', 'link', 'set',
                           EXT_BRIDGE, 'up'])