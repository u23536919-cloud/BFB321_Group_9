"""Tests that use a real MySQL database (the test database from DB_TEST_NAME in .env).
They are skipped automatically when MySQL is not running or not configured."""
import pytest

import db
import repository as repo
from app import create_app, setting_fields


def _database_available():
    try:
        db.init_database()  # rebuilds schema.sql + seed.sql in the TEST database only
        return True
    except db.DatabaseError:
        return False


pytestmark = pytest.mark.skipif(not _database_available(), reason="MySQL test database not available")


@pytest.fixture()
def client():
    db.init_database()  # fresh demo data for every test
    app = create_app("testing", USE_DATABASE=True)
    client = app.test_client()
    client.post("/login", data={"email": "jiara.martins@fintr.com", "password": "demo123"})
    return client


def _sql(statement, params=()):
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()


def test_seed_data_is_in_every_main_table():
    for table in ("roles", "users", "currencies", "regions", "exchange_rates",
                  "inventory_assumptions", "region_cost_inputs", "scenarios", "reports",
                  "products", "locations", "routes", "shipments", "invoices", "charge_types",
                  "invoice_lines", "orders"):
        assert _sql(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] > 0, table


def test_fintr_invoice_totals_match_the_data_pack():
    rows = _sql("SELECT invoice_id, SUM(amount_zar) AS total FROM invoice_lines GROUP BY invoice_id ORDER BY invoice_id")
    assert [float(r["total"]) for r in rows] == [882.18, 1071.18, 1196.86, 9485.78, 149.36]


def test_fintr_orders_and_delay_rate_match_the_data_pack():
    us_oct = _sql("SELECT COUNT(*) AS n, SUM(units) AS u FROM orders o JOIN regions r ON r.id = o.region_id "
                  "WHERE r.code = 'us' AND o.order_month = '2026-10-01'")[0]
    assert (us_oct["n"], int(us_oct["u"])) == (4, 7)
    late = _sql("SELECT SUM(had_exception) AS late, COUNT(*) AS n FROM shipments")[0]
    assert (int(late["late"]), late["n"]) == (1, 4)  # 25% delay rate


def test_region_page_shows_values_from_mysql(client):
    assert b'value="10.72"' in client.get("/region/uk").data  # 252 ZAR / 23.5
    # change a row directly in MySQL and the page follows
    _sql("UPDATE region_cost_inputs SET direct_freight_zar = 470 WHERE region_id = "
         "(SELECT id FROM regions WHERE code = 'uk')")
    assert b'value="20.0"' in client.get("/region/uk").data  # 470 / 23.5


def test_region_name_comes_from_mysql(client):
    _sql("UPDATE regions SET name = 'Great Britain' WHERE code = 'uk'")
    assert b"Great Britain" in client.get("/region/uk").data


def test_posting_a_region_saves_inputs_and_a_scenario(client):
    before = len(_sql("SELECT id FROM scenarios"))
    response = client.post("/region/uk", data={
        "direct_freight_zar": "10", "setup_zar": "100", "local_delivery_zar": "3",
        "rent_zar_month": "1000", "volume": "900", "wi_freight": "10"})
    assert response.status_code == 200
    row = _sql("SELECT i.monthly_volume, i.direct_freight_zar FROM region_cost_inputs i "
               "JOIN regions r ON r.id = i.region_id WHERE r.code = 'uk'")[0]
    assert float(row["monthly_volume"]) == 900
    assert float(row["direct_freight_zar"]) == pytest.approx(10 * 23.5, abs=0.001)
    assert len(_sql("SELECT id FROM scenarios")) == before + 1


def test_settings_are_saved_in_mysql_and_can_be_reset(client):
    data = {f["name"]: str(f["default"]) for f in setting_fields()}
    data["zar_per_gbp"] = "25"
    assert client.post("/settings", data=data).status_code == 302
    rate = _sql("SELECT zar_per_unit FROM exchange_rates WHERE currency_code = 'GBP' "
                "ORDER BY effective_date DESC LIMIT 1")[0]["zar_per_unit"]
    assert float(rate) == 25
    assert client.post("/settings", data={"reset": "1"}).status_code == 302
    assert repo.load_snapshot()["rates"]["GBP"] == 23.5


def test_login_checks_the_password_and_uses_the_role_from_mysql():
    db.init_database()
    client = create_app("testing", USE_DATABASE=True).test_client()
    assert client.get("/dashboard").status_code == 302  # pages need a login
    bad = client.post("/login", data={"email": "lerato.dlamini@fintr.com", "password": "wrong-one"})
    assert bad.status_code == 401 and b"Wrong email or password" in bad.data
    ok = client.post("/login", data={"email": "Lerato.Dlamini@fintr.com", "password": "demo123"})
    assert ok.status_code == 302
    with client.session_transaction() as s:
        assert s["role"] == "Senior management" and s["api_token"]
    assert client.get("/dashboard").status_code == 200
    client.get("/logout")
    assert client.get("/dashboard").status_code == 302


def test_pdf_download_is_logged(client):
    before = len(_sql("SELECT id FROM reports"))
    assert client.get("/reports/pdf?region=us").status_code == 200
    assert len(_sql("SELECT id FROM reports")) == before + 1


def test_database_down_shows_a_friendly_page(client, monkeypatch):
    def broken():
        raise db.DatabaseError("Cannot connect to the database.")
    monkeypatch.setattr(repo, "load_snapshot", broken)
    response = client.get("/dashboard")
    assert response.status_code == 503
    assert b"Database unavailable" in response.data
