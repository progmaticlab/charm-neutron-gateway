#!/usr/bin/python

import amulet
import os
import time
import yaml

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
u = OpenStackAmuletUtils(DEBUG)


class NeutronGatewayBasicDeployment(OpenStackAmuletDeployment):
    """Amulet tests on a basic neutron-gateway deployment."""

    def __init__(self, series, openstack=None, source=None, git=False,
                 stable=True):
        """Deploy the entire test environment."""
        super(NeutronGatewayBasicDeployment, self).__init__(series, openstack,
                                                            source, stable)
        self.git = git
        self._add_services()
        self._add_relations()
        self._configure_services()
        self._deploy()
        self._initialize_tests()

    def _add_services(self):
        """Add services

           Add the services that we're testing, where neutron-gateway is local,
           and the rest of the service are from lp branches that are
           compatible with the local charm (e.g. stable or next).
           """
        this_service = {'name': 'neutron-gateway'}
        other_services = [{'name': 'mysql'},
                          {'name': 'rabbitmq-server'}, {'name': 'keystone'},
                          {'name': 'nova-cloud-controller'}]
        if self._get_openstack_release() >= self.trusty_kilo:
            other_services.append({'name': 'neutron-api'})
        super(NeutronGatewayBasicDeployment, self)._add_services(this_service,
                                                                 other_services)

    def _add_relations(self):
        """Add all of the relations for the services."""
        relations = {
          'keystone:shared-db': 'mysql:shared-db',
          'neutron-gateway:shared-db': 'mysql:shared-db',
          'neutron-gateway:amqp': 'rabbitmq-server:amqp',
          'nova-cloud-controller:quantum-network-service': \
                                      'neutron-gateway:quantum-network-service',
          'nova-cloud-controller:shared-db': 'mysql:shared-db',
          'nova-cloud-controller:identity-service': 'keystone:identity-service',
          'nova-cloud-controller:amqp': 'rabbitmq-server:amqp'
        }
        if self._get_openstack_release() >= self.trusty_kilo:
            relations['neutron-api:shared-db'] = 'mysql:shared-db'
            relations['neutron-api:amqp'] = 'rabbitmq-server:amqp'
            relations['neutron-api:neutron-api'] = 'nova-cloud-controller:neutron-api'
            relations['neutron-api:identity-service'] = 'keystone:identity-service'
        super(NeutronGatewayBasicDeployment, self)._add_relations(relations)

    def _configure_services(self):
        """Configure all of the services."""
        neutron_api_config = neutron_gateway_config = {}
        if self.git:
            amulet_http_proxy = os.environ.get('AMULET_HTTP_PROXY')

            branch = 'stable/' + self._get_openstack_release_string()

            if self._get_openstack_release() >= self.trusty_kilo:
                openstack_origin_git = {
                    'repositories': [
                        {'name': 'requirements',
                         'repository': 'git://github.com/openstack/requirements',
                         'branch': branch},
                        {'name': 'neutron-fwaas',
                         'repository': 'git://github.com/openstack/neutron-fwaas',
                         'branch': branch},
                        {'name': 'neutron-lbaas',
                         'repository': 'git://github.com/openstack/neutron-lbaas',
                         'branch': branch},
                        {'name': 'neutron-vpnaas',
                         'repository': 'git://github.com/openstack/neutron-vpnaas',
                         'branch': branch},
                        {'name': 'neutron',
                         'repository': 'git://github.com/openstack/neutron',
                         'branch': branch},
                    ],
                    'directory': '/mnt/openstack-git',
                    'http_proxy': amulet_http_proxy,
                    'https_proxy': amulet_http_proxy,
                }
            else:
                reqs_repo = 'git://github.com/openstack/requirements'
                neutron_repo = 'git://github.com/openstack/neutron'
                if self._get_openstack_release() == self.trusty_icehouse:
                    reqs_repo = 'git://github.com/coreycb/requirements'
                    neutron_repo = 'git://github.com/coreycb/neutron'

                openstack_origin_git = {
                    'repositories': [
                        {'name': 'requirements',
                         'repository': reqs_repo,
                         'branch': branch},
                        {'name': 'neutron',
                         'repository': neutron_repo,
                         'branch': branch},
                    ],
                    'directory': '/mnt/openstack-git',
                    'http_proxy': amulet_http_proxy,
                    'https_proxy': amulet_http_proxy,
                }
            neutron_gateway_config['openstack-origin-git'] = yaml.dump(openstack_origin_git)
        keystone_config = {'admin-password': 'openstack',
                           'admin-token': 'ubuntutesting'}
        nova_cc_config = {'network-manager': 'Quantum',
                          'quantum-security-groups': 'yes'}
        configs = {'neutron-api': neutron_api_config,
                   'neutron-gateway': neutron_gateway_config,
                   'keystone': keystone_config,
                   'nova-cloud-controller': nova_cc_config}
        super(NeutronGatewayBasicDeployment, self)._configure_services(configs)

    def _initialize_tests(self):
        """Perform final initialization before tests get run."""
        # Access the sentries for inspecting service units
        self.mysql_sentry = self.d.sentry.unit['mysql/0']
        self.keystone_sentry = self.d.sentry.unit['keystone/0']
        self.rabbitmq_sentry = self.d.sentry.unit['rabbitmq-server/0']
        self.nova_cc_sentry = self.d.sentry.unit['nova-cloud-controller/0']
        self.neutron_gateway_sentry = self.d.sentry.unit['neutron-gateway/0']

        # Let things settle a bit before moving forward
        time.sleep(30)

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
        neutron_services = ['status neutron-dhcp-agent',
                            'status neutron-lbaas-agent',
                            'status neutron-metadata-agent',
                            'status neutron-metering-agent',
                            'status neutron-ovs-cleanup',
                            'status neutron-plugin-openvswitch-agent']

        if self._get_openstack_release() <= self.trusty_juno:
            neutron_services.append('status neutron-vpn-agent')

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
            self.neutron_gateway_sentry: neutron_services
        }

        ret = u.validate_services(commands)
        if ret:
            amulet.raise_status(amulet.FAIL, msg=ret)

    def test_neutron_gateway_shared_db_relation(self):
        """Verify the neutron-gateway to mysql shared-db relation data"""
        unit = self.neutron_gateway_sentry
        relation = ['shared-db', 'mysql:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'database': 'nova',
            'username': 'nova',
            'hostname': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('neutron-gateway shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_mysql_shared_db_relation(self):
        """Verify the mysql to neutron-gateway shared-db relation data"""
        unit = self.mysql_sentry
        relation = ['shared-db', 'neutron-gateway:shared-db']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'db_host': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('mysql shared-db', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_neutron_gateway_amqp_relation(self):
        """Verify the neutron-gateway to rabbitmq-server amqp relation data"""
        unit = self.neutron_gateway_sentry
        relation = ['amqp', 'rabbitmq-server:amqp']
        expected = {
            'username': 'neutron',
            'private-address': u.valid_ip,
            'vhost': 'openstack'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('neutron-gateway amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_rabbitmq_amqp_relation(self):
        """Verify the rabbitmq-server to neutron-gateway amqp relation data"""
        unit = self.rabbitmq_sentry
        relation = ['amqp', 'neutron-gateway:amqp']
        expected = {
            'private-address': u.valid_ip,
            'password': u.not_null,
            'hostname': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('rabbitmq amqp', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_neutron_gateway_network_service_relation(self):
        """Verify the neutron-gateway to nova-cc quantum-network-service
           relation data"""
        unit = self.neutron_gateway_sentry
        relation = ['quantum-network-service',
                    'nova-cloud-controller:quantum-network-service']
        expected = {
            'private-address': u.valid_ip
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('neutron-gateway network-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_nova_cc_network_service_relation(self):
        """Verify the nova-cc to neutron-gateway quantum-network-service
           relation data"""
        unit = self.nova_cc_sentry
        relation = ['quantum-network-service',
                    'neutron-gateway:quantum-network-service']
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
            'service_username': 'nova',
            'service_tenant_name': 'services'
        }

        ret = u.validate_relation_data(unit, relation, expected)
        if ret:
            message = u.relation_error('nova-cc network-service', ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_z_restart_on_config_change(self):
        """Verify that the specified services are restarted when the config
           is changed.

           Note(coreycb): The method name with the _z_ is a little odd
           but it forces the test to run last.  It just makes things
           easier because restarting services requires re-authorization.
           """
        conf = '/etc/neutron/neutron.conf'

        services = ['neutron-dhcp-agent',
                    'neutron-lbaas-agent',
                    'neutron-metadata-agent',
                    'neutron-metering-agent',
                    'neutron-openvswitch-agent']

        if self._get_openstack_release() <= self.trusty_juno:
            services.append('neutron-vpn-agent')

        u.log.debug("Making config change on neutron-gateway...")
        self.d.configure('neutron-gateway', {'debug': 'True'})

        time = 60
        for s in services:
            u.log.debug("Checking that service restarted: {}".format(s))
            if not u.service_restarted(self.neutron_gateway_sentry, s, conf,
                                       pgrep_full=True, sleep_time=time):
                self.d.configure('neutron-gateway', {'debug': 'False'})
                msg = "service {} didn't restart after config change".format(s)
                amulet.raise_status(amulet.FAIL, msg=msg)
            time = 0

        self.d.configure('neutron-gateway', {'debug': 'False'})

    def test_neutron_config(self):
        """Verify the data in the neutron config file."""
        unit = self.neutron_gateway_sentry
        rabbitmq_relation = self.rabbitmq_sentry.relation('amqp',
                                                         'neutron-gateway:amqp')

        conf = '/etc/neutron/neutron.conf'
        expected = {
            'DEFAULT': {
                'verbose': 'False',
                'debug': 'False',
                'core_plugin': 'neutron.plugins.ml2.plugin.Ml2Plugin',
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
        if self._get_openstack_release() >= self.trusty_kilo:
            oslo_concurrency = {
                'oslo_concurrency': {
                    'lock_path':'/var/lock/neutron'
                }
            }
            oslo_messaging_rabbit = {
                'oslo_messaging_rabbit': {
                    'rabbit_userid': 'neutron',
                    'rabbit_virtual_host': 'openstack',
                    'rabbit_password': rabbitmq_relation['password'],
                    'rabbit_host': rabbitmq_relation['hostname'],
                }
            }
            expected.update(oslo_concurrency)
            expected.update(oslo_messaging_rabbit)
        else:
            expected['DEFAULT']['lock_path'] = '/var/lock/neutron'
            expected['DEFAULT']['rabbit_userid'] = 'neutron'
            expected['DEFAULT']['rabbit_virtual_host'] = 'openstack'
            expected['DEFAULT']['rabbit_password'] = rabbitmq_relation['password']
            expected['DEFAULT']['rabbit_host'] = rabbitmq_relation['hostname']

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

        unit = self.neutron_gateway_sentry
        conf = '/etc/neutron/plugins/ml2/ml2_conf.ini'
        neutron_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')
        expected = {
            'ml2': {
                'type_drivers': 'gre,vxlan,vlan,flat',
                'tenant_network_types': 'gre,vxlan,vlan,flat',
                'mechanism_drivers': 'openvswitch,l2population'
            },
            'ml2_type_gre': {
                'tunnel_id_ranges': '1:1000'
            },
            'ml2_type_vxlan': {
                'vni_ranges': '1001:2000'
            },
            'ovs': {
                'enable_tunneling': 'True',
                'local_ip': neutron_gateway_relation['private-address']
            },
            'agent': {
                'tunnel_types': 'gre',
                'l2_population': 'False'
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

    def test_dhcp_agent_config(self):
        """Verify the data in the dhcp agent config file."""
        unit = self.neutron_gateway_sentry
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

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "dhcp agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_fwaas_driver_config(self):
        """Verify the data in the fwaas driver config file.  This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.neutron_gateway_sentry
        conf = '/etc/neutron/fwaas_driver.ini'
        if self._get_openstack_release() >= self.trusty_kilo:
            expected = {
                'driver': 'neutron_fwaas.services.firewall.drivers.'
                          'linux.iptables_fwaas.IptablesFwaasDriver',
                'enabled': 'True'
            }
        else:
            expected = {
                'driver': 'neutron.services.firewall.drivers.'
                          'linux.iptables_fwaas.IptablesFwaasDriver',
                'enabled': 'True'
            }

        ret = u.validate_config_data(unit, conf, 'fwaas', expected)
        if ret:
            message = "fwaas driver config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_l3_agent_config(self):
        """Verify the data in the l3 agent config file."""
        unit = self.neutron_gateway_sentry
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'neutron-gateway:quantum-network-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')

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
        if self._get_openstack_release() >= self.trusty_kilo:
            expected['admin_user'] = 'nova'

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "l3 agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_lbaas_agent_config(self):
        """Verify the data in the lbaas agent config file. This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.neutron_gateway_sentry
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
        if self._get_openstack_release() >= self.trusty_kilo:
            expected['DEFAULT']['device_driver'] = ('neutron_lbaas.services.' +
            'loadbalancer.drivers.haproxy.namespace_driver.HaproxyNSDriver')

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "lbaas agent config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_metadata_agent_config(self):
        """Verify the data in the metadata agent config file."""
        unit = self.neutron_gateway_sentry
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')
        neutron_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'neutron-gateway:quantum-network-service')

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
            'nova_metadata_ip': neutron_gateway_relation['private-address'],
            'nova_metadata_port': '8775'
        }
        if self._get_openstack_release() >= self.trusty_kilo:
            expected['admin_user'] = 'nova'

        if self._get_openstack_release() >= self.precise_icehouse:
            expected['cache_url'] = 'memory://?default_ttl=5'

        ret = u.validate_config_data(unit, conf, 'DEFAULT', expected)
        if ret:
            message = "metadata agent config error: {}".format(ret)
            amulet.raise_status(amulet.FAIL, msg=message)

    def test_metering_agent_config(self):
        """Verify the data in the metering agent config file.  This is only
           available since havana."""
        if self._get_openstack_release() < self.precise_havana:
            return

        unit = self.neutron_gateway_sentry
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
        unit = self.neutron_gateway_sentry
        conf = '/etc/nova/nova.conf'
        mysql_relation = self.mysql_sentry.relation('shared-db',
                                                    'neutron-gateway:shared-db')
        db_uri = "mysql://{}:{}@{}/{}".format('nova',
                                              mysql_relation['password'],
                                              mysql_relation['db_host'],
                                              'nova')
        rabbitmq_relation = self.rabbitmq_sentry.relation('amqp',
                                                         'neutron-gateway:amqp')
        nova_cc_relation = self.nova_cc_sentry.relation(\
                                      'quantum-network-service',
                                      'neutron-gateway:quantum-network-service')
        ep = self.keystone.service_catalog.url_for(service_type='identity',
                                                   endpoint_type='publicURL')

        expected = {
            'DEFAULT': {
                'logdir': '/var/log/nova',
                'state_path': '/var/lib/nova',
                'root_helper': 'sudo nova-rootwrap /etc/nova/rootwrap.conf',
                'verbose': 'False',
                'use_syslog': 'False',
                'api_paste_config': '/etc/nova/api-paste.ini',
                'enabled_apis': 'metadata',
                'multi_host': 'True',
                'network_api_class': 'nova.network.neutronv2.api.API',
            }
        }
        if self._get_openstack_release() >= self.trusty_kilo:
            neutron = {
                'neutron': {
                    'auth_strategy': 'keystone',
                    'url': nova_cc_relation['quantum_url'],
                    'admin_tenant_name': 'services',
                    'admin_username': 'nova',
                    'admin_password': nova_cc_relation['service_password'],
                    'admin_auth_url': ep,
                    'service_metadata': 'True',
                }
            }
            oslo_concurrency = {
                'oslo_concurrency': {
                    'lock_path':'/var/lock/nova'
                }
            }
            oslo_messaging_rabbit = {
                'oslo_messaging_rabbit': {
                    'rabbit_userid': 'neutron',
                    'rabbit_virtual_host': 'openstack',
                    'rabbit_password': rabbitmq_relation['password'],
                    'rabbit_host': rabbitmq_relation['hostname'],
                }
            }
            expected.update(oslo_concurrency)
            expected.update(oslo_messaging_rabbit)
        else:
            d = 'DEFAULT'
            expected[d]['lock_path'] = '/var/lock/nova'
            expected[d]['rabbit_userid'] = 'neutron'
            expected[d]['rabbit_virtual_host'] = 'openstack'
            expected[d]['rabbit_password'] = rabbitmq_relation['password']
            expected[d]['rabbit_host'] = rabbitmq_relation['hostname']
            expected[d]['service_neutron_metadata_proxy'] = 'True'
            expected[d]['neutron_auth_strategy'] = 'keystone'
            expected[d]['neutron_url'] = nova_cc_relation['quantum_url']
            expected[d]['neutron_admin_tenant_name'] = 'services'
            expected[d]['neutron_admin_username'] = 'quantum_s3_ec2_nova'
            expected[d]['neutron_admin_password'] = \
                                           nova_cc_relation['service_password']
            expected[d]['neutron_admin_auth_url'] = ep

        for section, pairs in expected.iteritems():
            ret = u.validate_config_data(unit, conf, section, pairs)
            if ret:
                message = "nova config error: {}".format(ret)
                amulet.raise_status(amulet.FAIL, msg=message)

    def test_ovs_neutron_plugin_config(self):
        """Verify the data in the ovs neutron plugin config file. The ovs
           plugin is not used by default since icehouse."""
        if self._get_openstack_release() >= self.precise_icehouse:
            return

        unit = self.neutron_gateway_sentry
        neutron_gateway_relation = unit.relation('shared-db', 'mysql:shared-db')

        conf = '/etc/neutron/plugins/openvswitch/ovs_neutron_plugin.ini'
        expected = {
            'ovs': {
                'local_ip': neutron_gateway_relation['private-address'],
                'tenant_network_type': 'gre',
                'enable_tunneling': 'True',
                'tunnel_id_ranges': '1:1000'
            },
            'agent': {
                'polling_interval': '10',
                'root_helper': 'sudo /usr/bin/neutron-rootwrap '
                '/etc/neutron/rootwrap.conf'
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

        unit = self.neutron_gateway_sentry
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
        if self._get_openstack_release() >= self.trusty_kilo:
            expected['vpnagent']['vpn_device_driver'] = ('neutron_vpnaas.' +
                'services.vpn.device_drivers.ipsec.OpenSwanDriver')

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
