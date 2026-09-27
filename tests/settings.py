"""The smallest Django project that can host the app, for the test suite."""

SECRET_KEY = 'asiapay-tests'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'rest_framework',
    'asiapay',
    'tests.testapp',
]

DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
}

ROOT_URLCONF = 'tests.urls'

ASIA_PAY_BASE_URL = 'https://asiapay.example/apiaccess'
ASIA_PAY_APP_KEY = 'app-key'
ASIA_PAY_APP_SECRET = 'app-secret'
ASIA_PAY_JWT_PRIVATE_KEY = 'test-private-key-long-enough-for-hs256'
ASIA_PAY_APP_ID = 'APPID'
ASIA_PAY_MERCHANT_CODE = 'MERCH'
ASIA_PAY_ORDER_PREFIX = 'TEST'
ASIA_PAY_RECEIPT_MODEL = 'testapp.Receipt'
ASIA_PAY_ON_PAYMENT_COMPLETED = 'tests.hooks.record'
