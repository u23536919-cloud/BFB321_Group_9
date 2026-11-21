"""Step 4 API tests.

The first group runs without MySQL (USE_DATABASE off): it checks login protection, roles,
validation, the JSON error format and the break-even endpoints.  The second group uses the MySQL
test database and is skipped automatically when it is not available (like test_database.py)."""
import pytest

import db
from api.auth import create_token
from app import create_app


def _headers(app, role="Finance", user_id=1):
    with app.app_context():
        token = create_token({"id": user_id, "email": "test@fintr.com", "role": role})
    return {"Authorization": f"Bearer {token}"}


# ---- without the database ------------------------------------------------------------------------
@pytest.fixture()
def app():
    return create_app("testing", USE_DATABASE=False)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_health_needs_no_login(client):
    assert client.get("/api/health").get_json() == {"status": "ok", "database": "off"}


def test_endpoints_need_a_token(client):
    r = client.get("/api/regions")
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "unauthorized"


def test_a_tampered_token_is_rejected(app, client):
    headers = _headers(app)
    headers["Authorization"] += "x"
    assert client.get("/api/regions", headers=headers).get_json()["error"]["code"] == "invalid_token"


def test_an_expired_token_is_rejected(app, client):
    app.config["JWT_EXPIRES_HOURS"] = -1
    r = client.get("/api/regions", headers=_headers(app))
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "token_expired"


def test_region_returns_inputs_and_break_even_results(app, client):
    body = client.get("/api/regions/uk", headers=_headers(app)).get_json()
    assert body["code"] == "uk" and body["monthly_volume"] == 780
    assert body["inputs_zar"]["direct_freight_zar"] == 252.0
    assert body["results"]["break_even_volume"] == pytest.approx(1137.08, abs=0.01)  # same as seed.sql
    assert body["results"]["recommendation"] == "Ship from SA"


def test_unknown_region_and_unknown_url_give_json_404(app, client):
    assert client.get("/api/regions/fr", headers=_headers(app)).get_json()["error"]["status"] == 404
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and r.is_json


def test_wrong_method_gives_json_405(app, client):
    r = client.delete("/api/regions", headers=_headers(app))
    assert r.status_code == 405 and r.get_json()["error"]["code"] == "method_not_allowed"


