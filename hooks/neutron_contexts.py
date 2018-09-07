# vim: set ts=4:et
import os
import uuid
from charmhelpers.core.hookenv import (
    log, ERROR,
    config,
    unit_get,
    network_get_primary_address,
)
from charmhelpers.contrib.openstack.context import (
    OSContextGenerator,
    NeutronAPIContext,
    config_flags_parser,
)
from charmhelpers.contrib.openstack.utils import (
    os_release,
    CompareOpenStackReleases,
)
from charmhelpers.contrib.hahelpers.cluster import (
    eligible_leader
)
from charmhelpers.contrib.network.ip import (
    get_address_in_network,
    get_host_ip,
)

NEUTRON_ML2_PLUGIN = "ml2"
NEUTRON_N1KV_PLUGIN = \
    "neutron.plugins.cisco.n1kv.n1kv_neutron_plugin.N1kvNeutronPluginV2"
NEUTRON_NSX_PLUGIN = "vmware"
NEUTRON_OVS_ODL_PLUGIN = "ml2"

OVS = 'ovs'
N1KV = 'n1kv'
NSX = 'nsx'
OVS_ODL = 'ovs-odl'

NEUTRON = 'neutron'

CORE_PLUGIN = {
    OVS: NEUTRON_ML2_PLUGIN,
    N1KV: NEUTRON_N1KV_PLUGIN,
    NSX: NEUTRON_NSX_PLUGIN,
    OVS_ODL: NEUTRON_OVS_ODL_PLUGIN,
}


def core_plugin():
    return CORE_PLUGIN[config('plugin')]


class L3AgentContext(OSContextGenerator):

    def __call__(self):
        api_settings = NeutronAPIContext()()
        ctxt = {}
        if config('run-internal-router') == 'leader':
            ctxt['handle_internal_only_router'] = eligible_leader(None)

        if config('run-internal-router') == 'all':
            ctxt['handle_internal_only_router'] = True

        if config('run-internal-router') == 'none':
            ctxt['handle_internal_only_router'] = False

        if config('external-network-id'):
            ctxt['ext_net_id'] = config('external-network-id')

        if not config('ext-port') and not config('external-network-id'):
            ctxt['external_configuration_new'] = True

        if config('plugin'):
            ctxt['plugin'] = config('plugin')
        if api_settings['enable_dvr']:
            ctxt['agent_mode'] = 'dvr_snat'
        else:
            ctxt['agent_mode'] = 'legacy'
        ctxt['rpc_response_timeout'] = api_settings['rpc_response_timeout']
        ctxt['report_interval'] = api_settings['report_interval']
        return ctxt


class NeutronGatewayContext(NeutronAPIContext):

    def __call__(self):
        api_settings = super(NeutronGatewayContext, self).__call__()
        ctxt = {
            'shared_secret': get_shared_secret(),
            'core_plugin': core_plugin(),
            'plugin': config('plugin'),
            'debug': config('debug'),
            'verbose': config('verbose'),
            'instance_mtu': config('instance-mtu'),
            'dns_servers': config('dns-servers'),
            'l2_population': api_settings['l2_population'],
            'enable_dvr': api_settings['enable_dvr'],
            'enable_l3ha': api_settings['enable_l3ha'],
            'extension_drivers': api_settings['extension_drivers'],
            'dns_domain': api_settings['dns_domain'],
            'overlay_network_type':
            api_settings['overlay_network_type'],
            'rpc_response_timeout': api_settings['rpc_response_timeout'],
            'report_interval': api_settings['report_interval'],
            'enable_metadata_network': config('enable-metadata-network'),
            'enable_isolated_metadata': config('enable-isolated-metadata'),
        }

        fallback = get_host_ip(unit_get('private-address'))
        if config('os-data-network'):
            # NOTE: prefer any existing use of config based networking
            ctxt['local_ip'] = \
                get_address_in_network(config('os-data-network'),
                                       fallback)
        else:
            # NOTE: test out network-spaces support, then fallback
            try:
                ctxt['local_ip'] = get_host_ip(
                    network_get_primary_address('data')
                )
            except NotImplementedError:
                ctxt['local_ip'] = fallback

        mappings = config('bridge-mappings')
        if mappings:
            ctxt['bridge_mappings'] = ','.join(mappings.split())

        flat_providers = config('flat-network-providers')
        if flat_providers:
            ctxt['network_providers'] = ','.join(flat_providers.split())

        vlan_ranges = config('vlan-ranges')
        if vlan_ranges:
            ctxt['vlan_ranges'] = ','.join(vlan_ranges.split())

        dnsmasq_flags = config('dnsmasq-flags')
        if dnsmasq_flags:
            ctxt['dnsmasq_flags'] = config_flags_parser(dnsmasq_flags)

        net_dev_mtu = api_settings['network_device_mtu']
        if net_dev_mtu:
            ctxt['network_device_mtu'] = net_dev_mtu
            ctxt['veth_mtu'] = net_dev_mtu

        # Override user supplied config for these plugins as these settings are
        # mandatory
        if ctxt['plugin'] in ['nvp', 'nsx', 'n1kv']:
            ctxt['enable_metadata_network'] = True
            ctxt['enable_isolated_metadata'] = True

        return ctxt


class NovaMetadataContext(OSContextGenerator):

    def __call__(self):
        ctxt = {}
        ctxt['vendordata_providers'] = []
        vdata = config('vendor-data')
        vdata_url = config('vendor-data-url')
        cmp_os_release = CompareOpenStackReleases(os_release('neutron-common'))

        if vdata:
            ctxt['vendor_data'] = True
            ctxt['vendordata_providers'].append('StaticJSON')

        if vdata_url:
            if cmp_os_release > 'mitaka':
                ctxt['vendor_data_url'] = vdata_url
                ctxt['vendordata_providers'].append('DynamicJSON')
            else:
                log('Dynamic vendor data unsupported'
                    ' for {}.'.format(cmp_os_release), level=ERROR)

        return ctxt


SHARED_SECRET = "/etc/{}/secret.txt"


def get_shared_secret():
    secret = None
    _path = SHARED_SECRET.format(NEUTRON)
    if not os.path.exists(_path):
        secret = str(uuid.uuid4())
        with open(_path, 'w') as secret_file:
            secret_file.write(secret)
    else:
        with open(_path, 'r') as secret_file:
            secret = secret_file.read().strip()
    return secret
