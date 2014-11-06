#!/usr/bin/python

from base64 import b64decode

from charmhelpers.core.hookenv import (
    log, ERROR, WARNING,
    config,
    is_relation_made,
    relation_get,
    relation_set,
    relation_ids,
    relations_of_type,
    local_unit,
    unit_get,
    Hooks, UnregisteredHookError
)
from charmhelpers.fetch import (
    apt_update,
    apt_install,
    filter_installed_packages,
)
from charmhelpers.core.host import (
    restart_on_change,
    lsb_release,
)
from charmhelpers.contrib.hahelpers.cluster import(
    eligible_leader
)
from charmhelpers.contrib.hahelpers.apache import(
    install_ca_cert
)
from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
    openstack_upgrade_available,
)
from charmhelpers.payload.execd import execd_preinstall

from charmhelpers.contrib.charmsupport.nrpe import NRPE

import sys
from quantum_utils import (
    register_configs,
    restart_map,
    do_openstack_upgrade,
    get_packages,
    get_early_packages,
    get_common_package,
    valid_plugin,
    configure_ovs,
    reassign_agent_resources,
    stop_services
)

hooks = Hooks()
CONFIGS = register_configs()


@hooks.hook('install')
def install():
    execd_preinstall()
    src = config('openstack-origin')
    if (lsb_release()['DISTRIB_CODENAME'] == 'precise' and
            src == 'distro'):
        src = 'cloud:precise-folsom'
    configure_installation_source(src)
    apt_update(fatal=True)
    if valid_plugin():
        apt_install(filter_installed_packages(get_early_packages()),
                    fatal=True)
        apt_install(filter_installed_packages(get_packages()),
                    fatal=True)
    else:
        log('Please provide a valid plugin config', level=ERROR)
        sys.exit(1)


@hooks.hook('config-changed')
@restart_on_change(restart_map())
def config_changed():
    global CONFIGS
    if openstack_upgrade_available(get_common_package()):
        CONFIGS = do_openstack_upgrade()
    update_nrpe_config()
    # Re-run joined hooks as config might have changed
    for r_id in relation_ids('shared-db'):
        db_joined(relation_id=r_id)
    for r_id in relation_ids('pgsql-db'):
        pgsql_db_joined(relation_id=r_id)
    for r_id in relation_ids('amqp'):
        amqp_joined(relation_id=r_id)
    for r_id in relation_ids('amqp-nova'):
        amqp_nova_joined(relation_id=r_id)
    if valid_plugin():
        CONFIGS.write_all()
        configure_ovs()
    else:
        log('Please provide a valid plugin config', level=ERROR)
        sys.exit(1)


@hooks.hook('upgrade-charm')
def upgrade_charm():
    install()
    config_changed()


@hooks.hook('shared-db-relation-joined')
def db_joined(relation_id=None):
    if is_relation_made('pgsql-db'):
        # raise error
        e = ('Attempting to associate a mysql database when there is already '
             'associated a postgresql one')
        log(e, level=ERROR)
        raise Exception(e)
    relation_set(username=config('database-user'),
                 database=config('database'),
                 hostname=unit_get('private-address'),
                 relation_id=relation_id)


@hooks.hook('pgsql-db-relation-joined')
def pgsql_db_joined(relation_id=None):
    if is_relation_made('shared-db'):
        # raise error
        e = ('Attempting to associate a postgresql database when there'
             ' is already associated a mysql one')
        log(e, level=ERROR)
        raise Exception(e)
    relation_set(database=config('database'),
                 relation_id=relation_id)


@hooks.hook('amqp-nova-relation-joined')
def amqp_nova_joined(relation_id=None):
    relation_set(relation_id=relation_id,
                 username=config('nova-rabbit-user'),
                 vhost=config('nova-rabbit-vhost'))


@hooks.hook('amqp-relation-joined')
def amqp_joined(relation_id=None):
    relation_set(relation_id=relation_id,
                 username=config('rabbit-user'),
                 vhost=config('rabbit-vhost'))


@hooks.hook('amqp-nova-relation-departed')
@hooks.hook('amqp-nova-relation-changed')
@restart_on_change(restart_map())
def amqp_nova_changed():
    if 'amqp-nova' not in CONFIGS.complete_contexts():
        log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write_all()


@hooks.hook('amqp-relation-departed')
@restart_on_change(restart_map())
def amqp_departed():
    if 'amqp' not in CONFIGS.complete_contexts():
        log('amqp relation incomplete. Peer not ready?')
        return
    CONFIGS.write_all()


@hooks.hook('shared-db-relation-changed',
            'pgsql-db-relation-changed',
            'amqp-relation-changed',
            'cluster-relation-changed',
            'cluster-relation-joined',
            'neutron-plugin-api-relation-changed')
@restart_on_change(restart_map())
def db_amqp_changed():
    CONFIGS.write_all()


@hooks.hook('quantum-network-service-relation-changed')
@restart_on_change(restart_map())
def nm_changed():
    CONFIGS.write_all()
    if relation_get('ca_cert'):
        ca_crt = b64decode(relation_get('ca_cert'))
        install_ca_cert(ca_crt)


@hooks.hook("cluster-relation-departed")
@restart_on_change(restart_map())
def cluster_departed():
    if config('plugin') in ['nvp', 'nsx']:
        log('Unable to re-assign agent resources for'
            ' failed nodes with nvp|nsx',
            level=WARNING)
        return
    if eligible_leader(None):
        reassign_agent_resources()
        CONFIGS.write_all()


@hooks.hook('cluster-relation-broken')
@hooks.hook('stop')
def stop():
    stop_services()


@hooks.hook('nrpe-external-master-relation-joined', 'nrpe-external-master-relation-changed')
def update_nrpe_config():
    SERVICES = [
        'neutron-dhcp-agent',
        'neutron-lbaas-agent',
        'neutron-metadata-agent',
        'neutron-metering-agent',
        'neutron-ovs-cleanup',
        'neutron-plugin-openvswitch-agent',
        'neutron-vpn-agent',
    ]
    # Find out if nrpe set nagios_hostname
    hostname = None
    host_context = None
    for rel in relations_of_type('nrpe-external-master'):
        if 'nagios_hostname' in rel:
            hostname = rel['nagios_hostname']
            host_context = rel['nagios_host_context']
            break
    nrpe = NRPE(hostname=hostname)
    apt_install('python-dbus')

    if host_context:
        current_unit = "%s:%s" % (host_context, local_unit())
    else:
        current_unit = local_unit()

    for service in SERVICES:
        nrpe.add_check(
            shortname=service,
            description='process check {%s}' % current_unit,
            check_cmd = 'check_upstart_job %s' % service,
            )

    nrpe.write()

if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
