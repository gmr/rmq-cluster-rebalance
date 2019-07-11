import argparse
import json
import logging
import os
import pathlib
import sys
import time
import typing
from urllib import parse

import coloredlogs
import requests
from requests import adapters, exceptions
import urllib3

from . import version

LOGGER = logging.getLogger(__name__)
LOGGING_FORMAT = '[%(asctime)-15s] %(levelname)-8s %(message)s'
LOGGING_FIELD_STYLES = {'hostname': {'color': 'magenta'},
                        'programname': {'color': 'yellow'},
                        'name': {'color': 'blue'},
                        'levelname': {'color': 'white', 'bold': True},
                        'asctime': {'color': 'white'}}
LOGGING_LEVEL_STYLES = {'debug': {'color': 'green'},
                        'info': {'color': 'white'},
                        'warning': {'color': 'yellow'},
                        'error': {'color': 'red'},
                        'critical': {'color': 'red', 'bold': True}}
DEFAULT_PRIORITY = 90
SLEEP_DURATION = 5

NUM_RETRIES = 3

ADAPTER = adapters.HTTPAdapter(
    max_retries=urllib3.Retry(
        status=NUM_RETRIES,
        status_forcelist=[429, 503]))


class Rebalance:
    """Rebalance queues in a RabbitMQ cluster"""
    POLICY_NAME = 'rmq-cluster-rebalance'

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.session = requests.Session()
        self.session.auth = (args.username, args.password)
        self.session.headers = {
            'User-Agent': 'rmq-cluster-rebalance/{}'.format(version)}
        self.session.mount('http://', ADAPTER)
        self.session.mount('https://', ADAPTER)
        self.session.verify = False
        self.vhost = parse.quote(self.args.vhost, safe='')
        self.nodes = self._lookup_nodes()
        self.node_offset = 0
        max_priority = self._lookup_max_priority()
        self.priority = DEFAULT_PRIORITY
        if max_priority > DEFAULT_PRIORITY:
            self.priority = max_priority + 1

    def run(self) -> typing.NoReturn:
        LOGGER.info('rmq-cluster-rebalance starting')
        for queue in self._queues():
            assign_to = self._node_assignment()
            if assign_to == queue['node']:
                LOGGER.info('Queue %s is already on %s, skipping',
                            queue['name'], queue['node'])
                self._advance_node()
                continue
            LOGGER.info('Moving %s to %s', queue['name'], assign_to)
            if assign_to not in queue.get('synchronised_slave_nodes', []):
                self._apply_step1_policy(queue)
            self._apply_step2_policy(queue, assign_to)
            self._delete_policy()
            self._advance_node()

    def _advance_node(self) -> typing.NoReturn:
        self.node_offset += 1
        if self.node_offset == len(self.nodes):
            self.node_offset = 0

    def _apply_policy(self, queue_name: str, definition: dict) \
            -> typing.NoReturn:
        try:
            result = self.session.put(
                self._build_url('/api/policies/{vhost}/{policy}'),
                data=json.dumps({
                    'pattern': '^{}$'.format(queue_name),
                    'definition': definition,
                    'priority': self.priority,
                    'apply-to': 'queues'}))
        except exceptions.ConnectionError as error:
            exit_application('Error applying policy: {}'.format(error), 1)
        else:
            if not result.ok:
                exit_application('Error applying policy: {}'.format(
                    result.json()['reason']), 2)

    def _apply_step1_policy(self, queue: dict) -> typing.NoReturn:
        """Apply the policy to ensure HA is setup"""
        policy = self._remove_blacklisted_keys(
            queue['effective_policy_definition'] or {})
        policy['ha-mode'] = 'all'
        self._apply_policy(queue['name'], policy)
        self._wait_for_synchronized_slaves(queue['name'])

    def _apply_step2_policy(self,
                            queue: dict,
                            destination: str) -> typing.NoReturn:
        """Apply the policy to move the master"""
        policy = queue['effective_policy_definition'] or {}
        self._remove_blacklisted_keys(policy)
        policy['ha-mode'] = 'nodes'
        policy['ha-params'] = [destination]
        self._apply_policy(queue['name'], policy)
        self._wait_for_queue_move(queue['name'], destination)

    def _build_url(self, path: str) -> str:
        kwargs = {'vhost': self.vhost}
        if '{policy}' in path:
            kwargs['policy'] = self.POLICY_NAME
        return '{}/{}'.format(
            self.args.url.rstrip('/'), path.format(**kwargs).lstrip('/'))

    def _delete_policy(self) -> typing.NoReturn:
        try:
            result = self.session.delete(
                self._build_url('/api/policies/{vhost}/{policy}'))
        except exceptions.ConnectionError as error:
            exit_application('Error deleting policy: {}'.format(error), 1)
        else:
            if not result.ok:
                exit_application('Error deleting policy: {}'.format(
                    result.json()['reason']), 3)

    def _get_queue_info(self, name: str) -> dict:
        try:
            response = self.session.get(
                self._build_url('/api/queues/{vhost}/{name}'.format(
                    vhost=self.vhost, name=name)))
        except exceptions.ConnectionError as error:
            exit_application('Error getting queue info: {}'.format(error), 1)
        else:
            queue = response.json()
            if not response.ok:
                exit_application('Error getting queue info: {}'.format(
                    queue['reason']), 4)
            return queue

    def _lookup_max_priority(self) -> int:
        try:
            result = self.session.get(self._build_url('/api/policies/{vhost}'))
        except exceptions.ConnectionError as error:
            exit_application('Error looking up policies: {}'.format(error), 1)
        else:
            policies = result.json()
            if not result.ok:
                exit_application('Error looking up policies: {}'.format(
                    policies['reason']), 5)
            if any(p['name'] == self.POLICY_NAME for p in policies):
                self._delete_policy()
            return max(p['priority'] for p in policies) if policies else 0

    def _lookup_nodes(self) -> typing.List[str]:
        try:
            result = self.session.get(self._build_url('/api/nodes'))
        except exceptions.ConnectionError as error:
            exit_application('Error looking up nodes: {}'.format(error), 1)
        else:
            nodes = result.json()
            if not result.ok:
                exit_application('Error looking up nodes: {}'.format(
                   nodes['reason']), 6)
            return [node['name'] for node in nodes]

    def _node_assignment(self) -> str:
        return self.nodes[self.node_offset]

    @staticmethod
    def _remove_blacklisted_keys(policy: dict) -> dict:
        for key in ['ha-mode', 'ha-params', 'queue-master-locator']:
            if key in policy:
                del policy[key]
        return policy

    def _queues(self) -> typing.Generator[dict, None, None]:
        try:
            response = self.session.get(self._build_url('/api/queues/{vhost}'))
        except exceptions.ConnectionError as error:
            exit_application('Error getting queues: {}'.format(error), 1)
        else:
            result = response.json()
            if not response.ok:
                exit_application('Error getting queues: {}'.format(
                   result['reason']), 7)
            for queue in result:
                yield queue

    def _wait_for_queue_move(self, name: str, node: str) -> typing.NoReturn:
        LOGGER.info('Waiting for %s to move to %s', name, node)
        while True:
            queue = self._get_queue_info(name)
            if (queue['node'] == node and
                    not queue.get('slave_nodes') and
                    not queue.get('synchronised_slave_nodes')):
                break
            LOGGER.info('Sleeping for %i seconds for queue move',
                        SLEEP_DURATION)
            time.sleep(SLEEP_DURATION)

    def _wait_for_synchronized_slaves(self, name: str) -> typing.NoReturn:
        LOGGER.info('Waiting for %s to synchronize HA slaves', name)
        while True:
            queue = self._get_queue_info(name)
            LOGGER.debug('sn: %r/ ssn: %r',
                         sorted(queue.get('slave_nodes', [])),
                         sorted(queue.get('synchronised_slave_nodes', [])))
            if (queue.get('slave_nodes') and
                sorted(queue.get('slave_nodes', [])) == sorted(queue.get(
                    'synchronised_slave_nodes', []))):
                break
            LOGGER.info('Sleeping for %i seconds while waiting HA sync',
                        SLEEP_DURATION)
            time.sleep(SLEEP_DURATION)


