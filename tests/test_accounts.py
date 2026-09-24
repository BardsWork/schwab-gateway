"""Tests for /accounts, /accounts/{hash}/orders and /accounts/{hash}/transactions."""
from datetime import date, datetime, time
from unittest.mock import MagicMock

import pytest
from schwab.client import Client

import gateway.client_state as cs


def _mock_resp(data, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception("boom")
    else:
        resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


ACCOUNTS = [{"accountNumber": "12345678", "hashValue": "HASH1"}]
ORDERS = [{"orderId": 1, "status": "FILLED"}, {"orderId": 2, "status": "FILLED"}]
TRANSACTIONS = [{"activityId": 9, "type": "TRADE"}]


class TestAccounts:
    def test_returns_accounts(self, test_app):
        cs._client.get_account_numbers.return_value = _mock_resp(ACCOUNTS)
        r = test_app.get("/accounts")
        assert r.status_code == 200
        assert r.json() == {"count": 1, "data": ACCOUNTS}

    def test_schwab_error_returns_502(self, test_app):
        cs._client.get_account_numbers.return_value = _mock_resp({}, status_code=401)
        assert test_app.get("/accounts").status_code == 502

    def test_no_client_returns_503(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        assert test_app.get("/accounts").status_code == 503


class TestOrders:
    def test_returns_orders_with_explicit_range(self, test_app):
        cs._client.get_orders_for_account.return_value = _mock_resp(ORDERS)
        r = test_app.get(
            "/accounts/HASH1/orders",
            params={"from_date": "2024-01-02", "to_date": "2024-01-31", "status": "FILLED"},
        )
        assert r.status_code == 200
        assert r.json() == {"count": 2, "data": ORDERS}
        cs._client.get_orders_for_account.assert_called_once_with(
            "HASH1",
            from_entered_datetime=datetime.combine(date(2024, 1, 2), time.min),
            to_entered_datetime=datetime.combine(date(2024, 1, 31), time.max),
            status=Client.Order.Status.FILLED,
            max_results=None,
        )

    def test_defaults_to_last_60_days(self, test_app):
        cs._client.get_orders_for_account.return_value = _mock_resp([])
        test_app.get("/accounts/HASH1/orders")
        kwargs = cs._client.get_orders_for_account.call_args.kwargs
        assert (kwargs["to_entered_datetime"].date() - kwargs["from_entered_datetime"].date()).days == 60
        assert kwargs["status"] is None

    def test_invalid_status_returns_400(self, test_app):
        r = test_app.get("/accounts/HASH1/orders", params={"status": "BOGUS"})
        assert r.status_code == 400

    def test_invalid_date_returns_422(self, test_app):
        r = test_app.get("/accounts/HASH1/orders", params={"from_date": "nope"})
        assert r.status_code == 422

    def test_schwab_error_returns_502(self, test_app):
        cs._client.get_orders_for_account.return_value = _mock_resp({}, status_code=500)
        assert test_app.get("/accounts/HASH1/orders").status_code == 502

    def test_no_client_returns_503(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        assert test_app.get("/accounts/HASH1/orders").status_code == 503


class TestTransactions:
    def test_returns_transactions(self, test_app):
        cs._client.get_transactions.return_value = _mock_resp(TRANSACTIONS)
        r = test_app.get(
            "/accounts/HASH1/transactions",
            params={"from_date": "2024-01-02", "to_date": "2024-01-31", "symbol": "spy"},
        )
        assert r.status_code == 200
        assert r.json() == {"count": 1, "data": TRANSACTIONS}
        cs._client.get_transactions.assert_called_once_with(
            "HASH1",
            start_date=datetime.combine(date(2024, 1, 2), time.min),
            end_date=datetime.combine(date(2024, 1, 31), time.max),
            transaction_types=[Client.Transactions.TransactionType.TRADE],
            symbol="SPY",
        )

    def test_defaults_to_last_60_days(self, test_app):
        cs._client.get_transactions.return_value = _mock_resp([])
        test_app.get("/accounts/HASH1/transactions")
        kwargs = cs._client.get_transactions.call_args.kwargs
        assert kwargs["end_date"] == datetime.combine(date.today(), time.max)
        assert (kwargs["end_date"].date() - kwargs["start_date"].date()).days == 60

    def test_to_date_only_anchors_start_to_to_date(self, test_app):
        cs._client.get_transactions.return_value = _mock_resp([])
        test_app.get("/accounts/HASH1/transactions", params={"to_date": "2024-03-31"})
        kwargs = cs._client.get_transactions.call_args.kwargs
        assert kwargs["start_date"] == datetime.combine(date(2024, 1, 31), time.min)
        assert kwargs["end_date"] == datetime.combine(date(2024, 3, 31), time.max)

    def test_multiple_types(self, test_app):
        cs._client.get_transactions.return_value = _mock_resp([])
        test_app.get("/accounts/HASH1/transactions", params={"types": "TRADE,DIVIDEND_OR_INTEREST"})
        types = cs._client.get_transactions.call_args.kwargs["transaction_types"]
        assert types == [
            Client.Transactions.TransactionType.TRADE,
            Client.Transactions.TransactionType.DIVIDEND_OR_INTEREST,
        ]

    def test_invalid_type_returns_400(self, test_app):
        r = test_app.get("/accounts/HASH1/transactions", params={"types": "BOGUS"})
        assert r.status_code == 400

    def test_schwab_error_returns_502(self, test_app):
        cs._client.get_transactions.return_value = _mock_resp({}, status_code=500)
        assert test_app.get("/accounts/HASH1/transactions").status_code == 502

    def test_no_client_returns_503(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        assert test_app.get("/accounts/HASH1/transactions").status_code == 503
