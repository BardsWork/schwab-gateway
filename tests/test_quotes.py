"""Tests for GET /quotes."""
from unittest.mock import MagicMock

import pytest

import gateway.client_state as cs


def _mock_resp(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


QUOTE_DATA = {
    "AAPL": {
        "assetMainType": "EQUITY",
        "assetSubType": "COE",
        "quoteType": "NBBO",
        "realtime": True,
        "ssid": 1973757747,
        "symbol": "AAPL",
        "quote": {
            "52WeekHigh": 288.62,
            "52WeekLow": 169.2101,
            "askPrice": 249.87,
            "askSize": 300,
            "bidPrice": 249.6,
            "bidSize": 3500,
            "closePrice": 248.96,
            "highPrice": 249.1999,
            "lastPrice": 249.77,
            "lowPrice": 246.0,
            "mark": 247.99,
            "netChange": 0.81,
            "netPercentChange": 0.32535347,
            "openPrice": 247.975,
            "postMarketChange": 1.78,
            "postMarketPercentChange": 0.71777088,
            "totalVolume": 88331081,
        },
        "fundamental": {
            "avg10DaysVolume": 36372459,
            "avg1YearVolume": 53038980,
            "divAmount": 1.04,
            "divYield": 0.4161,
            "eps": 7.46,
            "peRatio": 31.5756,
            "sharesOutstanding": 14681140000,
        },
        "reference": {
            "cusip": "037833100",
            "description": "APPLE INC",
            "exchange": "Q",
            "exchangeName": "NASDAQ",
            "isHardToBorrow": False,
            "isShortable": True,
            "htbRate": 0,
        },
    }
}


class TestQuotesEndpoint:

    def test_returns_envelope(self, test_app):
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp(QUOTE_DATA)

        resp = test_app.get("/quotes?symbols=SPY")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert "AAPL" in body["data"]

    def test_fundamental_fields_present(self, test_app):
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp(QUOTE_DATA)

        resp = test_app.get("/quotes?symbols=AAPL&fields=fundamental")
        assert resp.status_code == 200
        aapl = resp.json()["data"]["AAPL"]
        assert aapl["fundamental"]["peRatio"] == 31.5756
        assert aapl["fundamental"]["eps"] == 7.46

    def test_reference_has_company_name(self, test_app):
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp(QUOTE_DATA)

        resp = test_app.get("/quotes?symbols=AAPL&fields=reference")
        assert resp.status_code == 200
        ref = resp.json()["data"]["AAPL"]["reference"]
        assert ref["description"] == "APPLE INC"
        assert ref["exchangeName"] == "NASDAQ"

    def test_multiple_symbols(self, test_app):
        data = {**QUOTE_DATA, "SPY": {"symbol": "SPY", "quote": {}, "fundamental": {}, "reference": {}}}
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp(data)

        resp = test_app.get("/quotes?symbols=AAPL,SPY")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_symbols_uppercased(self, test_app):
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp(QUOTE_DATA)

        test_app.get("/quotes?symbols=aapl")
        call_args = cs._client.get_quotes.call_args
        assert "AAPL" in call_args[0][0]

    def test_invalid_field_returns_400(self, test_app):
        cs._client = MagicMock()
        resp = test_app.get("/quotes?symbols=SPY&fields=invalid")
        assert resp.status_code == 400
        assert "invalid" in resp.json()["detail"]

    def test_503_when_no_client(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        resp = test_app.get("/quotes?symbols=SPY")
        assert resp.status_code == 503

    def test_404_from_schwab(self, test_app):
        cs._client = MagicMock()
        cs._client.get_quotes.return_value = _mock_resp({}, status_code=404)

        resp = test_app.get("/quotes?symbols=XXXXXX")
        assert resp.status_code == 404

    def test_502_on_schwab_error(self, test_app):
        cs._client = MagicMock()
        mock_resp = _mock_resp({}, status_code=500)
        mock_resp.raise_for_status.side_effect = Exception("server error")
        cs._client.get_quotes.return_value = mock_resp

        resp = test_app.get("/quotes?symbols=SPY")
        assert resp.status_code == 502
