# Changelog

All notable changes to this package. Versions follow
[semantic versioning](https://semver.org); projects pin a tag, so read the
entries between your tag and the new one before upgrading.

## v0.1.0

First release, extracted from the Lavender system's `asia_pay` app.

- Web (`Checkout`), mobile (`Cross-App`) and static QR (`PWA`) payments.
- Requests signed as HS256 JWTs, identical to AsiaPay's jwt.io recipe.
- Public `notify_url` callback that re-reads every order from AsiaPay.
- `ASIA_PAY_ORDER_PREFIX` names a project's orders.

Moving from the copied `asia_pay` app:

- Import from `asiapay` instead of `asia_pay`, and put `'asiapay'` in
  `INSTALLED_APPS`.
- `ASIA_PAY_RECEIPT_MODEL` is now required (it used to default to
  `api.Receipt`).
- Logs go to the `asiapay` logger instead of `custom.logger`.
