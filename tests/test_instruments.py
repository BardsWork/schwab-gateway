"""Tests for GET /instruments."""
from unittest.mock import MagicMock

import pytest

import gateway.client_state as cs


def _mock_resp(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


FUNDAMENTAL_DATA = {
    "instruments": [
        {
            "fundamental": {
                "symbol": "AAPL",
                "high52": 288.62,
                "low52": 169.2101,
                "dividendAmount": 1.04,
                "dividendYield": 0.41774,
                "peRatio": 31.45257,
                "epsTTM": 7.88457,
                "marketCap": 3655016614400,
                "beta": 1.11639,
                "sharesOutstanding": 14681140000,
            },
            "cusip": "037833100",
            "symbol": "AAPL",
            "description": "APPLE INC",
            "exchange": "NASDAQ",
            "assetType": "EQUITY",
        }
    ]
}

SEARCH_DATA = {
    "instruments": [
        {"symbol": "SPY", "description": "SPDR S&P 500 ETF Trust", "assetType": "ETF", "cusip": "78462F103", "exchange": "PACIFIC"},
        {"symbol": "SPYD", "description": "SPDR Portfolio S&P 500 High Dividend ETF", "assetType": "ETF", "cusip": "78468R507", "exchange": "PACIFIC"},
    ]
}


class TestInstrumentsEndpoint:

    def test_fundamental_returns_envelope(self, test_app):
        cs._client = MagicMock()
        cs._client.get_instruments.return_value = _mock_resp(FUNDAMENTAL_DATA)

        resp = test_app.get("/instruments?symbols=AAPL&projection=fundamental")
        assert resp.status_code == 200
        body = resp.json()
        assert body["projection"] == "fundamental"
        assert body["count"] == 1
        assert "instruments" in body["data"]
        assert body["data"]["instruments"][0]["symbol"] == "AAPL"

    def test_fundamental_has_company_name_in_description(self, test_app):
        cs._client = MagicMock()
        cs._client.get_instruments.return_value = _mock_resp(FUNDAMENTAL_DATA)

        resp = test_app.get("/instruments?symbols=AAPL&projection=fundamental")
        assert resp.json()["data"]["instruments"][0]["description"] == "APPLE INC"

    def test_symbol_search(self, test_app):
        cs._client = MagicMock()
        cs._client.get_instruments.return_value = _mock_resp(SEARCH_DATA)

        resp = test_app.get("/instruments?symbols=SP&projection=symbol-search")
        assert resp.status_code == 200
        body = resp.json()
        assert body["projection"] == "symbol-search"
        assert body["count"] == 1  # count is len(data) which is {"instruments": [...]}

    def test_multiple_symbols_comma_separated(self, test_app):
        cs._client = MagicMock()
        cs._client.get_instruments.return_value = _mock_resp(FUNDAMENTAL_DATA)

        resp = test_app.get("/instruments?symbols=AAPL,MSFT&projection=fundamental")
        assert resp.status_code == 200
        # Verify both symbols were passed to the client (uppercased, split)
        call_args = cs._client.get_instruments.call_args
        assert "AAPL" in call_args[0][0]
        assert "MSFT" in call_args[0][0]

    def test_invalid_projection_returns_400(self, test_app):
        cs._client = MagicMock()
        resp = test_app.get("/instruments?symbols=SPY&projection=invalid")
        assert resp.status_code == 400
        assert "projection" in resp.json()["detail"]

    def test_empty_symbols_returns_400(self, test_app):
        cs._client = MagicMock()
        resp = test_app.get("/instruments?symbols=,,")
        assert resp.status_code == 400
        cs._client.get_instruments.assert_not_called()

    def test_503_when_no_client(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        resp = test_app.get("/instruments?symbols=SPY&projection=fundamental")
        assert resp.status_code == 503

    def test_404_from_schwab(self, test_app):
        cs._client = MagicMock()
        cs._client.get_instruments.return_value = _mock_resp({}, status_code=404)

        resp = test_app.get("/instruments?symbols=XXXXXX&projection=fundamental")
        assert resp.status_code == 404

    def test_502_on_schwab_error(self, test_app):
        cs._client = MagicMock()
        mock_resp = _mock_resp({}, status_code=500)
        mock_resp.raise_for_status.side_effect = Exception("server error")
        cs._client.get_instruments.return_value = mock_resp

        resp = test_app.get("/instruments?symbols=SPY&projection=fundamental")
        assert resp.status_code == 502
