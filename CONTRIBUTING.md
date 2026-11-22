# Contributing

Bug reports and pull requests are welcome. For a bug in the Home Assistant Deako integration itself (setup, entities, UI), use [Home Assistant Core](https://github.com/home-assistant/core/issues).

## Setup

1. Use Python 3.11 or newer
2. `python -m venv venv`
3. `source venv/bin/activate`
4. `pip install -r requirements.txt -r requirements_test.txt`

## Checks

CI runs these on every pull request, and they must pass:

1. `ruff check pydeako tests tests_ha`
2. `ruff format --check pydeako tests tests_ha`
3. `pylint pydeako tests`
4. `mypy pydeako tests`
5. `coverage run -m pytest`
6. `coverage report` (fails below 100%)
7. `zizmor .github` (when you change a workflow)

CI also builds the wheel and the sdist, runs the tests against the installed wheel, and runs the Home Assistant tests described below.

If you use [pre-commit](https://pre-commit.com), `pre-commit install --hook-type pre-commit --hook-type pre-push` runs the linters on commit and the tests on push, with the tools from your virtual environment.

## Tests

No test needs a Deako device, a particular network or anything outside 127.0.0.1. Please keep it that way, so everyone and CI can run the whole suite.

- `tests/fake_bridge.py` is a fake Deako Connect that speaks the [local API](docs/local-api.md) on a loopback port. `tests/test_integration.py` runs the real client against it.
- `tests/test_discovery.py` runs `DeakoDiscoverer` on a real zeroconf instance that listens on loopback only. The fake's mDNS records are put into the cache, so nothing is announced.
- `tests/test_api.py` pins the public API. Home Assistant calls it, so a change there needs a matching change in Home Assistant.
- `tests/test_properties.py` feeds generated input to the stream parser.
- `tests/test_readme_examples.py` runs the README examples.
- The remaining files in `tests/` are unit tests per module.
- `tests_ha/` runs Home Assistant's own deako integration on this library and the fake device, with only discovery replaced. Home Assistant pins its own pytest versions, so it needs a separate virtual environment on Python 3.14: `pip install . -r requirements_ha.txt`, then `pytest tests_ha`. Dependabot raises the pinned `pytest-homeassistant` with each Home Assistant release, which re-tests the library against it.

Fix a bug by first adding a test that fails because of it.

## Pull requests

Fork the repository, work on a branch, and open the pull request against `main`. Keep one topic per pull request and say what problem it solves.

## Releases

1. Open a pull request that sets `version` in `pyproject.toml`.
2. After it is merged, publish a GitHub release whose tag is exactly that version, for example `0.7.0`, with generated release notes.
3. The Release workflow checks that the tag matches, runs the whole CI on the tag and publishes the distributions that CI built to PyPI with Trusted Publishing, once a maintainer approves the `pypi` environment.
4. For Home Assistant, open a dependency bump in `home-assistant/core` that links to the comparison between the two versions, for example `https://github.com/pydeako-community/pydeako/compare/0.6.1...0.7.0`.
