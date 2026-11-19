import os

# API tests exercise route behavior without a MySQL-backed browser login.
# Production/development requests still pass through the authentication middleware.
os.environ["APP_ENV"] = "test"
