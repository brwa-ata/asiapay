from unittest.mock import MagicMock, patch

import jwt
import pytest

from asiapay import conf

from . import hooks

PRIVATE_KEY = 'test-private-key-long-enough-for-hs256'


@pytest.fixture(autouse=True)
def fresh_state():
    """Settings are cached per process; every test starts from its own."""
    conf.reset()
    hooks.calls.clear()
    yield
    conf.reset()


@pytest.fixture
def asiapay():
    """AsiaPay as the service and the callback view see it."""
    client = MagicMock()
    client.create_order.return_value = {
        'merch_order_id': 'ignored',
        'prepay_id': 'PREPAY1',
        'redirect_url': 'https://pay.example/paygate?x=1',
    }
    client.query_order.return_value = {'order_status': 'WAIT_PAY'}
    client.verify.side_effect = lambda sign: jwt.decode(
        sign, PRIVATE_KEY, algorithms=['HS256']
    )
    with (
        patch('asiapay.service.get_client', return_value=client),
        patch('asiapay.views.get_client', return_value=client),
    ):
        yield client
