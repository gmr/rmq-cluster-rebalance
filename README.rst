rmq-cluster-rebalance
=====================

CLI application for rebalancing queues in a RabbitMQ cluster

|Version| |Status| |Coverage| |License| |Docs|

Installation
------------

.. code-block:: bash

    pip install rmq-cluster-rebalance

Why?
----
When you specify the `queue master location <queue_master_location>`_ in RabbitMQ
with value of `min-masters`, RabbitMQ will attempt to balance the distribution
of the master locations for queues across the cluster. However, when you restart
(or upgrade) a RabbitMQ node in a cluster with HA queues, the master location for
a queue will move to a different node. Given enough such events and depending on
your HA policies, you can end up with an uneven distribution of queues with a master
location on a minority of nodes. In this scenario, the nodes in cluster may not be
utilized as evenly, with higher CPU, memory, disk, and network utilization on nodes
who are carrying the larger quantity of master nodes.

Rebalancing queues across nodes can also be useful when adding nodes to a cluster
with no `queue_master_locator` configuration set (which uses the default value of
`client-local`).

Methodology
-----------
The application use the RabbitMQ management UI to iterate through each queue
in the cluster. The queue is inspected and a policy named `rmq-cluster-rebalance`
is created using the existing policy configuration used by the queue adding
`ha-mode: all`. Once the queue has fully replicated across all nodes, the same
policy is replaced with a new policy that using `ha-mode: nodes` with the new
master node specified in `ha-params`. When the queue has fully moved, the policy
will is then removed and `rmq-cluster-rebalance` will move on to the next queue.

Nodes are assigned in a simple round-robin ordering. If a queue is already living
on the node where it would be assigned to, it will be skipped and no work is
performed on that queue.

Warning
-------
This approach should be safe for production use without interruption of publishers
or consumers. While it has worked for me without issue, your mileage may vary.

CLI Usage
---------

.. code:: bash

    usage: rmq-cluster-rebalance [-h] [-u USERNAME] [-p PASSWORD] [--vhost VHOST]
                                 [-L LOG_FILE] [-v] [--debug] [--version]
                                 [URL]

    Rebalances the queues in a RabbitMQ cluster

    positional arguments:
      URL                   The RabbitMQ Management API base URL (default:
                            http://localhost:15672)

    optional arguments:
      -h, --help            show this help message and exit
      -u USERNAME, --username USERNAME
                            The RabbitMQ Management API username (default: guest)
      -p PASSWORD, --password PASSWORD
                            The RabbitMQ Management API password (default: guest)
      --vhost VHOST         The RabbitMQ VHost to use (default: /)
      --version             output version information, then exit

    Logging options:
      -L LOG_FILE, --log-file LOG_FILE
                            Log to the specified filename (default: STDOUT)
      -v, --verbose         Increase output verbosity (default: False)
      --debug               Extra verbose debug logging (default: False)


.. _queue_master_location: https://www.rabbitmq.com/ha.html#master-migration-data-locality

.. |Version| image:: https://img.shields.io/pypi/v/rmq-cluster-rebalance.svg?
   :target: https://pypi.python.org/pypi/rmq-cluster-rebalance
   :alt: Package Version

.. |Status| image:: https://img.shields.io/circleci/build/gh/gmr/rmq-cluster-rebalance/master.svg?token=
   :target: https://circleci.com/gh/gmr/rmq-cluster-rebalance/tree/master
   :alt: Build Status

.. |Coverage| image:: https://codecov.io/gh/gmr/rmq-cluster-rebalance/branch/master/graph/badge.svg
   :target: https://codecov.io/github/gmr/rmq-cluster-rebalance?branch=master
   :alt: Code Coverage

.. |License| image:: https://img.shields.io/pypi/l/rmq-cluster-rebalance.svg?
   :target: https://github.com/gmr/rmq-cluster-rebalance/blob/master/LICENSE
   :alt: BSD

.. |Docs| image:: https://img.shields.io/readthedocs/rmq-cluster-rebalance.svg?
   :target: https://rmq-cluster-rebalance.readthedocs.io/
   :alt: Documentation Status