def configure_logging(args: argparse.Namespace) \
        -> typing.NoReturn:  # pragma: nocover
    """Configure Python logging"""
    logging.captureWarnings(True)
    level = logging.INFO if args.verbose else logging.WARNING
    if args.debug:
        level = logging.DEBUG
    filename = args.log_file if args.log_file != 'STDOUT' else None
    if filename:
        path = pathlib.Path(filename)
        if not path.parent.exists:
            filename = None
    if filename:
        logging.basicConfig(
            level=level, filename=filename, format=LOGGING_FORMAT)
    else:
        coloredlogs.install(
            level=level, fmt=LOGGING_FORMAT, level_styles=LOGGING_LEVEL_STYLES,
            field_styles=LOGGING_FIELD_STYLES)

    # urilib3 is chatty, even on INFO
    logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)


def exit_application(message: typing.Optional[str] = None,
                     code: int = 0) -> typing.NoReturn:  # pragma: nocover
    """Exit the application displaying the message to either INFO or ERROR
    based upon the exist code.

    :param str message: The exit message
    :param int code: The exit code (default: 0)

    """
    if message:
        log_method = LOGGER.info if not code else LOGGER.error
        log_method(message.strip())
    sys.exit(code)


def parse_cli_arguments(args: typing.Optional[list] = None) \
        -> argparse.Namespace:
    """Return the parsed CLI arguments for the application invocation"""
    parser = argparse.ArgumentParser(
        'rmq-cluster-rebalance', conflict_handler='resolve',
        description='Rebalances the queues in a RabbitMQ cluster',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '-u', '--username',
        default=os.environ.get('RABBITMQ_USER', 'guest'),
        help='The RabbitMQ Management API username')
    parser.add_argument(
        '-p', '--password',
        default=os.environ.get('RABBITMQ_PASSWORD', 'guest'),
        help='The RabbitMQ Management API password')
    parser.add_argument(
        '--vhost', default=os.environ.get('RABBITMQ_VHOST', '/'),
        help='The RabbitMQ VHost to use')

    group = parser.add_argument_group(title='Logging options')
    group.add_argument(
        '-L', '--log-file', action='store', default='STDOUT',
        help='Log to the specified filename')
    group.add_argument(
        '-v', '--verbose', action='store_true',
        help='Increase output verbosity')
    group.add_argument(
        '--debug', action='store_true', help='Extra verbose debug logging')
    parser.add_argument(
        '--version', action='version', version='%(prog)s {}'.format(version),
        help='output version information, then exit')

    parser.add_argument(
        'url', metavar='URL', nargs='?',
        default=os.environ.get('RABBITMQ_URL', 'http://localhost:15672'),
        help='The RabbitMQ Management API base URL')

    return parser.parse_args(args)


def main() -> typing.NoReturn:  # pragma: nocover
    """CLI Entry-point"""
    urllib3.disable_warnings()
    args = parse_cli_arguments()
    configure_logging(args)
    Rebalance(args).run()
