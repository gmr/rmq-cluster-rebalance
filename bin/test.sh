#!/usr/bin/env sh
set -e
source env/bin/activate
flake8 --output build/flake8/results.txt --tee --format=pylint
mypy rmq_cluster_rebalance --cobertura-xml-report build/mypy/
mv build/mypy/cobertura.xml build/mypy/results.xml
nosetests
