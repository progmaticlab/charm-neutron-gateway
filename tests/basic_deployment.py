#!/usr/bin/python

import amulet
try:
    from quantumclient.v2_0 import client as neutronclient
except ImportError:
    from neutronclient.v2_0 import client as neutronclient

from charmhelpers.contrib.openstack.amulet.deployment import (
    OpenStackAmuletDeployment
)

from charmhelpers.contrib.openstack.amulet.utils import (
    OpenStackAmuletUtils,
    DEBUG, # flake8: noqa
    ERROR
)

# Use DEBUG to turn on debug logging
u = OpenStackAmuletUtils(ERROR)


class QuantumGatewayBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic quantum-gateway deployment."""

    def __init__(self, series, openstack=None, source=None):
        """Deploy the entire test environment."""
        super(QuantumGatewayBasicDeployment, self).__init__(series, openstack,
                                                            source)
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add the service that we're testing, including the number of units,
           where quantum-gateway is local, and the other charms are from
           the charm store."""
        this_service = ('quantum-gateway', 1)
        other_services = [('mysql', 1),
                          ('rabbitmq-server', 1), ('keystone', 1),
                          ('nova-cloud-controller', 1)]
        super(QuantumGatewayBasicDeployment, self)._add_services(this_service,
                                                                 other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
          'keystone:shared-db': 'mysql:shared-db',
          'quantum-gateway:shared-db': 'mysql:shared-db',
          'quantum-gateway:amqp': 'rabbitmq-server:amqp',
          'nova-cloud-controller:quantum-network-service': \
                                      'quantum-gateway:quantum-network-service',
          'nova-cloud-controller:shared-db': 'mysql:shared-db',
          'nova-cloud-controller:identity-service': 'keystone:identity-service',
          'nova-cloud-controller:amqp': 'rabbitmq-server:amqp'
        }
        super(QuantumGatewayBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        nova_cc_config = {'network-manager': 'Quantum',
                          'quantum-security-groups': 'yes'}
        configs = {'keystone': keystone_config,
                   'nova-cloud-controller': nova_cc_config}
        super(QuantumGatewayBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.mysql_sentry = self.d.sentry.unit['mysql/0']
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.rabbitmq_sentry = self.d.sentry.unit['rabbitmq-server/0']
        self.nova_cc_sentry = self.d.sentry.unit['nova-cloud-controller/0']
        self.quantum_gateway_sentry = self.d.sentry.unit['quantum-gateway/0']

        # Authenticate admin with keystone
        self.keystone = u.authenticate_keystone_admin(self.keystone_sentry,
                                                      user='admin',
                                                      password='openstack',
                                                      tenant='admin')


        # Authenticate admin with neutron
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')
        self.neutron = neutronclient.Client(auth_url=ep,
                                            username='admin',
                                            password='openstack',
                                            tenant_name='admin',
                                            region_name='RegionOne')

    def test_services(self):
        """Verify the expected services are running on the corresponding
           service units."""
        if self._get_openstack_release() >= self.precise_havana:
            neutron_services = ['status neutron-dhcp-agent',
                                'status neutron-lbaas-agent',
                                'status neutron-metadata-agent',
                                'status neutron-plugin-openvswitch-agent']
            if self._get_openstack_release() == self.precise_havana:
                neutron_services.append('status neutron-l3-agent')
            else:
                neutron_services.append('status neutron-vpn-agent')
                neutron_services.append('status neutron-metering-agent')
                neutron_services.append('status neutron-ovs-cleanup')
        else:
            neutron_services = ['status quantum-dhcp-agent',
                                'status quantum-l3-agent',
                                'status quantum-metadata-agent',
                                'status quantum-plugin-openvswitch-agent']

        nova_cc_services = ['status nova-api-ec2',
                            'status nova-api-os-compute',
                            'status nova-objectstore',
                            'status nova-cert',
                            'status nova-scheduler']
        if self._get_openstack_release() >= self.precise_grizzly:
            nova_cc_services.append('status nova-conductor')

        commands = {
            self.mysql_sentry: ['status mysql'],
            self.keystone_sentry: ['status keystone'],
            self.nova_cc_sentry: nova_cc_services,
            self.quantum_gateway_sentry: neutron_services
        }

        ret = u.validate_services(commands)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_quantum_gateway_shared_db_relation(self):
        """Verify the quantum-gateway to mysql shared-db relation data"""
        unit = self.quantum_gateway_sentry
        relation = ['shared-db', 'mysql:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'database': 'nova',
            'username': 'nova',
            'hostname': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('quantum-gateway shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_mysql_shared_db_relation(self):
        """Verify the mysql to quantum-gateway shared-db relation data"""
        unit = self.mysql_sentry
        relation = ['shared-db', 'quantum-gateway:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'db_host': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('mysql shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_quantum_gateway_amqp_relation(self):
        """Verify the quantum-gateway to rabbitmq-server amqp relation data"""
        unit = self.quantum_gateway_sentry
        relation = ['amqp', 'rabbitmq-server:amqp']
        expected = {
            'username': 'neutron',
            'private-address': u.valid_ip,
            'vhost': 'openstack'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('quantum-gateway amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_rabbitmq_amqp_relation(self):
        """Verify the rabbitmq-server to quantum-gateway amqp relation data"""
        unit = self.rabbitmq_sentry
        relation = ['amqp', 'quantum-gateway:amqp']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'hostname': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('rabbitmq amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_quantum_gateway_network_service_relation(self):
        """Verify the quantum-gateway to nova-cc quantum-network-service
           relation data"""
        unit = self.quantum_gateway_sentry
        relation = ['quantum-network-service',
                    'nova-cloud-controller:quantum-network-service']
        expected = {
            'private-address': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('quantum-gateway network-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_nova_cc_network_service_relation(self):
        """Verify the nova-cc to quantum-gateway quantum-network-service
           relation data"""
        unit = self.nova_cc_sentry
        relation = ['quantum-network-service',
                    'quantum-gateway:quantum-network-service']
        expected = {
            'service_protocol': 'http',
            'service_tenant': 'services',
            'quantum_url': u.valid_url,
            'quantum_port': '9696',
            'service_port': '5000',
            'region': 'RegionOne',
            'service_password': u.not_null,
            'quantum_host': u.valid_ip,
            'auth_port': '35357',
            'auth_protocol': 'http',
            'private-address': u.valid_ip,
            'keystone_host': u.valid_ip,
            'quantum_plugin': 'ovs',
            'auth_host': u.valid_ip,
            'service_username': 'quantum_s3_ec2_nova',
            'service_tenant_name': 'services'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('nova-cc network-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed."""
        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/neutron.conf'
            services = ['neutron-dhcp-agent', 'neutron-openvswitch-agent',
                        'neutron-metering-agent', 'neutron-lbaas-agent',
                        'neutron-metadata-agent']
            if self._get_openstack_release() == self.precise_havana:
                services.append('neutron-l3-agent')
            else:
                services.append('neutron-vpn-agent')
        else:
            conf = '/etc/quantum/quantum.conf'
            services = ['quantum-dhcp-agent', 'quantum-openvswitch-agent',
                        'quantum-metadata-agent', 'quantum-l3-agent']

        self.d.configure('quantum-gateway', {'debug': 'True'})

        time = 20
        for s in services:
            if not u.service_restarted(self.quantum_gateway_sentry, s, conf,
                                       pgrep_full=True, sleep_time=time):
                msg = "service {} didn't restart after config change".format(s)
                amulet.raise_status(amulet.FAIL, msg=msg)
            time = 0

        self.d.configure('quantum-gateway', {'debug': 'False'})

    def test_neutron_config(self):
        """Verify the data in the neutron config file."""
        unit = self.quantum_gateway_sentry
        rabbitmq_relation = self.rabbitmq_sentry.relation('amqp',
                                                         'quantum-gateway:amqp')

        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/neutron.conf'
            expected = {
                'DEFAULT': {
                    'verbose': 'False',
                    'debug': 'False',
                    'lock_path': '/var/lock/neutron',
                    'rabbit_userid': 'neutron',
                    'rabbit_virtual_host': 'openstack',
                    'rabbit_password': rabbitmq_relation['password'],
                    'rabbit_host': rabbitmq_relation['hostname'],
                    'control_exchange': 'neutron',
                    'notification_driver': 'neutron.openstack.common.notifier.'
                                           'list_notifier',
                    'list_notifier_drivers': 'neutron.openstack.common.'
                                             'notifier.rabbit_notifier'
                },
                'agent': {
                    'root_helper': 'sudo /usr/bin/neutron-rootwrap '
                                   '/etc/neutron/rootwrap.conf'
                }
            }
        else:
            conf = '/etc/quantum/quantum.conf'
            expected = {
                'DEFAULT': {
                    'verbose': 'False',
                    'debug': 'False',
                    'lock_path': '/var/lock/quantum',
                    'rabbit_userid': 'neutron',
                    'rabbit_virtual_host': 'openstack',
                    'rabbit_password': rabbitmq_relation['password'],
                    'rabbit_host': rabbitmq_relation['hostname'],
                    'control_exchange': 'quantum',
                    'notification_driver': 'quantum.openstack.common.notifier.'
                                           'list_notifier',
                    'list_notifier_drivers': 'quantum.openstack.common.'
                                             'notifier.rabbit_notifier'
                },
                'AGENT': {
                    'root_helper': 'sudo /usr/bin/quantum-rootwrap '
                                   '/etc/quantum/rootwrap.conf'
                }
            }

        if self._get_openstack_release() >= self.precise_icehouse:
            expected['DEFAULT']['core_plugin'] = \
                                          'neutron.plugins.ml2.plugin.Ml2Plugin'
        elif self._get_openstack_release() >= self.precise_havana:
            expected['DEFAULT']['core_plugin'] = \
             'neutron.plugins.openvswitch.ovs_neutron_plugin.OVSNeutronPluginV2'
        else:
            expected['DEFAULT']['core_plugin'] = \
             'quantum.plugins.openvswitch.ovs_quantum_plugin.OVSQuantumPluginV2'

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "neutron config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_ml2_config(self):
        """Verify the data in the ml2 config file. This is only available
           since icehouse."""
        if self._get_openstack_release() < self.precise_icehouse:
            return

        unit = self.quantum_gateway_sentry
        conf = '/etc/neutron/plugins/ml2/ml2_conf.ini'
        quantum_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')
        expected = {
            'ml2': {
                'type_drivers': 'gre,vxlan',
                'tenant_network_types': 'gre,vxlan',
                'mechanism_drivers': 'openvswitch'
            },
            'ml2_type_gre': {
                'tunnel_id_ranges': '1:1000'
            },
            'ml2_type_vxlan': {
                'vni_ranges': '1001:2000'
            },
            'ovs': {
                'enable_tunneling': 'True',
                'local_ip': quantum_gateway_relation['private-address']
            },
            'agent': {
                'tunnel_types': 'gre'
            },
            'securitygroup': {
                'firewall_driver': 'neutron.agent.linux.iptables_firewall.'
                                   'OVSHybridIptablesFirewallDriver'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "ml2 config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_api_paste_config(self):
        """Verify the data in the api paste config file."""
        unit = self.quantum_gateway_sentry
        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/api-paste.ini'
            expected = {
                'composite:neutron': {
                    'use': 'egg:Paste#urlmap',
                    '/': 'neutronversions',
                    '/v2.0': 'neutronapi_v2_0'
                },
                'filter:keystonecontext': {
                    'paste.filter_factory': 'neutron.auth:'
                                            'NeutronKeystoneContext.factory'
                },
                'filter:authtoken': {
                    'paste.filter_factory': 'keystoneclient.middleware.'
                                            'auth_token:filter_factory'
                },
                'filter:extensions': {
                    'paste.filter_factory': 'neutron.api.extensions:'
                                            'plugin_aware_extension_middleware_'
                                            'factory'
                },
                'app:neutronversions': {
                    'paste.app_factory': 'neutron.api.versions:Versions.factory'
                },
                'app:neutronapiapp_v2_0': {
                    'paste.app_factory': 'neutron.api.v2.router:APIRouter.'
                                         'factory'
                }
            }
            if self._get_openstack_release() == self.precise_havana:
                expected_additional = {
                    'composite:neutronapi_v2_0': {
                        'use': 'call:neutron.auth:pipeline_factory',
                        'noauth': 'extensions neutronapiapp_v2_0',
                        'keystone': 'authtoken keystonecontext extensions '
                                    'neutronapiapp_v2_0'
                    }
                }
            else:
                expected_additional = {
                    'composite:neutronapi_v2_0': {
                        'use': 'call:neutron.auth:pipeline_factory',
                        'noauth': 'request_id catch_errors extensions '
                                  'neutronapiapp_v2_0',
                        'keystone': 'request_id catch_errors authtoken '
                                    'keystonecontext extensions '
                                    'neutronapiapp_v2_0'
                    }
                }
            expected = dict(expected.items() + expected_additional.items())
        else:
            conf = '/etc/quantum/api-paste.ini'
            expected = {
                'composite:quantum': {
                    'use': 'egg:Paste#urlmap',
                    '/': 'quantumversions',
                    '/v2.0': 'quantumapi_v2_0'
                },
                'composite:quantumapi_v2_0': {
                    'use': 'call:quantum.auth:pipeline_factory',
                    'noauth': 'extensions quantumapiapp_v2_0',
                    'keystone': 'authtoken keystonecontext extensions '
                                'quantumapiapp_v2_0',
                },
                'filter:keystonecontext': {
                    'paste.filter_factory': 'quantum.auth:'
                                            'QuantumKeystoneContext.factory'
                },
                'filter:authtoken': {
                    'paste.filter_factory': 'keystoneclient.middleware.'
                                            'auth_token:filter_factory'
                },
                'filter:extensions': {
                    'paste.filter_factory': 'quantum.api.extensions:'
                                            'plugin_aware_extension_middleware_'
                                            'factory'
                },
                'app:quantumversions': {
                    'paste.app_factory': 'quantum.api.versions:Versions.factory'
                },
                'app:quantumapiapp_v2_0': {
                    'paste.app_factory': 'quantum.api.v2.router:APIRouter.'
                                         'factory'
                }
            }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "api paste config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_dhcp_agent_config(self):
        """Verify the data in the dhcp agent config file."""
        unit = self.quantum_gateway_sentry
        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/dhcp_agent.ini'
            expected = {
                'state_path': '/var/lib/neutron',
                'interface_driver': 'neutron.agent.linux.interface.'
                                    'OVSInterfaceDriver',
                'dhcp_driver': 'neutron.agent.linux.dhcp.Dnsmasq',
                'root_helper': 'sudo /usr/bin/neutron-rootwrap '
                               '/etc/neutron/rootwrap.conf',
                'ovs_use_veth': 'True'
            }
        else:
            conf = '/etc/quantum/dhcp_agent.ini'
            expected = {
                'state_path': '/var/lib/quantum',
                'interface_driver': 'quantum.agent.linux.interface.'
                                    'OVSInterfaceDriver',
                'dhcp_driver': 'quantum.agent.linux.dhcp.Dnsmasq',
                'root_helper': 'sudo /usr/bin/quantum-rootwrap '
                               '/etc/quantum/rootwrap.conf'
            }

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "dhcp agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_fwaas_driver_config(self):
        """Verify the data in the fwaas driver config file.  This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.quantum_gateway_sentry
        conf = '/etc/neutron/fwaas_driver.ini'
        expected = {
            'driver': 'neutron.services.firewall.drivers.linux.'
                      'iptables_fwaas.IptablesFwaasDriver',
            'enabled': 'True'
        }

        ret = u.validate_config_data(unit, conf, 'fwaas', expected)
        if ret:
            message = "fwaas driver config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_l3_agent_config(self):
        """Verify the data in the l3 agent config file."""
        unit = self.quantum_gateway_sentry
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'quantum-gateway:quantum-network-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')

        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/l3_agent.ini'
            expected = {
                'interface_driver': 'neutron.agent.linux.interface.'
                                    'OVSInterfaceDriver',
                'auth_url': ep,
                'auth_region': 'RegionOne',
                'admin_tenant_name': 'services',
                'admin_user': 'quantum_s3_ec2_nova',
                'admin_password': nova_cc_relation['service_password'],
                'root_helper': 'sudo /usr/bin/neutron-rootwrap '
                               '/etc/neutron/rootwrap.conf',
                'ovs_use_veth': 'True',
                'handle_internal_only_routers': 'True'
            }
        else:
            conf = '/etc/quantum/l3_agent.ini'
            expected = {
                'interface_driver': 'quantum.agent.linux.interface.'
                                    'OVSInterfaceDriver',
                'auth_url': ep,
                'auth_region': 'RegionOne',
                'admin_tenant_name': 'services',
                'admin_user': 'quantum_s3_ec2_nova',
                'admin_password': nova_cc_relation['service_password'],
                'root_helper': 'sudo /usr/bin/quantum-rootwrap '
                               '/etc/quantum/rootwrap.conf'
            }

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "l3 agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_lbaas_agent_config(self):
        """Verify the data in the lbaas agent config file. This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.quantum_gateway_sentry
        conf = '/etc/neutron/lbaas_agent.ini'
        expected = {
            'DEFAULT': {
                'periodic_interval': '10',
                'interface_driver': 'neutron.agent.linux.interface.'
                                    'OVSInterfaceDriver',
                'ovs_use_veth': 'False',
                'device_driver': 'neutron.services.loadbalancer.drivers.'
                                 'haproxy.namespace_driver.HaproxyNSDriver'
            },
            'haproxy': {
                'loadbalancer_state_path': '$state_path/lbaas',
                'user_group': 'nogroup'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "lbaas agent config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_metadata_agent_config(self):
        """Verify the data in the metadata agent config file."""
        unit = self.quantum_gateway_sentry
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')
        quantum_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'quantum-gateway:quantum-network-service')

        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/metadata_agent.ini'
            expected = {
                'auth_url': ep,
                'auth_region': 'RegionOne',
                'admin_tenant_name': 'services',
                'admin_user': 'quantum_s3_ec2_nova',
                'admin_password': nova_cc_relation['service_password'],
                'root_helper': 'sudo neutron-rootwrap '
                                 '/etc/neutron/rootwrap.conf',
                'state_path': '/var/lib/neutron',
                'nova_metadata_ip': quantum_gateway_relation['private-address'],
                'nova_metadata_port': '8775'
            }
        else:
            conf = '/etc/quantum/metadata_agent.ini'
            expected = {
                'auth_url': ep,
                'auth_region': 'RegionOne',
                'admin_tenant_name': 'services',
                'admin_user': 'quantum_s3_ec2_nova',
                'admin_password': nova_cc_relation['service_password'],
                'root_helper': 'sudo quantum-rootwrap '
                               '/etc/quantum/rootwrap.conf',
                'state_path': '/var/lib/quantum',
                'nova_metadata_ip': quantum_gateway_relation['private-address'],
                'nova_metadata_port': '8775'
            }

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "metadata agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_metering_agent_config(self):
        """Verify the data in the metering agent config file.  This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.quantum_gateway_sentry
        conf = '/etc/neutron/metering_agent.ini'
        expected = {
            'driver': 'neutron.services.metering.drivers.iptables.'
                      'iptables_driver.IptablesMeteringDriver',
            'measure_interval': '30',
            'report_interval': '300',
            'interface_driver': 'neutron.agent.linux.interface.'
                                'OVSInterfaceDriver',
            'use_namespaces': 'True'
        }

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "metering agent config error: {}".format(ret)

    def test_nova_config(self):
        """Verify the data in the nova config file."""
        unit = self.quantum_gateway_sentry
        conf = '/etc/nova/nova.conf'
        mysql_relation = self.mysql_sentry.relation('shared-db',
                                                    'quantum-gateway:shared-db')
        db_uri = "mysql://{}:{}@{}/{}".format('nova',
                                              mysql_relation['password'],
                                              mysql_relation['db_host'],
                                              'nova')
        rabbitmq_relation = self.rabbitmq_sentry.relation('amqp',
                                                         'quantum-gateway:amqp')
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'quantum-gateway:quantum-network-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')

        if self._get_openstack_release() >= self.precise_havana:
            expected = {
                'logdir': '/var/log/nova',
                'state_path': '/var/lib/nova',
                'lock_path': '/var/lock/nova',
                'root_helper': 'sudo nova-rootwrap /etc/nova/rootwrap.conf',
                'verbose': 'False',
                'use_syslog': 'False',
                'api_paste_config': '/etc/nova/api-paste.ini',
                'enabled_apis': 'metadata',
                'multi_host': 'True',
                'sql_connection': db_uri,
                'service_neutron_metadata_proxy': 'True',
                'rabbit_userid': 'neutron',
                'rabbit_virtual_host': 'openstack',
                'rabbit_password': rabbitmq_relation['password'],
                'rabbit_host': rabbitmq_relation['hostname'],
                'network_api_class': 'nova.network.neutronv2.api.API',
                'neutron_auth_strategy': 'keystone',
                'neutron_url': nova_cc_relation['quantum_url'],
                'neutron_admin_tenant_name': 'services',
                'neutron_admin_username': 'quantum_s3_ec2_nova',
                'neutron_admin_password': nova_cc_relation['service_password'],
                'neutron_admin_auth_url': ep

            }
        else:
            expected = {
                'logdir': '/var/log/nova',
                'state_path': '/var/lib/nova',
                'lock_path': '/var/lock/nova',
                'root_helper': 'sudo nova-rootwrap /etc/nova/rootwrap.conf',
                'verbose': 'True',
                'api_paste_config': '/etc/nova/api-paste.ini',
                'enabled_apis': 'metadata',
                'multi_host': 'True',
                'sql_connection':  db_uri,
                'service_quantum_metadata_proxy': 'True',
                'rabbit_userid': 'neutron',
                'rabbit_virtual_host': 'openstack',
                'rabbit_password': rabbitmq_relation['password'],
                'rabbit_host': rabbitmq_relation['hostname'],
                'network_api_class': 'nova.network.quantumv2.api.API',
                'quantum_auth_strategy': 'keystone',
                'quantum_url': nova_cc_relation['quantum_url'],
                'quantum_admin_tenant_name': 'services',
                'quantum_admin_username': 'quantum_s3_ec2_nova',
                'quantum_admin_password': nova_cc_relation['service_password'],
                'quantum_admin_auth_url': ep
            }

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "nova config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_ovs_neutron_plugin_config(self):
        """Verify the data in the ovs neutron plugin config file. The ovs
           plugin is not used by default since icehouse."""
        if self._get_openstack_release() >= self.precise_icehouse:
            return

        unit = self.quantum_gateway_sentry
        quantum_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')

        if self._get_openstack_release() >= self.precise_havana:
            conf = '/etc/neutron/plugins/openvswitch/ovs_neutron_plugin.ini'
            expected = {
                'ovs': {
                    'local_ip': quantum_gateway_relation['private-address'],
                    'tenant_network_type': 'gre',
                    'enable_tunneling': 'True',
                    'tunnel_id_ranges': '1:1000'
                }
            }
            if self._get_openstack_release() > self.precise_havana:
                expected_additional = {
                    'agent': {
                        'polling_interval': '10',
                        'root_helper': 'sudo /usr/bin/neutron-rootwrap '
                        '/etc/neutron/rootwrap.conf'
                    }
                }
                expected = dict(expected.items() + expected_additional.items())
        else:
            conf = '/etc/quantum/plugins/openvswitch/ovs_quantum_plugin.ini'
            expected = {
                'OVS': {
                    'local_ip': quantum_gateway_relation['private-address'],
                    'tenant_network_type': 'gre',
                    'enable_tunneling': 'True',
                    'tunnel_id_ranges': '1:1000'
                    },
                'AGENT': {
                    'polling_interval': '10',
                    'root_helper': 'sudo /usr/bin/quantum-rootwrap '
                                   '/etc/quantum/rootwrap.conf'
                    }
            }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "ovs neutron plugin config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_vpn_agent_config(self):
        """Verify the data in the vpn agent config file.  This isn't available
           prior to havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.quantum_gateway_sentry
        conf = '/etc/neutron/vpn_agent.ini'
        expected = {
            'vpnagent': {
                'vpn_device_driver': 'neutron.services.vpn.device_drivers.'
                                     'ipsec.OpenSwanDriver'
            },
            'ipsec': {
                'ipsec_status_check_interval': '60'
            }
        }

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "vpn agent config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_create_network(self):
        """Create a network, verify that it exists, and then delete it."""
        self.neutron.format = 'json'
        net_name = 'ext_net'

        #Verify that the network doesn't exist
        networks = self.neutron.list_networks(name=net_name)
        net_count = len(networks['networks'])
        if net_count != 0:
            msg = "Expected zero networks, found {}".format(net_count)
            amulet.raise_status(amulet.FAIL, msg=msg)

        # Create a network and verify that it exists
        network = {'name': net_name}
        self.neutron.create_network({'network':network})

        networks = self.neutron.list_networks(name=net_name)
        net_len = len(networks['networks'])
        if net_len != 1:
            msg = "Expected 1 network, found {}".format(net_len)
            amulet.raise_status(amulet.FAIL, msg=msg)

        network = networks['networks'][0]
        if network['name'] != net_name:
            amulet.raise_status(amulet.FAIL, msg="network ext_net not found")

        #Cleanup
        self.neutron.delete_network(network['id'])
