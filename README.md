# asiapay

A reusable Django package that takes online payments through the
**AsiaPay** payment gateway (<https://asiapay.iq/integration>) in any project
whose *receipt* model has:

- a text field to hold our AsiaPay order id (default `ref_no`)
- a boolean field that stays `False` until AsiaPay confirms the payment
  (default `is_completed`)

It works for all three ways AsiaPay can be paid:

| Where the customer pays | AsiaPay trade type | What the customer sees |
| --- | --- | --- |
| Website | `Checkout` | AsiaPay's payment page with a dynamic QR code |
| Mobile app | `Cross-App` | The AsiaPay app opens and asks them to confirm |
| Static QR (kiosk, counter, printed) | `PWA` | A QR code you show them, scanned with the AsiaPay app |

`asiapay.client` has **no Django dependency**. If all you need is a raw
AsiaPay API wrapper, `from asiapay.client import AsiaPayClient` works in any
Python code, Django or not.

---

## Contents

1. [How a payment works](#how-a-payment-works)
2. [What is in the package](#what-is-in-the-package)
3. [Install into a new project](#install-into-a-new-project)
4. [Credentials](#credentials)
5. [Settings reference](#settings-reference)
6. [Web, mobile and static QR](#web-mobile-and-static-qr)
7. [Python API](#python-api)
8. [The callback (`notify_url`)](#the-callback-notify_url)
9. [How the request is signed](#how-the-request-is-signed)
10. [AsiaPay API contract](#asiapay-api-contract)
11. [Logging](#logging)
12. [Testing](#testing)
13. [Troubleshooting](#troubleshooting)
14. [Updating and releasing](#updating-and-releasing)
15. [Known limits](#known-limits)

---

## How a payment works

```
Your server                         AsiaPay                        Customer
-----------                         -------                        --------
1. Create a pending receipt
   (is_completed = False)
2. service.start_payment(receipt) ─► preOrder
   store merch_order_id in ref_no ◄─ redirect_url, prepay_id
3. Return redirect_url ─────────────────────────────────────────►  opens it / scans it
                                                                   pays in AsiaPay
4.                                  AsiaPay calls notify_url ──►   (redirected back to
   api/asia-pay/callback/                                           your redirect_url)
   └─ re-reads the order (queryOrder), never trusts the body
   └─ PAY_SUCCESS → is_completed = True, your hook runs once
5. Client polls your status endpoint → service.sync_status()
   (same re-read; safe to race the callback)
6. Client sees is_completed = True → continues (e.g. places the order)
```

Three rules hold the whole way through:

- **Nothing AsiaPay sends us is trusted.** The callback only *names* an order.
  The status is always read back from AsiaPay with `queryOrder` before a
  receipt changes.
- **Coming back to `redirect_url` does not mean paid.** The customer can come
  back without paying. Always check the status before you continue.
- **A payment is applied once.** The receipt is locked and moved from pending
  to completed once. The callback and the poll can arrive together; your hook
  still runs a single time.

---

## What is in the package

| Module (`asiapay.…`) | What it does |
| --- | --- |
| `client.py` | `AsiaPayClient`: token, signing, `create_order`, `query_order`, `refund`. Only needs `requests` and `PyJWT` |
| `exceptions.py` | `AsiaPayError`, `AsiaPayAuthError`, `AsiaPayAPIError` |
| `conf.py` | Reads the `ASIA_PAY_*` settings once and builds one shared client per process |
| `service.py` | Connects the client to your receipt model: `start_payment`, `sync_status`, `apply_status`, `handle_callback` |
| `views.py` | `AsiaPayCallbackView`, the public endpoint AsiaPay calls |
| `urls.py` | Routes `callback/` |

The app does **not** ship an endpoint to *start* a payment. Starting one needs
a receipt first, and only your project knows how to create a receipt (which
customer, which amount, what fees). You write that endpoint; see step 5 below.

---

## Install into a new project

### 1. Install the package

Install from GitHub, pinned to a release tag (see
[Releases](https://github.com/brwa-ata/asiapay/tags) for the latest):

```bash
# uv
uv add git+https://github.com/brwa-ata/asiapay --tag v0.1.0

# pip
pip install "asiapay @ git+https://github.com/brwa-ata/asiapay@v0.1.0"
```

With uv this lands in `pyproject.toml` as:

```toml
[project]
dependencies = ["asiapay"]

[tool.uv.sources]
asiapay = { git = "https://github.com/brwa-ata/asiapay", tag = "v0.1.0" }
```

With pip, add the same line to `requirements.txt`:

```
asiapay @ git+https://github.com/brwa-ata/asiapay@v0.1.0
```

It pulls in what it needs: Django 4.2+, Django REST framework 3.14+,
PyJWT 2.5+ and requests 2.28+, on Python 3.10+.

### 2. Register it and route the callback

```python
# settings.py
INSTALLED_APPS += ['asiapay']

# urls.py
path('api/asia-pay/', include('asiapay.urls')),
```

The callback is then served at `/api/asia-pay/callback/`.

### 3. Add the credentials

Put them in `.env` (never in code), then read them in settings:

```bash
# .env
ASIA_PAY_BASE_URL=https://apitest.asiapay.iq:5443/apiaccess
ASIA_PAY_APP_KEY=...
ASIA_PAY_APP_SECRET=...
ASIA_PAY_JWT_PRIVATE_KEY=...
ASIA_PAY_APP_ID=...
ASIA_PAY_MERCHANT_CODE=...
```

```python
# settings.py (python-decouple shown; os.environ works the same)
from decouple import config

ASIA_PAY_BASE_URL = config('ASIA_PAY_BASE_URL')
ASIA_PAY_APP_KEY = config('ASIA_PAY_APP_KEY')
ASIA_PAY_APP_SECRET = config('ASIA_PAY_APP_SECRET')
ASIA_PAY_JWT_PRIVATE_KEY = config('ASIA_PAY_JWT_PRIVATE_KEY')
ASIA_PAY_APP_ID = config('ASIA_PAY_APP_ID')
ASIA_PAY_MERCHANT_CODE = config('ASIA_PAY_MERCHANT_CODE')

# Not a secret: a short code that starts every order id AsiaPay sees,
# so this project's orders read SHOP<receipt id>T<unix time>.
ASIA_PAY_ORDER_PREFIX = 'SHOP'
```

The URL above is the **sandbox**. AsiaPay gives you the production URL
together with the production credentials.

### 4. Point the app at your receipt model

The model is required; the two field names default to the values shown:

```python
ASIA_PAY_RECEIPT_MODEL = 'shop.Receipt'         # required: app_label.ModelName
ASIA_PAY_RECEIPT_REF_FIELD = 'ref_no'           # CharField, 30+ chars
ASIA_PAY_RECEIPT_STATUS_FIELD = 'is_completed'  # BooleanField

# Optional: run something once when a payment completes
ASIA_PAY_ON_PAYMENT_COMPLETED = 'shop.payments.on_asia_pay_completed'
```

What the app reads from your receipt:

| Field | Required | Used for |
| --- | --- | --- |
| the ref field (`ref_no`) | yes | Stores our `merch_order_id`, and finds the receipt when the callback arrives |
| the status field (`is_completed`) | yes | `False` while pending, `True` once AsiaPay reports `PAY_SUCCESS` |
| `amount` | if you don't pass `amount=` | The amount charged |
| `invoice_no` | no | Builds the order title (`Payment RV123`); falls back to `Payment #<pk>` |

The hook is any function with this signature:

```python
def on_asia_pay_completed(receipt, status_value):
    # status_value is 'PAY_SUCCESS'
    # e.g. top up a balance, mark an invoice paid, send a notification
    ...
```

It runs **once**, inside the same database transaction that marks the receipt
completed. If it raises, the error is logged to the `asiapay` logger and the
payment still counts as completed. A bug in your hook must never lose a
payment, so make sure that logger goes somewhere you read (see
[Logging](#logging)).

### 5. Write the "start payment" and "status" endpoints

The app leaves these to you. Here is a minimal version to adapt:

```python
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from asiapay import service
from asiapay.client import TRADE_CHECKOUT, TRADE_TYPES
from asiapay.conf import get_conf
from asiapay.exceptions import AsiaPayError

from shop.models import Receipt


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def start_asia_pay(request):
    trade_type = request.data.get('trade_type') or TRADE_CHECKOUT
    if trade_type not in TRADE_TYPES:
        return Response({'message': 'Invalid trade type'}, status=422)

    conf = get_conf()
    redirect_url = request.data.get('redirect_url') or conf.redirect_url
    if not redirect_url:
        return Response({'message': 'redirect_url is required'}, status=422)

    receipt = Receipt.objects.create(
        customer=request.user,
        amount=request.data['amount'],
        is_completed=False,
    )
    try:
        payment = service.start_payment(
            receipt,
            notify_url=conf.notify_url
            or request.build_absolute_uri('/api/asia-pay/callback/'),
            redirect_url=redirect_url,
            trade_type=trade_type,
        )
    except AsiaPayError as exc:
        # returning a Response does not roll back by itself
        transaction.set_rollback(True)
        return Response({'message': f'AsiaPay payment failed: {exc}'}, status=422)

    return Response(
        {
            'receipt_id': receipt.pk,
            'ref_no': receipt.ref_no,
            'trade_type': trade_type,
            'redirect_url': payment.get('redirect_url'),
        },
        status=201,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def asia_pay_status(request, receipt_id):
    # scope to the caller, so nobody can poll someone else's payment
    receipt = get_object_or_404(Receipt, pk=receipt_id, customer=request.user)
    if not receipt.is_completed:
        try:
            service.sync_status(receipt)
        except AsiaPayError:
            pass  # AsiaPay is briefly unavailable; the client polls again
        receipt.refresh_from_db()
    return Response({'receipt_id': receipt.pk, 'is_completed': receipt.is_completed})
```

Why each piece is there:

- **Receipt first, then AsiaPay.** `start_payment` needs the receipt's `pk`
  to build a unique order id.
- **`transaction.atomic` + `set_rollback`.** If AsiaPay refuses the order, the
  pending receipt disappears with it instead of lingering.
- **`notify_url` falls back to the request host.** That way, dev, staging and
  production each receive their own callbacks without extra settings.
- **The status endpoint.** AsiaPay cannot reach `localhost`, so during local
  development the poll is the only way a payment completes. In production it
  also covers a callback that arrives late or never.

---

## Credentials

AsiaPay sends five values when you sign up. Each one has a single job:

| Setting | AsiaPay calls it | Where it is used |
| --- | --- | --- |
| `ASIA_PAY_APP_KEY` | App Key | `X-APP-Key` header on every call |
| `ASIA_PAY_APP_SECRET` | App Secret | Body of the token call (`{"appSecret": ...}`) |
| `ASIA_PAY_JWT_PRIVATE_KEY` | JWT private key | Signs every request body (see [signing](#how-the-request-is-signed)) |
| `ASIA_PAY_APP_ID` | App ID | `biz_content.appid` |
| `ASIA_PAY_MERCHANT_CODE` | Merchant code | `biz_content.merch_code` |

Sandbox and production credentials are different. Switching environments
means changing all five **and** `ASIA_PAY_BASE_URL` together.

---

## Settings reference

| Setting | Default | Purpose |
| --- | --- | --- |
| `ASIA_PAY_BASE_URL` | *(blank)* | Gateway root, e.g. `https://apitest.asiapay.iq:5443/apiaccess` |
| `ASIA_PAY_APP_KEY` | — | See [Credentials](#credentials) |
| `ASIA_PAY_APP_SECRET` | — | See [Credentials](#credentials) |
| `ASIA_PAY_JWT_PRIVATE_KEY` | — | See [Credentials](#credentials) |
| `ASIA_PAY_APP_ID` | — | See [Credentials](#credentials) |
| `ASIA_PAY_MERCHANT_CODE` | — | See [Credentials](#credentials) |
| `ASIA_PAY_CURRENCY` | `IQD` | `trans_currency` sent with every order |
| `ASIA_PAY_TIMEOUT` | `30` | HTTP timeout to AsiaPay, in seconds |
| `ASIA_PAY_TIMEOUT_EXPRESS` | `30m` | How long an order stays payable |
| `ASIA_PAY_ORDER_PREFIX` | *(blank)* | Starts every `merch_order_id`; see [order ids](#order-ids) |
| `ASIA_PAY_NOTIFY_URL` | *(blank)* | Absolute callback URL. Blank = your view builds `<host>/api/asia-pay/callback/` |
| `ASIA_PAY_REDIRECT_URL` | *(blank)* | Fallback page to send the customer back to, when the client sends none |
| `ASIA_PAY_RECEIPT_MODEL` | — (required) | Your receipt model, as `app_label.ModelName` |
| `ASIA_PAY_RECEIPT_REF_FIELD` | `ref_no` | Field that stores the `merch_order_id` |
| `ASIA_PAY_RECEIPT_STATUS_FIELD` | `is_completed` | Boolean field set to `True` on success |
| `ASIA_PAY_ON_PAYMENT_COMPLETED` | *(blank)* | Dotted path to `fn(receipt, status_value)`, run once on success |

`ASIA_PAY_NOTIFY_URL` and `ASIA_PAY_REDIRECT_URL` are fallbacks for *your*
view to read from `get_conf()`. `service.start_payment()` itself always takes
both URLs as arguments.

Settings are read **once per process** and cached along with the client.
After changing them, restart the server; in tests, call `asiapay.conf.reset()`.

### Order ids

`start_payment` names each AsiaPay order
`<ASIA_PAY_ORDER_PREFIX><receipt pk>T<unix time>`, e.g. `SHOP123T1790504662`,
and stores it in the receipt's ref field. That id is what you see in
AsiaPay's merchant portal, and what `query_order` and `refund` take.

- **The prefix** tells your orders apart from other projects' orders on the
  same merchant account, and makes them easy to spot in AsiaPay's reports.
  Keep it short, letters and digits only.
- **The timestamp** keeps the id unique when several databases (local,
  staging, production) share one sandbox merchant and so reuse the same
  receipt ids.

---

## Web, mobile and static QR

Pick the trade type from where the customer is paying. Every trade type
returns its result in the same `redirect_url` field, but what the client does
with it differs. The shapes below were checked against the sandbox.

### Website: `Checkout`

`redirect_url` is AsiaPay's payment page:

```
https://apitest.asiapay.iq:5443/payment/web/paygate?appid=…&prepay_id=…&sign=…&trade_type=Checkout&language=en
```

1. Send `redirect_url` = a page on your site, e.g.
   `https://shop.example/checkout/asia-pay-return`.
2. Send the browser to the returned `redirect_url` (`window.location.href = …`).
3. The customer pays; AsiaPay sends them back to your page.
4. That page polls your status endpoint until `is_completed` is `true`, then
   continues. If it never turns true, the customer backed out.

### Mobile app: `Cross-App`

`redirect_url` is an AsiaPay "middle page" that switches the phone to the
AsiaPay app (`asiapay://h5checkout` on both Android and iOS):

```
https://apitest.asiapay.iq:5443/demo/middle_page/index.html?businessType=CrossAppPay&params=<base64 JSON>
```

1. Send `redirect_url` = a link that opens your app again. A universal link or
   app link (`https://…`) is the safest choice. A custom scheme
   (`myapp://payment/asia-pay`) may also work; test it in the sandbox first.
2. Open the returned `redirect_url` in the **system browser**, not an in-app
   WebView, because a WebView may be unable to hand off to the AsiaPay app:
   - Flutter: `launchUrl(uri, mode: LaunchMode.externalApplication)`
   - React Native: `Linking.openURL(url)`
3. The customer confirms in the AsiaPay app and returns to yours.
4. Poll your status endpoint when the app comes back to the foreground, and
   keep polling for a short while, since the callback may land a few seconds
   later.

### Static QR: `PWA`

`redirect_url` is a payment string to show as a QR code (the sandbox returns
a placeholder `pay.com` host):

```
https://pay.com?tradeType=PWA&appId=…&merchCode=…&prepayId=…&payToken=PWA:…
```

1. Render `redirect_url` as a QR code on screen or on paper.
2. The customer scans it with the AsiaPay app and pays.
3. Poll your status endpoint every few seconds until `is_completed` is `true`
   or `ASIA_PAY_TIMEOUT_EXPRESS` has passed.

### Polling, for every trade type

- Poll every **3–5 seconds**. Stop once `is_completed` is `true`, or once the
  order's payable window (`ASIA_PAY_TIMEOUT_EXPRESS`, 30 minutes by default)
  has passed.
- An unpaid order reads `WAIT_PAY`, then `PAY_FAILED` once its window has
  passed; its receipt simply stays pending. Show a "Try again" button; each
  retry starts a fresh payment and a fresh receipt.

---

## Python API

### `asiapay.service` (needs Django)

```python
from asiapay import service

# Create an AsiaPay order for a pending receipt.
# Sets receipt.<ref field> = merch_order_id and receipt.<status field> = False.
payment = service.start_payment(
    receipt,
    notify_url='https://shop.example/api/asia-pay/callback/',
    redirect_url='https://shop.example/checkout/asia-pay-return',
    trade_type='Checkout',          # 'Checkout' | 'Cross-App' | 'PWA'
    amount=None,                    # default: receipt.amount
    title=None,                     # default: 'Payment <invoice_no>'
)
# payment == {'merch_order_id': ..., 'prepay_id': ..., 'redirect_url': ...}

# Re-read the order from AsiaPay and complete the receipt on PAY_SUCCESS.
details = service.sync_status(receipt)
# details == {'order_status': 'WAIT_PAY' | 'PAY_SUCCESS' | ..., 'trans_id': ..., ...}

# What the callback view calls; returns the receipt, or None if unknown.
service.handle_callback(merch_order_id)
```

Pass `amount=` when the customer is charged something other than
`receipt.amount`, e.g. the amount plus a fee they pay on top.

### `asiapay.client.AsiaPayClient` (no Django)

```python
from asiapay.conf import get_client        # inside Django: the shared client
client = get_client()

# or standalone, anywhere:
from asiapay.client import AsiaPayClient
client = AsiaPayClient(
    base_url, app_key, app_secret, private_key, app_id, merchant_code,
    currency='IQD', timeout=30,
)

client.create_order(
    'ORDER123', 25000,
    title='Order 123', notify_url='https://…', redirect_url='https://…',
    trade_type='Checkout', timeout_express='30m',
)
client.query_order('ORDER123')                      # -> biz_content
client.refund('ORDER123', 'REFUND-1', 'Out of stock')  # -> biz_content, see refund_status
client.sign(body)                                   # -> JWT string
client.verify(sign)                                 # -> decoded dict, raises on a bad sign
```

Constants: `TRADE_CHECKOUT`, `TRADE_CROSS_APP`, `TRADE_PWA`, `TRADE_TYPES`,
`PAY_SUCCESS`.

The access token is fetched when first needed and cached for as long as
AsiaPay says it is valid, minus 60 seconds. It is shared by every thread in
the process. A `401` fetches a new token and retries once.

### Exceptions

All of them derive from `AsiaPayError`, so `except AsiaPayError` catches
everything:

| Exception | Raised when |
| --- | --- |
| `AsiaPayAuthError` | A setting is missing, or the token call fails |
| `AsiaPayAPIError` | A call fails, or answers with `result` other than `SUCCESS`. Carries `.status_code` and `.payload` (AsiaPay's full answer) |

---

## The callback (`notify_url`)

`/api/asia-pay/callback/` accepts `GET` and `POST`, needs no authentication,
and is safe to expose publicly: it reads an order id and nothing else.

AsiaPay does not document the notification body, so the view looks for the
order id in all three places it could be:

1. `biz_content.merch_order_id` (the shape of every other AsiaPay message)
2. a top-level `merch_order_id`
3. inside a `sign` JWT, which is accepted only if it verifies with our
   private key

It then calls `queryOrder` itself and acts on AsiaPay's answer.

| Situation | Response |
| --- | --- |
| No order id anywhere | `400 {"message": "missing merch_order_id"}` |
| AsiaPay could not be reached while re-reading | `503`, so AsiaPay retries later instead of the payment being lost |
| Known or unknown order, processed | `200 {"result": "SUCCESS", "message": "ok"}` |

An unknown order id is logged and answered with `200`, so AsiaPay does not
keep retrying an order this server never created (for example, one from
another environment sharing the sandbox merchant).

---

## How the request is signed

Every request body except the token call is signed:

1. Build the body **without** `sign`:
   `biz_content`, `method`, `nonce_str`, `sign_type: "JWTSecret"`,
   `timestamp` (Unix **seconds**, as a string), `version: "1.0"`.
2. Encode that whole body as an **HS256 JWT**, keyed with
   `ASIA_PAY_JWT_PRIVATE_KEY` **used as the plain string**. It is *not*
   base64-decoded, even though it looks like base64; the gateway refuses that.
3. Put the resulting JWT into the body as `sign` and send it.

This is exactly what AsiaPay support describes with jwt.io, and it produces
the same signature. To check a body by hand:

1. Open <https://www.jwt.io>, **JWT Encoder** tab, algorithm HS256.
2. **Payload**: paste the request body *without* `sign`.
3. **Sign JWT → Secret**: paste the private key, with **BASE64URL ENCODED
   switched off**.
4. The **Encoded JWT** is the `sign` value. It matches `client.sign(body)`
   for the same body, character for character.

---

## AsiaPay API contract

Base URL: `ASIA_PAY_BASE_URL` (sandbox `https://apitest.asiapay.iq:5443/apiaccess`).

| Call | Endpoint | `method` field |
| --- | --- | --- |
| Token | `POST /payment/gateway/payment/v1/token` | — (body is `{"appSecret": …}`, not signed) |
| Create order | `POST /payment/gateway/payment/v1/merchant/preOrder` | `payment.preorder` |
| Query order | `POST /payment/gateway/payment/v1/merchant/queryOrder` | `payment.queryorder` |
| Refund | `POST /payment/gateway/payment/v1/merchant/refund` | `payment.refund` |

**Headers.** Every call sends `X-APP-Key`. Every call except the token call
also sends `Authorization` with the token *exactly* as AsiaPay returned it;
it already starts with `Bearer `.

**Create-order body** (what the client sends):

```json
{
  "biz_content": {
    "appid": "<ASIA_PAY_APP_ID>",
    "business_type": "BuyGoods",
    "merch_code": "<ASIA_PAY_MERCHANT_CODE>",
    "merch_order_id": "SHOP123T1790504662",
    "redirect_url": "https://shop.example/checkout/asia-pay-return",
    "notify_url": "https://shop.example/api/asia-pay/callback/",
    "timeout_express": "30m",
    "title": "Payment RV123",
    "total_amount": "25000",
    "trade_type": "Checkout",
    "trans_currency": "IQD"
  },
  "method": "payment.preorder",
  "nonce_str": "0dbaa05b67844021801eb28c8b37c809",
  "sign_type": "JWTSecret",
  "timestamp": "1790504662",
  "version": "1.0",
  "sign": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…"
}
```

**Every answer** has the same envelope:

```json
{ "result": "SUCCESS", "code": "0", "msg": "success", "biz_content": { … } }
```

A refusal can arrive with a **non-error HTTP status**; the sandbox answers an
unknown order with `299`. The client therefore treats a call as successful
only when `result == "SUCCESS"`, whatever the HTTP status, and returns
`biz_content`.

**Order statuses** (`queryOrder` → `order_status`). AsiaPay documents only
`PAY_SUCCESS`; these have been seen in the sandbox:

| Status | Meaning |
| --- | --- |
| `WAIT_PAY` | Created, not paid yet |
| `PAY_SUCCESS` | Paid |
| `PAY_FAILED` | Not paid, e.g. left unpaid past `timeout_express` |

Only `PAY_SUCCESS` changes a receipt.

**Amounts.** IQD has no minor units, so the client sends whole numbers
(`"25000"`). AsiaPay reads them back with three decimals (`"25000.000"`).

---

## Logging

Everything the package logs goes to the **`asiapay`** logger: orders created,
callbacks received, receipts completed, and any error raised by your hook.
Django does not write it anywhere until you route it, e.g. to a file:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'asiapay_file': {
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'asiapay.log',
        },
    },
    'loggers': {
        'asiapay': {'handlers': ['asiapay_file'], 'level': 'INFO'},
    },
}
```

---

## Testing

### In your project

Mock AsiaPay at the client, so your tests never call the network:

```python
from unittest.mock import MagicMock, patch

fake = MagicMock()
fake.create_order.return_value = {
    'merch_order_id': 'x',
    'prepay_id': 'P1',
    'redirect_url': 'https://pay.example/paygate',
}
fake.query_order.return_value = {'order_status': 'PAY_SUCCESS'}

# the service starts and syncs payments; the view reads signed callbacks
with (
    patch('asiapay.service.get_client', return_value=fake),
    patch('asiapay.views.get_client', return_value=fake),
):
    ...  # call your start / status endpoints, or post to the callback
```

To check what is actually sent, patch one level lower instead:
`patch('asiapay.client.requests.post', ...)` and read
`post.call_args.kwargs['json']`.

Use `@override_settings(...)` together with `asiapay.conf.reset()`, or the
cached config keeps the old values.

### The package's own tests

```bash
git clone https://github.com/brwa-ata/asiapay && cd asiapay
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

They run against a minimal Django project in `tests/` (SQLite in memory, a
stand-in `Receipt` model) and never touch the network. GitHub Actions runs
them on every push, on Python 3.10 with the oldest supported dependencies and
on Python 3.13 with the newest.

### Against the sandbox

With sandbox credentials in your project's settings, this creates one real
test order per trade type and prints what AsiaPay returns. Unpaid sandbox
orders expire after `timeout_express`.

```bash
python manage.py shell -c "
import time
from asiapay.conf import get_client
from asiapay.client import TRADE_TYPES
c = get_client()
for tt in TRADE_TYPES:
    print(tt, c.create_order(f'TEST{tt[:3].upper()}{int(time.time())}', 250,
        title='Sandbox check', notify_url='https://example.com/n',
        redirect_url='https://example.com/r', trade_type=tt))
"
```

To pay a sandbox order end to end, open the Checkout `redirect_url` and use
the sandbox test account AsiaPay gives you. Your local server will not receive
the callback, so complete the receipt with your status endpoint.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ImproperlyConfigured: Set ASIA_PAY_RECEIPT_MODEL …` | No receipt model configured | Set `ASIA_PAY_RECEIPT_MODEL = 'app_label.Model'` |
| `AsiaPay client requires base_url, app_key, …` | A setting is empty | Fill all six `ASIA_PAY_*` credentials |
| `AsiaPay token request returned 4xx` | Wrong App Key / App Secret, or sandbox keys against production | Check the pair and `ASIA_PAY_BASE_URL` belong to the same environment |
| AsiaPay refuses the signature | Private key base64-decoded, wrong key, or body changed after signing | Use the key as the plain string; compare with jwt.io (see [signing](#how-the-request-is-signed)) |
| `AsiaPay payment.queryorder returned 299: …` | Order not found, often an id from another environment | Check `ref_no` was created against this `ASIA_PAY_BASE_URL` |
| Receipt stays pending in local development | AsiaPay cannot reach `localhost` | Poll your status endpoint, or set `ASIA_PAY_NOTIFY_URL` to a tunnel (ngrok, Cloudflare Tunnel) |
| Receipt stays pending in production | Customer never paid (`WAIT_PAY`, then `PAY_FAILED`), or the callback URL is unreachable | `client.query_order(ref_no)`; check the `asiapay` logger for callback lines |
| Payment completed but the side effect is missing | Your hook raised | Look for `AsiaPay payment hook … failed` in the `asiapay` logger |
| New settings are ignored | Config is cached per process | Restart the server / worker; `conf.reset()` in tests |
| Nothing is logged | The `asiapay` logger is not routed | See [Logging](#logging) |

---

## Updating and releasing

Versions follow [semantic versioning](https://semver.org): `0.1.1` fixes
something, `0.2.0` adds something, and a change that makes projects edit
their own code is called out in `CHANGELOG.md`. Every project pins a tag, so
nothing changes in a project until it chooses to upgrade.

### Updating a project to a new version

```bash
# uv: move the pin to the new tag
uv add git+https://github.com/brwa-ata/asiapay --tag v0.2.0

# pip: edit the tag in requirements.txt, then
pip install -r requirements.txt
```

Read `CHANGELOG.md` first, then deploy as usual. The package has no database
models, so an upgrade never needs a migration.

### Releasing a new version (maintainer)

1. Make the change, with a test, and check `uv run pytest` and
   `uv run ruff check .` pass.
2. Bump the version: `uv version --bump patch` (or `minor` / `major`). This
   edits `pyproject.toml`.
3. Add an entry at the top of `CHANGELOG.md`.
4. Commit, tag and push:

   ```bash
   git commit -am "Release v0.1.1"
   git tag v0.1.1
   git push origin main --tags
   ```

5. Wait for the GitHub Actions run on the tag to pass, then update the
   projects that need the change.

A pushed tag is what projects install, so never move or reuse one; fix a bad
release with a new version.

---

## Known limits

- **Amounts are whole numbers.** `client._format_amount` rounds to whole
  units, which is right for IQD. A currency with cents needs a change in the
  package first.
- **There is no failure or expiry hook.** An unpaid or expired order
  (`PAY_FAILED`) leaves its receipt pending (`is_completed = False`)
  indefinitely. Pending receipts should never count toward anything, and your
  project may want to clean up old ones.
- **Refunds do not touch the receipt.** `client.refund()` calls AsiaPay and
  returns `refund_status`. Reversing the payment in your own books is up to
  you.
- **The callback body is undocumented,** which is why the view searches three
  places for the order id and always re-reads the status from AsiaPay.
