"""Completion hooks the tests point ASIA_PAY_ON_PAYMENT_COMPLETED at."""

calls = []


def record(receipt, status_value):
    calls.append((receipt.pk, status_value))


def explode(receipt, status_value):
    raise RuntimeError('a bug in the project hook')