def test_website_pages_still_use_html_errors(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and not r.is_json


def test_scenario_runs_the_model(app, client):
    r = client.post("/api/scenarios", headers=_headers(app), json={"region": "uk", "freight_pct": 10})
    assert r.status_code == 201
    body = r.get_json()
    assert body["id"] is None  # nothing saved without the database
    assert body["scenario"]["break_even_volume"] == pytest.approx(951.28, abs=0.01)  # same as seed.sql


def test_scenario_validation_lists_every_problem(app, client):
    r = client.post("/api/scenarios", headers=_headers(app),
                    json={"region": "uk", "freight_pct": "a lot", "growth_pct": -500, "colour": "red"})
    assert r.status_code == 400
    details = r.get_json()["error"]["details"]
    assert set(details) == {"freight_pct", "growth_pct", "colour"}


def test_senior_management_is_read_only(app, client):
    headers = _headers(app, role="Senior management")
    assert client.get("/api/regions", headers=headers).status_code == 200
    r = client.post("/api/scenarios", headers=headers, json={"region": "uk"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "forbidden"


def test_database_endpoints_say_when_the_database_is_off(app, client):
    r = client.get("/api/orders", headers=_headers(app))
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "database_off"


# ---- with the MySQL test database ----------------------------------------------------------------
def _database_available():
    try:
        db.init_database()
        return True
    except db.DatabaseError:
        return False


needs_db = pytest.mark.skipif(not _database_available(), reason="MySQL test database not available")


@pytest.fixture()
def db_client():
    db.init_database()  # fresh demo data for every test
    return create_app("testing", USE_DATABASE=True).test_client()


def _login(client, email="jiara.martins@fintr.com", password="demo123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


@needs_db
def test_login_gives_a_token_that_works(db_client):
    r = _login(db_client)
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "Finance"
    me = db_client.get("/api/auth/me", headers={"Authorization": "Bearer " + r.get_json()["token"]})
    assert me.get_json()["user"]["email"] == "jiara.martins@fintr.com"


@needs_db
def test_wrong_password_is_rejected(db_client):
    r = _login(db_client, password="wrong-one")
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "invalid_credentials"


@needs_db
def test_orders_are_paginated_and_filtered(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    body = db_client.get("/api/orders?region=us&per_page=3&page=2", headers=h).get_json()
    assert body["pagination"] == {"page": 2, "per_page": 3, "total": 7, "pages": 3}
    oct_orders = db_client.get("/api/orders?region=us&from=2026-10&to=2026-10", headers=h).get_json()
    assert sum(o["units"] for o in oct_orders["data"]) == 7


@needs_db
def test_shipment_detail_has_its_invoice_lines(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    late = db_client.get("/api/shipments?exception=true", headers=h).get_json()["data"]
    assert [s["id"] for s in late] == [4]
    detail = db_client.get("/api/shipments/4", headers=h).get_json()
    assert detail["total_zar"] == 9485.78 and len(detail["charges"]) == 5


@needs_db
def test_updating_inputs_saves_to_mysql(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    r = db_client.put("/api/regions/uk/inputs", headers=h, json={"monthly_volume": 900})
    assert r.status_code == 200
    again = db_client.get("/api/regions/uk", headers=h).get_json()
    assert again["monthly_volume"] == 900


@needs_db
def test_saved_scenario_appears_in_the_list(db_client):
    h = {"Authorization": "Bearer " + _login(db_client, "thabo.nkosi@fintr.com").get_json()["token"]}
    new_id = db_client.post("/api/scenarios", headers=h, json={"region": "us", "lease_pct": 5}).get_json()["id"]
    newest = db_client.get("/api/scenarios?region=us&per_page=1", headers=h).get_json()["data"][0]
    assert newest["id"] == new_id and newest["created_by"] == "thabo.nkosi@fintr.com"


@needs_db
def test_region_shows_actual_demand_and_a_new_order_counts(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    before = db_client.get("/api/regions/us", headers=h).get_json()["actual_demand"]
    assert before["consumer_orders"] == 6 and before["months"] == 5
    r = db_client.post("/api/orders", headers=h, json={
        "region": "us", "sku": "FINTR-01", "route": "US-STOCK-CUST", "order_month": "2026-11",
        "order_type": "Consumer", "units": 2})
    assert r.status_code == 201 and r.get_json()["units"] == 2
    after = db_client.get("/api/regions/us", headers=h).get_json()["actual_demand"]
    assert after["consumer_orders"] == 7 and after["months"] == 6


@needs_db
def test_order_can_be_read_and_updated(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    assert db_client.get("/api/orders/8", headers=h).get_json()["units"] == 4
    r = db_client.put("/api/orders/8", headers=h, json={"units": 5})
    assert r.status_code == 200
    rows = _sql_rows("SELECT units FROM orders WHERE id = 8")
    assert rows[0]["units"] == 5  # the change is in MySQL
    assert db_client.get("/api/orders/999", headers=h).status_code == 404


@needs_db
def test_order_must_match_its_route_and_product(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    r = db_client.post("/api/orders", headers=h, json={
        "region": "uk", "sku": "NOPE", "route": "US-STOCK-CUST", "order_month": "2026-11",
        "order_type": "Consumer", "units": 1})
    assert r.status_code == 400
    assert set(r.get_json()["error"]["details"]) == {"route", "sku"}
    bad_ref = db_client.put("/api/orders/1", headers=h, json={"shipment_id": 999})
    assert bad_ref.status_code == 400 and bad_ref.get_json()["error"]["code"] == "bad_reference"


@needs_db
def test_product_create_read_update_and_duplicate_sku(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    body = {"sku": "fintr-02", "name": "Expansion pack", "ex_factory_zar": 60,
            "landed_cost_zar": 80, "retail_price_zar": 499}
    created = db_client.post("/api/products", headers=h, json=body)
    assert created.status_code == 201 and created.get_json()["sku"] == "FINTR-02"
    new_id = created.get_json()["id"]
    assert db_client.put(f"/api/products/{new_id}", headers=h,
                         json={"retail_price_zar": 549}).get_json()["retail_price_zar"] == 549
    assert db_client.get("/api/products", headers=h).get_json()["pagination"]["total"] == 2
    dup = db_client.post("/api/products", headers=h, json=body)
    assert dup.status_code == 409 and "FINTR-02" in dup.get_json()["error"]["message"]


@needs_db
def test_product_validation_returns_400_with_every_problem(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    r = db_client.post("/api/products", headers=h, json={"sku": "", "ex_factory_zar": 100,
                                                         "landed_cost_zar": "lots"})
    assert r.status_code == 400
    assert set(r.get_json()["error"]["details"]) == {"sku", "name", "landed_cost_zar", "retail_price_zar"}
    price = db_client.put("/api/products/1", headers=h, json={"landed_cost_zar": 10})
    assert price.status_code == 400  # landed cost below ex-factory cost


@needs_db
def test_one_scenario_can_be_read(db_client):
    h = {"Authorization": "Bearer " + _login(db_client).get_json()["token"]}
    assert db_client.get("/api/scenarios/1", headers=h).get_json()["recommendation"] == "Ship from SA"
    assert db_client.get("/api/scenarios/999", headers=h).status_code == 404


def _sql_rows(statement):
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(statement)
        return cur.fetchall()
