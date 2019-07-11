import contextlib
import json
import os
import time
import unittest
from unittest import mock

from requests import exceptions

from rmq_cluster_rebalance import __main__


class TestCase(unittest.TestCase):

    PRIORITY = 55

    def setUp(self) -> None:
        self.rabbit1_uri = os.environ['RABBIT1_URI']
        self.rabbit2_uri = os.environ['RABBIT2_URI']
        self.rabbit3_uri = os.environ['RABBIT3_URI']
        self.cli_args = __main__.parse_cli_arguments(
            ['-u', 'guest', '-p', 'guest', self.rabbit1_uri])
        self.rebalance = __main__.Rebalance(self.cli_args)
        result = self.rebalance.session.put(
            self.rebalance._build_url('/api/policies/%2f/test-policy'),
            data=json.dumps({
                'pattern': '^.*$',
                'definition': {
                    'ha-mode': 'all',
                    'max-length': 100,
                    'overflow': 'drop-head',
                    'queue-master-locator': 'min-masters'
                },
                'priority': self.PRIORITY,
                'apply-to': 'queues'
            }))
        if not result.ok:
            raise RuntimeError(result.content)

    def _wait_for_queue_ready(self, name, policy=None):
        while True:  # Wait for the queue readiness for testing
            queue = self.rebalance._get_queue_info(name)
            if 'effective_policy_definition' in queue:
                if policy and queue['policy'] != policy:
                    time.sleep(1)
                    continue
                break
            time.sleep(1)


class ExitApplicationTestCase(TestCase):

    @contextlib.contextmanager
    def exit_application(self):
        with mock.patch(
                'rmq_cluster_rebalance.__main__.exit_application') as ea:
            ea.side_effect = AssertionError
            yield ea

    def test_exit_called_on_apply_policy_error(self):
        with self.exit_application():
            with self.assertRaises(AssertionError):
                self.rebalance._apply_policy('foo', {'ha-mode': 'bar'})

    def test_exit_called_on_apply_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'put') as put:
            put.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    self.rebalance._apply_policy('foo', {'ha-mode': 'all'})

    def test_exit_called_on_delete_policy_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'delete') as delete:
            delete.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    self.rebalance._delete_policy()

    def test_exit_called_on_policy_delete_error(self):
        with self.exit_application():
            with self.assertRaises(AssertionError):
                self.rebalance._delete_policy()

    def test_exit_called_on_get_queue_info_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'get') as get:
            get.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    self.rebalance._get_queue_info('foo')

    def test_exit_called_on_get_queue_info_request_error(self):
        with self.exit_application():
            with self.assertRaises(AssertionError):
                self.rebalance._get_queue_info('foo')

    def test_exit_called_on_lookup_max_priority_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'get') as get:
            get.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    self.rebalance._lookup_max_priority()

    def test_exit_called_on_lookup_max_priority_request_error(self):
        self.rebalance.session.auth = ('bad', 'credentials')
        with self.exit_application():
            with self.assertRaises(AssertionError):
                self.rebalance._lookup_max_priority()

    def test_exit_called_on_lookup_nodes_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'get') as get:
            get.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    self.rebalance._lookup_nodes()

    def test_exit_called_on_lookup_nodes_request_error(self):
        self.rebalance.session.auth = ('bad', 'credentials')
        with self.exit_application():
            with self.assertRaises(AssertionError):
                self.rebalance._lookup_nodes()

    def test_exit_called_on_queues_connection_error(self):
        with mock.patch.object(self.rebalance.session, 'get') as get:
            get.side_effect = exceptions.ConnectionError
            with self.exit_application():
                with self.assertRaises(AssertionError):
                    list(self.rebalance._queues())

    def test_exit_called_on_queues_request_error(self):
        self.rebalance.session.auth = ('bad', 'credentials')
        with self.exit_application():
            with self.assertRaises(AssertionError):
                for _queue in self.rebalance._queues():
                    pass


