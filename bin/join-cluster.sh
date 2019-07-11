#!/usr/bin/env sh
set -e
/opt/rabbitmq/sbin/rabbitmqctl stop_app
/opt/rabbitmq/sbin/rabbitmqctl reset
/opt/rabbitmq/sbin/rabbitmqctl join_cluster rabbit@rabbit1
/opt/rabbitmq/sbin/rabbitmqctl start_app
