# Contributing

To get setup in the environment and run the tests, take the following steps:

```bash
python3 -m venv env
source env/bin/activate
pip install -r requires/testing.txt
./bootstrap  # Will setup docker containers for testing
bin/test.sh  # Runs flake8, mypy, && nose
```

## Code Style

This project uses a strict pep-8 code style with Google style imports. Pull
requests that do not pass the configured flake8 tests will likely to be closed
without review.

## Test Coverage

Pull requests that make changes or additions that are not covered by tests
will likely be closed without review.

