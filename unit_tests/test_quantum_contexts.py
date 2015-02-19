from mock import (
    Mock,
    MagicMock,
    patch
)
import quantum_contexts
import sys
from contextlib import contextmanager

from test_utils import (
    CharmTestCase
)

TO_PATCH = [
    'apt_install',
    'config',
    'context_complete',
    'eligible_leader',
    'get_ipv4_addr',
    'get_ipv6_addr',
    'get_nic_hwaddr',
    'get_os_codename_install_source',
    'list_nics',
    'relation_get',
    'relation_ids',
    'related_units',
    'unit_get',
]


@contextmanager
def patch_open():
    '''Patch open() to allow mocking both open() itself and the file that is
    yielded.

    Yields the mock for "open" and "file", respectively.'''
    mock_open = MagicMock(spec=open)
    mock_file = MagicMock(spec=file)

    @contextmanager
    def stub_open(*args, **kwargs):
        mock_open(*args, **kwargs)
        yield mock_file

    with patch('__builtin__.open', stub_open):
        yield mock_open, mock_file


class _TestQuantumContext(CharmTestCase):

    def setUp(self):
        super(_TestQuantumContext, self).setUp(quantum_contexts, TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_not_related(self):
        self.relation_ids.return_value = []
        self.assertEquals(self.context(), {})

    def test_no_units(self):
        self.relation_ids.return_value = []
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = []
        self.assertEquals(self.context(), {})

    def test_no_data(self):
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = ['bar']
        self.relation_get.side_effect = self.test_relation.get
        self.context_complete.return_value = False
        self.assertEquals(self.context(), {})

    def test_data_multi_unit(self):
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = ['bar', 'baz']
        self.context_complete.return_value = True
        self.relation_get.side_effect = self.test_relation.get
        self.assertEquals(self.context(), self.data_result)

    def test_data_single_unit(self):
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = ['bar']
        self.context_complete.return_value = True
        self.relation_get.side_effect = self.test_relation.get
        self.assertEquals(self.context(), self.data_result)


class TestNetworkServiceContext(_TestQuantumContext):

    def setUp(self):
        super(TestNetworkServiceContext, self).setUp()
        self.context = quantum_contexts.NetworkServiceContext()
        self.test_relation.set(
            {'keystone_host': '10.5.0.1',
             'service_port': '5000',
             'auth_port': '20000',
             'service_tenant': 'tenant',
             'service_username': 'username',
             'service_password': 'password',
             'quantum_host': '10.5.0.2',
             'quantum_port': '9696',
             'quantum_url': 'http://10.5.0.2:9696/v2',
             'region': 'aregion'}
        )
        self.data_result = {
            'keystone_host': '10.5.0.1',
            'service_port': '5000',
            'auth_port': '20000',
            'service_tenant': 'tenant',
            'service_username': 'username',
            'service_password': 'password',
            'quantum_host': '10.5.0.2',
            'quantum_port': '9696',
            'quantum_url': 'http://10.5.0.2:9696/v2',
            'region': 'aregion',
            'service_protocol': 'http',
            'auth_protocol': 'http',
        }


class TestNeutronPortContext(CharmTestCase):

    def setUp(self):
        super(TestNeutronPortContext, self).setUp(quantum_contexts,
                                                  TO_PATCH)
        self.machine_macs = {
            'eth0': 'fe:c5:ce:8e:2b:00',
            'eth1': 'fe:c5:ce:8e:2b:01',
            'eth2': 'fe:c5:ce:8e:2b:02',
            'eth3': 'fe:c5:ce:8e:2b:03',
        }
        self.machine_nics = {
            'eth0': ['192.168.0.1'],
            'eth1': ['192.168.0.2'],
            'eth2': [],
            'eth3': [],
        }
        self.absent_macs = "aa:a5:ae:ae:ab:a4 "

    def test_no_ext_port(self):
        self.config.return_value = None
        self.assertIsNone(quantum_contexts.ExternalPortContext()())

    def test_ext_port_eth(self):
        self.config.return_value = 'eth1010'
        self.assertEquals(quantum_contexts.ExternalPortContext()(),
                          {'ext_port': 'eth1010'})

    def _fake_get_hwaddr(self, arg):
        return self.machine_macs[arg]

    def _fake_get_ipv4(self, arg, fatal=False):
        return self.machine_nics[arg]

    def test_ext_port_mac(self):
        config_macs = self.absent_macs + " " + self.machine_macs['eth2']
        self.get_ipv4_addr.side_effect = self._fake_get_ipv4
        self.get_ipv6_addr.return_value = []
        self.config.return_value = config_macs
        self.list_nics.return_value = self.machine_macs.keys()
        self.get_nic_hwaddr.side_effect = self._fake_get_hwaddr
        self.assertEquals(quantum_contexts.ExternalPortContext()(),
                          {'ext_port': 'eth2'})
        self.config.return_value = self.absent_macs
        self.assertIsNone(quantum_contexts.ExternalPortContext()())

    def test_ext_port_mac_one_used_nic(self):
        config_macs = self.machine_macs['eth1'] + " " + \
            self.machine_macs['eth2']
        self.get_ipv4_addr.side_effect = self._fake_get_ipv4
        self.get_ipv6_addr.return_value = []
        self.config.return_value = config_macs
        self.list_nics.return_value = self.machine_macs.keys()
        self.get_nic_hwaddr.side_effect = self._fake_get_hwaddr
        self.assertEquals(quantum_contexts.ExternalPortContext()(),
                          {'ext_port': 'eth2'})

    def test_data_port_eth(self):
        self.config.return_value = 'eth1010'
        self.assertEquals(quantum_contexts.DataPortContext()(),
                          {'data_port': 'eth1010'})


class TestL3AgentContext(CharmTestCase):

    def setUp(self):
        super(TestL3AgentContext, self).setUp(quantum_contexts,
                                              TO_PATCH)
        self.config.side_effect = self.test_config.get

    def test_no_ext_netid(self):
        self.test_config.set('run-internal-router', 'none')
        self.test_config.set('external-network-id', '')
        self.eligible_leader.return_value = False
        self.assertEquals(quantum_contexts.L3AgentContext()(),
                          {'agent_mode': 'legacy',
                           'handle_internal_only_router': False,
                           'plugin': 'ovs'})

    def test_hior_leader(self):
        self.test_config.set('run-internal-router', 'leader')
        self.test_config.set('external-network-id', 'netid')
        self.eligible_leader.return_value = True
        self.assertEquals(quantum_contexts.L3AgentContext()(),
                          {'agent_mode': 'legacy',
                           'handle_internal_only_router': True,
                           'ext_net_id': 'netid',
                           'plugin': 'ovs'})

    def test_hior_all(self):
        self.test_config.set('run-internal-router', 'all')
        self.test_config.set('external-network-id', 'netid')
        self.eligible_leader.return_value = True
        self.assertEquals(quantum_contexts.L3AgentContext()(),
                          {'agent_mode': 'legacy',
                           'handle_internal_only_router': True,
                           'ext_net_id': 'netid',
                           'plugin': 'ovs'})


class TestQuantumGatewayContext(CharmTestCase):

    def setUp(self):
        super(TestQuantumGatewayContext, self).setUp(quantum_contexts,
                                                     TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch.object(quantum_contexts, 'get_shared_secret')
    @patch.object(quantum_contexts, 'get_host_ip')
    def test_all(self, _host_ip, _secret):
        self.test_config.set('plugin', 'ovs')
        self.test_config.set('debug', False)
        self.test_config.set('verbose', True)
        self.test_config.set('instance-mtu', 1420)
        self.get_os_codename_install_source.return_value = 'folsom'
        _host_ip.return_value = '10.5.0.1'
        _secret.return_value = 'testsecret'
        self.assertEquals(quantum_contexts.QuantumGatewayContext()(), {
            'shared_secret': 'testsecret',
            'enable_dvr': False,
            'local_ip': '10.5.0.1',
            'instance_mtu': 1420,
            'core_plugin': "quantum.plugins.openvswitch.ovs_quantum_plugin."
                           "OVSQuantumPluginV2",
            'plugin': 'ovs',
            'debug': False,
            'verbose': True,
            'l2_population': False,
            'overlay_network_type': 'gre',
        })


class TestSharedSecret(CharmTestCase):

    def setUp(self):
        super(TestSharedSecret, self).setUp(quantum_contexts,
                                            TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch('os.path')
    @patch('uuid.uuid4')
    def test_secret_created_stored(self, _uuid4, _path):
        _path.exists.return_value = False
        _uuid4.return_value = 'secret_thing'
        with patch_open() as (_open, _file):
            self.assertEquals(quantum_contexts.get_shared_secret(),
                              'secret_thing')
            _open.assert_called_with(
                quantum_contexts.SHARED_SECRET.format('quantum'), 'w')
            _file.write.assert_called_with('secret_thing')

    @patch('os.path')
    def test_secret_retrieved(self, _path):
        _path.exists.return_value = True
        with patch_open() as (_open, _file):
            _file.read.return_value = 'secret_thing\n'
            self.assertEquals(quantum_contexts.get_shared_secret(),
                              'secret_thing')
            _open.assert_called_with(
                quantum_contexts.SHARED_SECRET.format('quantum'), 'r')


class TestHostIP(CharmTestCase):

    def setUp(self):
        super(TestHostIP, self).setUp(quantum_contexts,
                                      TO_PATCH)
        self.config.side_effect = self.test_config.get
        # Save and inject
        self.mods = {'dns': None, 'dns.resolver': None}
        for mod in self.mods:
            if mod not in sys.modules:
                sys.modules[mod] = Mock()
            else:
                del self.mods[mod]

    def tearDown(self):
        super(TestHostIP, self).tearDown()
        # Cleanup
        for mod in self.mods.keys():
            del sys.modules[mod]

    def test_get_host_ip_already_ip(self):
        self.assertEquals(quantum_contexts.get_host_ip('10.5.0.1'),
                          '10.5.0.1')

    def test_get_host_ip_noarg(self):
        self.unit_get.return_value = "10.5.0.1"
        self.assertEquals(quantum_contexts.get_host_ip(),
                          '10.5.0.1')

    @patch('dns.resolver.query')
    def test_get_host_ip_hostname_unresolvable(self, _query):
        class NXDOMAIN(Exception):
            pass
        _query.side_effect = NXDOMAIN()
        self.assertRaises(NXDOMAIN, quantum_contexts.get_host_ip,
                          'missing.example.com')

    @patch('dns.resolver.query')
    def test_get_host_ip_hostname_resolvable(self, _query):
        data = MagicMock()
        data.address = '10.5.0.1'
        _query.return_value = [data]
        self.assertEquals(quantum_contexts.get_host_ip('myhost.example.com'),
                          '10.5.0.1')
        _query.assert_called_with('myhost.example.com', 'A')


class TestMisc(CharmTestCase):

    def setUp(self):
        super(TestMisc,
              self).setUp(quantum_contexts,
                          TO_PATCH)

    def test_lt_havana(self):
        self.get_os_codename_install_source.return_value = 'folsom'
        self.assertEquals(quantum_contexts.networking_name(), 'quantum')

    def test_ge_havana(self):
        self.get_os_codename_install_source.return_value = 'havana'
        self.assertEquals(quantum_contexts.networking_name(), 'neutron')

    def test_remap_plugin(self):
        self.get_os_codename_install_source.return_value = 'havana'
        self.assertEquals(quantum_contexts.remap_plugin('nvp'), 'nvp')
        self.assertEquals(quantum_contexts.remap_plugin('nsx'), 'nvp')

    def test_remap_plugin_icehouse(self):
        self.get_os_codename_install_source.return_value = 'icehouse'
        self.assertEquals(quantum_contexts.remap_plugin('nvp'), 'nsx')
        self.assertEquals(quantum_contexts.remap_plugin('nsx'), 'nsx')

    def test_remap_plugin_noop(self):
        self.get_os_codename_install_source.return_value = 'icehouse'
        self.assertEquals(quantum_contexts.remap_plugin('ovs'), 'ovs')

    def test_core_plugin(self):
        self.get_os_codename_install_source.return_value = 'havana'
        self.config.return_value = 'ovs'
        self.assertEquals(quantum_contexts.core_plugin(),
                          quantum_contexts.NEUTRON_OVS_PLUGIN)

    def test_core_plugin_ml2(self):
        self.get_os_codename_install_source.return_value = 'icehouse'
        self.config.return_value = 'ovs'
        self.assertEquals(quantum_contexts.core_plugin(),
                          quantum_contexts.NEUTRON_ML2_PLUGIN)

    def test_neutron_api_settings(self):
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = ['bar']
        self.test_relation.set({'l2-population': True,
                                'overlay-network-type': 'gre', })
        self.relation_get.side_effect = self.test_relation.get
        self.assertEquals(quantum_contexts._neutron_api_settings(),
                          {'enable_dvr': False,
                           'l2_population': True,
                           'overlay_network_type': 'gre'})

    def test_neutron_api_settings2(self):
        self.relation_ids.return_value = ['foo']
        self.related_units.return_value = ['bar']
        self.test_relation.set({'l2-population': True,
                                'overlay-network-type': 'gre', })
        self.relation_get.side_effect = self.test_relation.get
        self.assertEquals(quantum_contexts._neutron_api_settings(),
                          {'enable_dvr': False,
                           'l2_population': True,
                           'overlay_network_type': 'gre'})

    def test_neutron_api_settings_no_apiplugin(self):
        self.relation_ids.return_value = []
        self.assertEquals(quantum_contexts._neutron_api_settings(),
                          {'enable_dvr': False,
                           'l2_population': False,
                           'overlay_network_type': 'gre', })
