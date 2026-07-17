.PHONY: test-exact test-fast test-domain test-bridge test-transport test-full

# Usage: make test-exact T=tests/test_quest_inventory_guard.py::test_name
test-exact:
	.venv/bin/python -m pytest -q -m "" $(T)

test-fast:
	.venv/bin/python -m pytest -q

test-domain:
	.venv/bin/python -m pytest -q -m domain

test-bridge:
	.venv/bin/python -m pytest -q -m "integration and not transport" tests/test_page_bridge_js.py tests/test_*_js.py

test-transport:
	.venv/bin/python -m pytest -q -m transport

test-full:
	.venv/bin/python -m pytest -q -m "" --durations=30