class PolicyTestCase(TestCase):

    def test_default_max_policy(self):
        self.assertGreater(self.rebalance.priority, self.PRIORITY)
        self.assertEqual(self.rebalance.priority, __main__.DEFAULT_PRIORITY)

    def test_max_policy_gt_default(self):
        self.rebalance.session.put(
            self.rebalance._build_url('/api/policies/%2f/test-policy'),
            data=json.dumps({
                'pattern': '^.*$',
                'definition': {
                    'ha-mode': 'all'
                },
                'priority': __main__.DEFAULT_PRIORITY + 5,
                'apply-to': 'queues'
            }))
        rebalance = __main__.Rebalance(self.cli_args)
        self.assertGreater(rebalance.priority, __main__.DEFAULT_PRIORITY)
        self.assertEqual(rebalance.priority, __main__.DEFAULT_PRIORITY + 6)

    def test_apply_policy_1(self):
        queue_name = 'policy-queue'
        queue_url = '{}/api/queues/%2f/{}'.format(self.rabbit1_uri, queue_name)
        if not self.rebalance.session.put(
                queue_url, data=json.dumps({
                    'auto_delete': False, 'durable': True, 'arguments': {}})):
            raise RuntimeError('Failed to create queue {}'.format(queue_name))
        self._wait_for_queue_ready(queue_name, 'test-policy')
        queue = self.rebalance.session.get(queue_url).json()
        self.rebalance._apply_step1_policy(queue)
        self._wait_for_queue_ready(queue_name, self.rebalance.POLICY_NAME)
        queue = self.rebalance.session.get(queue_url).json()
        policy = queue['effective_policy_definition']
        self.assertEqual(policy['ha-mode'], 'all')
        self.assertEqual(policy['max-length'], 100)
        self.assertEqual(policy['overflow'], 'drop-head')
        self.assertNotIn('ha-params', policy)
        self.assertNotIn('queue-master-locator', policy)
        self.rebalance.session.delete(queue_url)

    def test_apply_policy_2(self):
        queue_name = 'policy-queue'
        queue_url = '{}/api/queues/%2f/{}'.format(self.rabbit1_uri, queue_name)
        if not self.rebalance.session.put(
                queue_url, data=json.dumps({
                    'auto_delete': False, 'durable': True, 'arguments': {}})):
            raise RuntimeError('Failed to create queue {}'.format(queue_name))
        self._wait_for_queue_ready(queue_name, 'test-policy')
        queue = self.rebalance.session.get(queue_url).json()
        self.rebalance._apply_step2_policy(queue, 'rabbit@rabbit2')
        self._wait_for_queue_ready(queue_name, self.rebalance.POLICY_NAME)
        queue = self.rebalance.session.get(queue_url).json()
        policy = queue['effective_policy_definition']
        self.assertEqual(policy['ha-mode'], 'nodes')
        self.assertListEqual(policy['ha-params'], ['rabbit@rabbit2'])
        self.assertEqual(policy['max-length'], 100)
        self.assertEqual(policy['overflow'], 'drop-head')
        self.assertNotIn('queue-master-locator', policy)
        self.rebalance.session.delete(queue_url)


class RebalanceTestCase(TestCase):

    QUEUES = ['queue1', 'queue2', 'queue3', 'queue4', 'queue5', 'queue6']

    def setUp(self):
        super().setUp()
        queues = {
            'queue1': self.rabbit3_uri,
            'queue2': self.rabbit1_uri,
            'queue3': self.rabbit2_uri,
            'queue4': self.rabbit3_uri,
            'queue5': self.rabbit1_uri,
            'queue6': self.rabbit2_uri
        }
        for queue, base_uri in queues.items():
            if not self.rebalance.session.put(
                    '{}/api/queues/%2f/{}'.format(base_uri, queue),
                    data=json.dumps({
                        'auto_delete': False,
                        'durable': True,
                        'arguments': {
                            'x-queue-master-locator': 'client-local'
                        }})):
                raise RuntimeError('Failed to create queue {}'.format(queue))
        for queue in queues.keys():
            self._wait_for_queue_ready(queue)

    def tearDown(self):
        super().tearDown()
        for name in self.QUEUES:
            if not self.rebalance.session.delete('{}/api/queues/%2f/{}'.format(
                    self.rabbit1_uri, name)):
                raise RuntimeError('Failed to delete queue {}'.format(name))
            self.rebalance.session.delete(
                self.rebalance._build_url('/api/policies/%2F/{}'.format(name)))

    def _compare_queues(self, expectation):
        queues = {}
        for queue in self.rebalance._queues():
            queues[queue['name']] = queue['node']
        self.assertDictEqual(queues, expectation)

    def test_test_setup_expectations(self):
        # Test queues have not been moved yet
        self._compare_queues({
            'queue1': 'rabbit@rabbit3',
            'queue2': 'rabbit@rabbit1',
            'queue3': 'rabbit@rabbit2',
            'queue4': 'rabbit@rabbit3',
            'queue5': 'rabbit@rabbit1',
            'queue6': 'rabbit@rabbit2'})

    def test_queue_rebalance(self):
        # Make queue6 non-ha for branch coverage
        for offset in [1, 2, 3]:
            name = 'queue{}'.format(offset)
            if not self.rebalance.session.put(
                self.rebalance._build_url('/api/policies/%2f/{}'.format(name)),
                data=json.dumps({
                    'pattern': '^{}$'.format(name),
                    'definition': {
                        'max-length': 100,
                        'overflow': 'drop-head'
                    },
                    'priority': self.PRIORITY + 1,
                    'apply-to': 'queues'})).ok:
                raise RuntimeError(
                    'Failed to apply policy for {}'.format(name))
            self._wait_for_queue_ready(name, name)

        # Only queue1, queue2, and queue3 should invoke step 1
        with mock.patch.object(
                self.rebalance, '_apply_step1_policy',
                wraps=self.rebalance._apply_step1_policy) as apply_policy:
            self.rebalance.run()
            self.assertListEqual(
                [args[0]['name'] for args, _kw in apply_policy.call_args_list],
                ['queue1', 'queue2', 'queue3'])

        # Test post move queue locations
        self._compare_queues({
            'queue1': 'rabbit@rabbit1',
            'queue2': 'rabbit@rabbit2',
            'queue3': 'rabbit@rabbit3',
            'queue4': 'rabbit@rabbit1',
            'queue5': 'rabbit@rabbit2',
            'queue6': 'rabbit@rabbit3'})

        # Test that we skip on second pass
        with mock.patch.object(
                self.rebalance, '_apply_policy',
                wraps=self.rebalance._apply_policy) as apply_policy:
            self.rebalance.run()
            apply_policy.assert_not_called()
