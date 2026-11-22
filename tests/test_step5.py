"""Step 5 tests: the real website login, the pages wired to the API, live KPIs and stock.
The database tests are skipped automatically when the MySQL test database is not available."""
import pytest

import db
from app import create_app


# ---- without the database (demo mode) -------------------------------------------------------------
def test_demo_mode_login_still_works_without_accounts():
    client = create_app("testing", USE_DATABASE=False).test_client()
    r = client.post("/login", data={"email": "someone@fintr.com", "password": "x", "role": "Finance"})
    assert r.status_code == 302
    page = client.get("/orders")
    assert page.status_code == 200 and b"This page needs the database" in page.data


def test_login_form_needs_email_and_password():
    client = create_app("testing", USE_DATABASE=False).test_client()
    assert client.post("/login", data={"email": "", "password": ""}).status_code == 400


# ---- with the MySQL test database ----------------------------------------------------------------
def _database_available():
    try:
        db.init_database()
        return True
    except db.DatabaseError:
        return False


needs_db = pytest.mark.skipif(not _database_available(), reason="MySQL test database not available")


def _client(email="thabo.nkosi@fintr.com"):
    db.init_database()  # fresh demo data for every test
    client = create_app("testing", USE_DATABASE=True).test_client()
    client.post("/login", data={"email": email, "password": "demo123"})
    return client


def _token(client):
    with client.session_transaction() as s:
        return {"Authorization": "Bearer " + s["api_token"]}


@needs_db
def test_pages_need_a_login_and_go_back_to_the_page_after_it():
    db.init_database()
    client = create_app("testing", USE_DATABASE=True).test_client()
    r = client.get("/orders")
    assert r.status_code == 302 and "/login?next=/orders" in r.headers["Location"]
    r = client.post("/login?next=/orders", data={"email": "thabo.nkosi@fintr.com", "password": "demo123"})
    assert r.headers["Location"].endswith("/orders")
    evil = client.post("/login?next=//evil.example", data={"email": "thabo.nkosi@fintr.com", "password": "demo123"})
    assert evil.headers["Location"].endswith("/dashboard")  # never sends you to another website


@needs_db
def test_logged_in_pages_carry_the_api_token_for_the_browser():
    client = _client()
    for path in ("/dashboard", "/orders", "/products", "/region/us"):
        page = client.get(path)
        assert page.status_code == 200 and b'name="api-token"' in page.data, path
    assert b'name="can-edit" content="true"' in client.get("/orders").data


@needs_db
def test_read_only_role_gets_no_edit_controls():
    client = _client("lerato.dlamini@fintr.com")
    assert b'name="can-edit" content="false"' in client.get("/orders").data
    assert b'id="add-form"' not in client.get("/orders").data
    assert b'class="btn-calc" type="submit" disabled' in client.get("/region/uk").data


@needs_db
def test_kpis_match_the_data_pack():
    client = _client()
    k = client.get("/api/kpis", headers=_token(client)).get_json()
    assert k["totals"]["consumer_orders"] == 7 and k["totals"]["consumer_units"] == 10
    assert k["totals"]["revenue_zar"] == 8990.0 and k["totals"]["courier_spend_zar"] == 12785.36
    assert k["totals"]["late_rate"] == 0.25
    stock = k["stock_points"][0]
    assert (stock["units_in"], stock["units_out"], stock["on_hand"], stock["status"]) == (24, 7, 17, "OK")
    assert k["unit_cost_by_route_zar"]["direct_to_us_customer"] == pytest.approx(1134.02)
    assert k["unit_cost_by_route_zar"]["bulk_to_us_stock_point"] == pytest.approx(395.24, abs=0.01)
    assert k["monthly_units"]["months"] == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert k["monthly_units"]["us"] == [1, 1, 0, 0, 7]
    assert all(r["recommendation_at_actual_demand"] == "Ship from SA" for r in k["regions"])


@needs_db
def test_an_order_from_the_stock_point_reduces_stock_and_cannot_exceed_it():
    client = _client()
    h = _token(client)
    order = {"region": "us", "sku": "FINTR-01", "route": "US-STOCK-CUST", "order_month": "2026-11",
             "order_type": "Consumer", "units": 5}
    assert client.post("/api/orders", headers=h, json=order).status_code == 201
    assert client.get("/api/kpis", headers=h).get_json()["stock_points"][0]["on_hand"] == 12
    too_many = client.post("/api/orders", headers=h, json={**order, "units": 13})
    assert too_many.status_code == 400 and "Only 12 units" in too_many.get_json()["error"]["details"]["units"]
    # an update gives the order's own units back first: order 8 has 4 units, so 4 + 12 = 16 is allowed
    assert client.put("/api/orders/8", headers=h, json={"units": 16}).status_code == 200
    assert client.get("/api/kpis", headers=h).get_json()["stock_points"][0]["status"] == "Out of stock"


@needs_db
def test_region_calculate_flow_saves_inputs_and_returns_the_page_analysis():
    client = _client()
    h = _token(client)
    assert client.put("/api/regions/us/inputs", headers=h, json={"monthly_volume": 1200}).status_code == 200
    run = client.post("/api/scenarios", headers=h, json={"region": "us", "freight_pct": 10, "growth_pct": 5})
    body = run.get_json()
    labels = [row["label"] for row in body["analysis"]["scen"]]
    assert labels[0] == "Base case" and labels[-1] == "Your what-if (sliders)"
    assert len(body["analysis"]["curve"]) == 13
    assert b'value="1200.0"' in client.get("/region/us").data  # the page shows the saved volume
