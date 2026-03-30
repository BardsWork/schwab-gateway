"""Tests for GET /options/{symbol}."""
from unittest.mock import MagicMock

import gateway.client_state as cs


def _mock_resp(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status.return_value = None
    resp.json.return_value = data
    return resp


def _make_contract(
    strike: float,
    put_call: str,
    bid: float = 1.0,
    ask: float = 1.10,
    last: float = 1.05,
    volume: int = 500,
    open_interest: int = 2000,
    iv: float = 0.25,
    delta: float = 0.50,
    gamma: float = 0.02,
    theta: float = -0.05,
    vega: float = 0.10,
) -> dict:
    return {
        "strikePrice": strike,
        "putCall": put_call,
        "bid": bid,
        "ask": ask,
        "last": last,
        "totalVolume": volume,
        "openInterest": open_interest,
        "impliedVolatility": iv,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
    }


# Minimal chain payload with one call and one put expiry
_CHAIN_PAYLOAD = {
    "symbol": "SPY",
    "status": "SUCCESS",
    "underlying": {
        "lastPrice": 500.0,
        "mark": 499.99,
    },
    "callExpDateMap": {
        "2024-02-16:20": {
            "500.0": [_make_contract(500.0, "CALL", delta=0.52)],
            "510.0": [_make_contract(510.0, "CALL", delta=0.40)],
        },
        "2024-03-15:47": {
            "500.0": [_make_contract(500.0, "CALL", delta=0.54)],
        },
    },
    "putExpDateMap": {
        "2024-02-16:20": {
            "490.0": [_make_contract(490.0, "PUT", delta=-0.40)],
        },
        "2024-03-15:47": {
            "490.0": [_make_contract(490.0, "PUT", delta=-0.42)],
        },
    },
}


class TestOptionsEndpoint:

    def test_returns_envelope_shape(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "SPY"
        assert body["underlying_price"] == 500.0
        assert isinstance(body["expirations"], list)
        assert isinstance(body["data"], list)
        assert body["count"] == len(body["data"])

    def test_expirations_sorted(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        exps = resp.json()["expirations"]
        assert exps == sorted(exps)
        assert "2024-02-16" in exps
        assert "2024-03-15" in exps

    def test_total_contract_count(self, test_app):
        """2 calls on Feb + 1 call on Mar + 1 put on Feb + 1 put on Mar = 5."""
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        assert resp.json()["count"] == 5

    def test_contract_fields_present(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        contract = resp.json()["data"][0]
        for field in ("strike", "expiry", "type", "bid", "ask", "mid", "last",
                      "volume", "open_interest", "iv", "delta", "gamma", "theta", "vega"):
            assert field in contract, f"Missing field: {field}"

    def test_call_and_put_types(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        types = {c["type"] for c in resp.json()["data"]}
        assert types == {"call", "put"}

    def test_mid_computed_correctly(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        for contract in resp.json()["data"]:
            expected_mid = (contract["bid"] + contract["ask"]) / 2.0
            assert abs(contract["mid"] - expected_mid) < 1e-9

    def test_iv_decimal_preserved(self, test_app):
        """IV of 0.25 in Schwab response should stay as 0.25 (not 25.0)."""
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/SPY")
        for contract in resp.json()["data"]:
            assert contract["iv"] == pytest.approx(0.25)

    def test_iv_rescaled_when_whole_number_percentage(self, test_app):
        """If Schwab returns IV as e.g. 25.0 (percent), divide by 100."""
        payload = {
            **_CHAIN_PAYLOAD,
            "callExpDateMap": {
                "2024-02-16:20": {
                    "500.0": [_make_contract(500.0, "CALL", iv=25.0)],
                }
            },
            "putExpDateMap": {},
        }
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(payload)

        resp = test_app.get("/options/SPY")
        assert resp.json()["data"][0]["iv"] == pytest.approx(0.25)

    def test_symbol_uppercased_and_dollar_stripped(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        resp = test_app.get("/options/$spy")
        assert resp.status_code == 200
        assert resp.json()["symbol"] == "SPY"
        # Confirm the call to Schwab stripped the $
        call_kwargs = cs._client.get_option_chain.call_args[1]
        assert call_kwargs["symbol"] == "SPY"

    def test_strike_count_passed_to_schwab(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        test_app.get("/options/SPY?strike_count=10")
        call_kwargs = cs._client.get_option_chain.call_args[1]
        assert call_kwargs["strike_count"] == 10

    def test_from_to_date_passed_as_date_objects(self, test_app):
        from datetime import date
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(_CHAIN_PAYLOAD)

        test_app.get("/options/SPY?from_date=2024-02-01&to_date=2024-03-31")
        call_kwargs = cs._client.get_option_chain.call_args[1]
        assert call_kwargs["from_date"] == date(2024, 2, 1)
        assert call_kwargs["to_date"] == date(2024, 3, 31)

    def test_empty_maps_returns_404(self, test_app):
        payload = {
            "symbol": "XYZ",
            "status": "SUCCESS",
            "underlying": {"lastPrice": 0.0},
            "callExpDateMap": {},
            "putExpDateMap": {},
        }
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(payload)

        resp = test_app.get("/options/XYZ")
        assert resp.status_code == 404

    def test_missing_maps_key_returns_404(self, test_app):
        """Schwab may omit the map keys entirely when no data."""
        payload = {"symbol": "XYZ", "status": "SUCCESS", "underlying": {"lastPrice": 0.0}}
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(payload)

        resp = test_app.get("/options/XYZ")
        assert resp.status_code == 404

    def test_404_from_schwab(self, test_app):
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp({}, status_code=404)

        resp = test_app.get("/options/XXXXXX")
        assert resp.status_code == 404

    def test_502_on_schwab_error(self, test_app):
        cs._client = MagicMock()
        mock_resp = _mock_resp({}, status_code=500)
        mock_resp.raise_for_status.side_effect = Exception("server error")
        cs._client.get_option_chain.return_value = mock_resp

        resp = test_app.get("/options/SPY")
        assert resp.status_code == 502

    def test_503_when_no_client(self, test_app, monkeypatch):
        monkeypatch.setattr(cs, "_client", None)
        resp = test_app.get("/options/SPY")
        assert resp.status_code == 503

    def test_underlying_price_falls_back_to_mark(self, test_app):
        payload = {
            **_CHAIN_PAYLOAD,
            "underlying": {"lastPrice": 0.0, "mark": 499.50},
        }
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(payload)

        resp = test_app.get("/options/SPY")
        assert resp.json()["underlying_price"] == pytest.approx(499.50)

    def test_null_greeks_default_to_zero(self, test_app):
        payload = {
            **_CHAIN_PAYLOAD,
            "callExpDateMap": {
                "2024-02-16:20": {
                    "500.0": [{
                        "strikePrice": 500.0,
                        "putCall": "CALL",
                        "bid": 1.0,
                        "ask": 1.1,
                        "last": 1.05,
                        "totalVolume": 100,
                        "openInterest": 500,
                        "impliedVolatility": None,
                        "delta": None,
                        "gamma": None,
                        "theta": None,
                        "vega": None,
                    }]
                }
            },
            "putExpDateMap": {},
        }
        cs._client = MagicMock()
        cs._client.get_option_chain.return_value = _mock_resp(payload)

        resp = test_app.get("/options/SPY")
        contract = resp.json()["data"][0]
        assert contract["iv"] == 0.0
        assert contract["delta"] == 0.0


import pytest
