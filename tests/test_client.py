"""The raw client: signing, the token, and reading AsiaPay's answers."""

import base64
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import jwt
import pytest

from asiapay.client import TRADE_CROSS_APP, AsiaPayClient
from asiapay.exceptions import AsiaPayAPIError, AsiaPayAuthError

from .conftest import PRIVATE_KEY


def make_client(token='Bearer T'):
    client = AsiaPayClient(
        'https://asiapay.example/apiaccess/',
        'app-key',
        'app-secret',
        PRIVATE_KEY,
        'APPID',
        'MERCH',
    )
    if token:
        client._token = token
        client._token_expiry = 10**12
    return client


def answer(status_code=200, **data):
    response = MagicMock(status_code=status_code, content=b'x', text='x')
    response.json.return_value = data
    return response


def ok(**biz_content):
    return answer(result='SUCCESS', biz_content=biz_content)


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def test_the_whole_body_is_signed_with_the_private_key():
    with patch('asiapay.client.requests.post', return_value=ok()) as post:
        make_client().create_order(
            'ORDER1',
            15000.0,
            title='t',
            notify_url='n',
            redirect_url='r',
            trade_type=TRADE_CROSS_APP,
        )

    body = post.call_args.kwargs['json']
    signed = jwt.decode(body.pop('sign'), PRIVATE_KEY, algorithms=['HS256'])
    assert signed == body
    assert list(body) == [
        'biz_content',
        'method',
        'nonce_str',
        'sign_type',
        'timestamp',
        'version',
    ]
    assert body['method'] == 'payment.preorder'
    assert body['sign_type'] == 'JWTSecret'
    assert body['timestamp'].isdigit()
    assert body['biz_content']['total_amount'] == '15000'
    assert body['biz_content']['trade_type'] == 'Cross-App'
    assert body['biz_content']['appid'] == 'APPID'
    assert body['biz_content']['merch_code'] == 'MERCH'


def test_sign_matches_what_jwt_io_produces():
    """AsiaPay support's recipe: jwt.io, HS256, body as payload, key as text.

    Rebuilt here by hand -- the header jwt.io uses, the body minified in its
    own key order, HMAC-SHA256 with the key *not* base64-decoded -- so a change
    in how the client serialises the body cannot slip through.
    """
    body = {
        'biz_content': {'appid': 'APPID', 'total_amount': '250'},
        'method': 'payment.preorder',
        'nonce_str': 'N1',
        'sign_type': 'JWTSecret',
        'timestamp': '1790504662',
        'version': '1.0',
    }
    header = b64url(b'{"alg":"HS256","typ":"JWT"}')
    payload = b64url(json.dumps(body, separators=(',', ':')).encode())
    signature = hmac.new(
        PRIVATE_KEY.encode(), f'{header}.{payload}'.encode(), hashlib.sha256
    ).digest()

    assert make_client().sign(body) == f'{header}.{payload}.{b64url(signature)}'


def test_calls_carry_the_app_key_and_the_token_as_given():
    with patch('asiapay.client.requests.post', return_value=ok()) as post:
        make_client().query_order('ORDER1')

    url = post.call_args.args[0]
    headers = post.call_args.kwargs['headers']
    assert url == (
        'https://asiapay.example/apiaccess'
        '/payment/gateway/payment/v1/merchant/queryOrder'
    )
    assert headers == {'X-APP-Key': 'app-key', 'Authorization': 'Bearer T'}


def test_a_refusal_with_a_non_error_status_still_raises():
    """The sandbox answers an unknown order with HTTP 299."""
    refusal = answer(299, result='FAIL', msg='not found')
    with (
        patch('asiapay.client.requests.post', return_value=refusal),
        pytest.raises(AsiaPayAPIError) as caught,
    ):
        make_client().query_order('NOPE')

    assert caught.value.status_code == 299
    assert caught.value.payload['msg'] == 'not found'
    assert 'not found' in str(caught.value)


def test_the_token_is_fetched_once_and_reused():
    token = answer(
        token='Bearer NEW',
        effectiveDate='20260101100000',
        expirationDate='20260101110000',
    )
    with patch('asiapay.client.requests.post', side_effect=[token, ok(), ok()]) as post:
        client = make_client(token=None)
        client.query_order('A')
        client.query_order('B')

    token_call, first, second = post.call_args_list
    assert token_call.args[0].endswith('/payment/gateway/payment/v1/token')
    assert token_call.kwargs['json'] == {'appSecret': 'app-secret'}
    assert first.kwargs['headers']['Authorization'] == 'Bearer NEW'
    assert second.kwargs['headers']['Authorization'] == 'Bearer NEW'


def test_an_expired_token_is_refreshed_once_and_the_call_retried():
    fresh = answer(token='Bearer FRESH')
    with patch(
        'asiapay.client.requests.post',
        side_effect=[answer(401), fresh, ok(order_status='PAY_SUCCESS')],
    ) as post:
        result = make_client(token='Bearer OLD').query_order('A')

    assert result == {'order_status': 'PAY_SUCCESS'}
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer FRESH'


def test_a_failed_token_call_raises_an_auth_error():
    with (
        patch('asiapay.client.requests.post', return_value=answer(403)),
        pytest.raises(AsiaPayAuthError),
    ):
        make_client(token=None).query_order('A')


def test_missing_credentials_are_refused_up_front():
    with pytest.raises(AsiaPayAuthError):
        AsiaPayClient('https://x', 'key', '', PRIVATE_KEY, 'APPID', 'MERCH')


def test_refund_sends_the_order_and_the_reason():
    with patch('asiapay.client.requests.post', return_value=ok()) as post:
        make_client().refund('ORDER1', 'R1', 'Out of stock')

    body = post.call_args.kwargs['json']
    assert post.call_args.args[0].endswith('/merchant/refund')
    assert body['method'] == 'payment.refund'
    assert body['biz_content']['refund_request_no'] == 'R1'
    assert body['biz_content']['refund_reason'] == 'Out of stock'
