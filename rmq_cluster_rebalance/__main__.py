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


class Rebalance:

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.session = requests.Session()
        self.session.auth = (args.username, args.password)
        self.session.headers = {
            'User-Agent': 'rmq-cluster-rebalance/{}'.format(version)
        }
        self.session.verify = False
        self.vhost = parse.quote(self.args.vhost, safe='')
        self.nodes = self._lookup_nodes()
        self.node_offset = 0
        max_priority = self._lookup_max_priority()
        self.priority = DEFAULT_PRIORITY
        if max_priority > DEFAULT_PRIORITY:
            self.priority = max_priority + 1

    def run(self):
        LOGGER.info('rmq-cluster-rebalance starting')
        LOGGER.debug('Nodes: %r', self.nodes)
        for queue in self._queues():
            assign_to = self._node_assignment()
            if assign_to == queue['node']:
                LOGGER.info('Queue %s is already on %s, skipping',
                            queue['name'], queue['node'])
                self.node_offset += 1
                if self.node_offset == len(self.nodes):
                    self.node_offset = 0
                continue
            LOGGER.info('Moving %s to %s', queue['name'], assign_to)
            policy = queue['effective_policy_definition'] or {}
            if queue['node'] not in queue.get('synchronised_slave_nodes', []):
                policy = queue['effective_policy_definition'] or {}
                policy['ha-mode'] = 'all'
                if 'ha-params' in policy:
                    del policy['ha-params']
                self._apply_policy(queue['name'], policy)
                self._wait_for_synchronized_slaves(queue['name'])
            policy['ha-mode'] = 'nodes'
            policy['ha-params'] = [assign_to]
            self._apply_policy(queue['name'], policy)
            self._wait_for_queue_move(queue['name'], assign_to)
            self._delete_policy()
            self.node_offset += 1
            if self.node_offset == len(self.nodes):
                self.node_offset = 0

    def _apply_policy(self, queue_name: str, definition: dict):
        result = self.session.put(
            self._build_url('/api/policies/{vhost}/rmq-cluster-rebalance'),
            data=json.dumps({
                'pattern': '^{}$'.format(queue_name),
                'definition': definition,
                'priority': self.priority,
                'apply-to': 'queues'
            }))
        if not result.ok:
            exit_application(
                'Error applying policy: {}'.format(result.json()['reason']), 1)

    def _build_url(self, path: str) -> str:
        return '{}/{}'.format(
            self.args.url.rstrip('/'),
            path.format(vhost=self.vhost).lstrip('/'))

    def _delete_policy(self) -> typing.NoReturn:
        result = self.session.delete(
            self._build_url('/api/policies/{vhost}/rmq-cluster-rebalance'))
        if not result.ok:
            exit_application(
                'Error deleting policy: {}'.format(result.json()['reason']), 1)

    def _lookup_nodes(self) -> typing.List[str]:
        result = self.session.get(self._build_url('/api/nodes'))
        return [node['name'] for node in result.json()]

    def _lookup_max_priority(self) -> int:
        result = self.session.get(self._build_url('/api/policies/{vhost}'))
        policies = result.json()
        return max(p['priority'] for p in policies) if policies else 0

    def _node_assignment(self) -> str:
        return self.nodes[self.node_offset]

    def _queues(self) -> typing.Generator[dict, None, None]:
        result = self.session.get(self._build_url('/api/queues/{vhost}'))
        for queue in result.json():
            yield queue

    def _wait_for_queue_move(self, name: str, node: str) -> typing.NoReturn:
        while True:
            response = self.session.get(
                self._build_url('/api/queues/{vhost}/{name}'.format(
                    vhost=self.vhost, name=name)))
            result = response.json()
            if (result['node'] == node and
                    not result.get('slave_nodes') and
                    not result.get('synchronised_slave_nodes')):
                break
            LOGGER.info('Sleeping for %i seconds for queue move',
                        SLEEP_DURATION)
            time.sleep(SLEEP_DURATION)

    def _wait_for_synchronized_slaves(self, name: str) -> typing.NoReturn:
        while True:
            response = self.session.get(
                self._build_url('/api/queues/{vhost}/{name}'.format(
                    vhost=self.vhost, name=name)))
            result = response.json()
            LOGGER.debug('%r/%r',
                         sorted(result.get('slave_nodes', [])),
                         sorted(result.get('synchronised_slave_nodes', [])))
            if (result.get('slave_nodes') and
                sorted(result.get('slave_nodes', [])) == sorted(result.get(
                    'synchronised_slave_nodes', []))):
                break
            LOGGER.info('Sleeping for %i seconds while waiting HA sync',
                        SLEEP_DURATION)
            time.sleep(SLEEP_DURATION)


def configure_logging(args: argparse.Namespace) -> typing.NoReturn:
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
                     code: int = 0) -> typing.NoReturn:
    """Exit the application displaying the message to either INFO or ERROR
    based upon the exist code.

    :param str message: The exit message
    :param int code: The exit code (default: 0)

    """
    if message:
        log_method = LOGGER.info if not code else LOGGER.error
        log_method(message.strip())
    sys.exit(code)


def parse_cli_arguments() -> argparse.Namespace:
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

    return parser.parse_args()


def main() -> typing.NoReturn:
    """CLI Entry-point"""
    urllib3.disable_warnings()
    args = parse_cli_arguments()
    configure_logging(args)
    Rebalance(args).run()
