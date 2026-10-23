from patch_agent import cost_report, patch_agent

# conftest.py is loaded by pytest before the tests, so every agent run in the
# test session is tracked.
patch_agent()


def pytest_terminal_summary(terminalreporter):
    """Print the cost report after the test results."""
    terminalreporter.write_line(cost_report())
