# Contributing

To get setup in the environment and run the tests, take the following steps:

```bash
python3 -m venv env
. env/bin/activate
pip install -r requires/testing.txt

nosetests
flake8
```

## Test Coverage

Pull requests that make changes or additions that are not covered by tests
will likely be closed without review.
