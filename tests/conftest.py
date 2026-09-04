"""Keep offline tests independent of the developer's live-provider .env.

Set defaults before test collection imports the module-level ASGI application.
Individual wire-contract tests still inject their own settings and mock clients.
Real provider campaigns belong to the explicitly invoked benchmark scripts.
"""

import os


def pytest_configure(config):
    os.environ.update(
        PROVIDER_PROFILE="fake",
        DATABASE_URL="",
        SQLITE_PATH=":memory:",
        AUTH_REQUIRED="false",
    )
