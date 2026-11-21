"""Data access: every SQL query the web app uses lives here.
All queries use %s placeholders (never string joining) so user input cannot
change the SQL. Table and column names match database/schema.sql and the ERD.
DECIMAL values come back from MySQL as Decimal, so we turn them into floats
for the break-even model."""
from datetime import date

from db import DatabaseError, connection

INPUT_FIELDS = ("direct_freight_zar", "setup_zar", "local_delivery_zar", "rent_zar_month")


def _num(value):
    """DECIMAL -> float (None stays None)."""
    return None if value is None else float(value)


def load_snapshot():
    """Read everything one page needs in a single connection: regions, latest
    exchange rates, inventory assumptions and each region's current inputs."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, code, name, currency_code FROM regions ORDER BY id")
        regions = {r["code"]: {"id": r["id"], "name": r["name"], "currency": r["currency_code"]}
                   for r in cur.fetchall()}

        # newest rate on or before today, for each currency
        cur.execute(
            "SELECT e.currency_code, e.zar_per_unit FROM exchange_rates e "
            "JOIN (SELECT currency_code, MAX(effective_date) AS d FROM exchange_rates "
            "      WHERE effective_date <= %s GROUP BY currency_code) latest "
            "  ON latest.currency_code = e.currency_code AND latest.d = e.effective_date",
            (date.today(),))
        rates = {r["currency_code"]: float(r["zar_per_unit"]) for r in cur.fetchall()}

        cur.execute("SELECT name, value FROM inventory_assumptions")
        assumptions = {r["name"]: float(r["value"]) for r in cur.fetchall()}

        cur.execute(
            "SELECT r.code, i.monthly_volume, i.direct_freight_zar, i.setup_zar, i.local_delivery_zar, "
            "       i.rent_zar_month, i.inbound_shipment_fixed_zar, i.baseline_freight_zar "
            "FROM region_cost_inputs i JOIN regions r ON r.id = i.region_id")
        inputs = {row["code"]: {k: _num(v) for k, v in row.items() if k != "code"}
                  for row in cur.fetchall()}
    if not regions or not rates or not assumptions or not inputs:
        raise DatabaseError("The database is empty. Run: python db.py init")
    return {"regions": regions, "rates": rates, "assumptions": assumptions, "inputs": inputs}


def upsert_user(name, email, role_name):
    """Find the user by email (creating the user and role if new); return the user id."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM roles WHERE name = %s", (role_name,))
        role = cur.fetchone()
        if role is None:
            cur.execute("INSERT INTO roles (name) VALUES (%s)", (role_name,))
            role_id = cur.lastrowid
        else:
            role_id = role["id"]
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if user is None:
            cur.execute("INSERT INTO users (name, email, role_id) VALUES (%s, %s, %s)",
                        (name, email, role_id))
            return cur.lastrowid
        cur.execute("UPDATE users SET name = %s, role_id = %s WHERE id = %s", (name, role_id, user["id"]))
        return user["id"]


def save_region_inputs(code, values, volume, user_id):
    """Save the four editable cost inputs and the monthly volume of one region."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE region_cost_inputs i JOIN regions r ON r.id = i.region_id "
            "SET i.direct_freight_zar = %s, i.setup_zar = %s, i.local_delivery_zar = %s, "
            "    i.rent_zar_month = %s, i.monthly_volume = %s, i.updated_by = %s "
            "WHERE r.code = %s",
            (values["direct_freight_zar"], values["setup_zar"], values["local_delivery_zar"],
             values["rent_zar_month"], volume, user_id, code))


def save_settings(new_values, regional_shipment, user_id):
    """Save exchange rates (as today's rate), the shared inventory assumptions and each
    region's inbound shipment cost. regional_shipment is {region code: ZAR}."""
    rate_names = {"USD": "fx_zar_per_usd", "GBP": "zar_per_gbp"}
    with connection() as conn, conn.cursor() as cur:
        for currency, name in rate_names.items():
            cur.execute(
                "INSERT INTO exchange_rates (currency_code, zar_per_unit, effective_date) "
                "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE zar_per_unit = %s",
                (currency, new_values[name], date.today(), new_values[name]))
        for name, value in new_values.items():
            if name in rate_names.values() or name.startswith("inbound_shipment_fixed_zar_"):
                continue
            cur.execute("UPDATE inventory_assumptions SET value = %s WHERE name = %s", (value, name))
        for code, zar in regional_shipment.items():
            cur.execute(
                "UPDATE region_cost_inputs i JOIN regions r ON r.id = i.region_id "
                "SET i.inbound_shipment_fixed_zar = %s, i.updated_by = %s WHERE r.code = %s",
                (zar, user_id, code))


def record_scenario(code, user_id, volume, wi, break_even, saving, recommendation):
    """Save one what-if run so the history is kept. Returns scenario ID"""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scenarios (region_id, user_id, monthly_volume, freight_pct, customs_pct, "
            "lease_pct, growth_pct, break_even_volume, monthly_saving_zar, recommendation) "
            "SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM regions WHERE code = %s",
            (user_id, volume, wi["freight"], wi["customs"], wi["lease"], wi["growth"],
             break_even, saving, recommendation, code))
        return cur.lastrowid


def record_report(code, user_id, sections, file_name):
    """Log a generated PDF report."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO reports (region_id, user_id, sections, file_name) "
            "SELECT id, %s, %s, %s FROM regions WHERE code = %s",
            (user_id, sections, file_name, code))


def recent_scenarios(limit=10):
    """Latest saved what-if runs (used by the tests and available for the UI)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT s.id, r.code AS region, s.monthly_volume, s.recommendation, s.created_at "
            "FROM scenarios s JOIN regions r ON r.id = s.region_id "
            "ORDER BY s.created_at DESC, s.id DESC LIMIT %s", (limit,))
        return cur.fetchall()

# Queries used by the REST API (api/) 
# Filters are added as "column = %s" pieces with their values kept separately,
# so user input never becomes part of the SQL text.

def _where(conditions):
    """[(sql, value), ...] -> (' WHERE a AND b', [values]); entries whose value is None are skipped."""
    parts = [(sql, value) for sql, value in conditions if value is not None]
    if not parts:
        return "", []
    return " WHERE " + " AND ".join(sql for sql, _ in parts), [value for _, value in parts]


def _page(cur, select_sql, count_sql, where, params, order_by, limit, offset):
    cur.execute(count_sql + where, params)
    total = cur.fetchone()["n"]
    cur.execute(f"{select_sql}{where} ORDER BY {order_by} LIMIT %s OFFSET %s", (*params, limit, offset))
    return cur.fetchall(), total


def find_user_by_email(email):
    """The user with their role and password hash (None if not found)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT u.id, u.name, u.email, u.password_hash, r.name AS role "
                    "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.email = %s", (email,))
        return cur.fetchone()


def get_user(user_id):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT u.id, u.name, u.email, r.name AS role "
                    "FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s", (user_id,))
        return cur.fetchone()


def list_scenarios(region=None, recommendation=None, limit=20, offset=0):
    where, params = _where([("r.code = %s", region), ("s.recommendation = %s", recommendation)])
    base = (" FROM scenarios s JOIN regions r ON r.id = s.region_id "
            "LEFT JOIN users u ON u.id = s.user_id")
    with connection() as conn, conn.cursor() as cur:
        return _page(cur,
                     "SELECT s.id, r.code AS region, u.email AS created_by, s.monthly_volume, s.freight_pct, "
                     "s.customs_pct, s.lease_pct, s.growth_pct, s.break_even_volume, s.monthly_saving_zar, "
                     "s.recommendation, s.created_at" + base,
                     "SELECT COUNT(*) AS n" + base, where, params, "s.created_at DESC, s.id DESC", limit, offset)


def list_orders(region=None, order_type=None, month_from=None, month_to=None, limit=20, offset=0):
    where, params = _where([("r.code = %s", region), ("o.order_type = %s", order_type),
                            ("o.order_month >= %s", month_from), ("o.order_month <= %s", month_to)])
    base = (" FROM orders o JOIN regions r ON r.id = o.region_id "
            "JOIN products p ON p.id = o.product_id JOIN routes rt ON rt.id = o.route_id")
    with connection() as conn, conn.cursor() as cur:
        return _page(cur,
                     "SELECT o.id, r.code AS region, o.order_month, o.order_type, o.units, p.sku, "
                     "rt.code AS route, o.shipment_id" + base,
                     "SELECT COUNT(*) AS n" + base, where, params, "o.order_month, o.id", limit, offset)


def list_shipments(region=None, route=None, exception=None, limit=20, offset=0):
    where, params = _where([("dl.region_id = (SELECT id FROM regions WHERE code = %s)", region),
                            ("rt.code = %s", route), ("s.had_exception = %s", exception)])
    base = (" FROM shipments s JOIN routes rt ON rt.id = s.route_id "
            "JOIN locations dl ON dl.id = rt.destination_location_id")
    # the invoice total is worked out from its lines (it is never stored, see schema.sql)
    total_sql = ("(SELECT SUM(l.amount_zar) FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id "
                 " WHERE i.shipment_id = s.id) AS total_zar")
    units_sql = "(SELECT SUM(o.units) FROM orders o WHERE o.shipment_id = s.id) AS units"
    with connection() as conn, conn.cursor() as cur:
        return _page(cur,
                     "SELECT s.id, rt.code AS route, s.carrier, s.cartons, s.ship_date, s.date_precision, "
                     f"s.had_exception, s.exception_note, {units_sql}, {total_sql}" + base,
                     "SELECT COUNT(*) AS n" + base, where, params, "s.ship_date, s.id", limit, offset)


def get_shipment(shipment_id):
    """One shipment with its invoice lines (None if not found)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT s.id, rt.code AS route, rt.transit_days_min, rt.transit_days_max, s.carrier, "
                    "s.cartons, s.ship_date, s.date_precision, s.had_exception, s.exception_note "
                    "FROM shipments s JOIN routes rt ON rt.id = s.route_id WHERE s.id = %s", (shipment_id,))
        shipment = cur.fetchone()
        if shipment is None:
            return None
        cur.execute("SELECT ct.code AS charge, ct.name, l.amount_zar, l.note "
                    "FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id "
                    "JOIN charge_types ct ON ct.id = l.charge_type_id WHERE i.shipment_id = %s "
                    "ORDER BY l.id", (shipment_id,))
        shipment["charges"] = cur.fetchall()
        shipment["total_zar"] = sum(c["amount_zar"] for c in shipment["charges"])
        return shipment


def list_reports(region=None, limit=20, offset=0):
    where, params = _where([("r.code = %s", region)])
    base = " FROM reports rp JOIN regions r ON r.id = rp.region_id LEFT JOIN users u ON u.id = rp.user_id"
    with connection() as conn, conn.cursor() as cur:
        return _page(cur,
                     "SELECT rp.id, r.code AS region, u.email AS created_by, rp.sections, rp.file_name, "
                     "rp.created_at" + base,
                     "SELECT COUNT(*) AS n" + base, where, params, "rp.created_at DESC, rp.id DESC", limit, offset)


# ---- products (create / read / update) -----------------------------------------------------------
PRODUCT_COLUMNS = "id, sku, name, ex_factory_zar, landed_cost_zar, retail_price_zar, created_at, updated_at"


def list_products(limit=20, offset=0):
    with connection() as conn, conn.cursor() as cur:
        return _page(cur, f"SELECT {PRODUCT_COLUMNS} FROM products", "SELECT COUNT(*) AS n FROM products",
                     "", [], "sku", limit, offset)


def get_product(product_id):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = %s", (product_id,))
        return cur.fetchone()


def create_product(values):
    """values: sku, name, ex_factory_zar, landed_cost_zar, retail_price_zar.  Returns the new id.
    A duplicate SKU is refused by the UNIQUE key (db.DataRuleError)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO products (sku, name, ex_factory_zar, landed_cost_zar, retail_price_zar) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (values["sku"], values["name"], values["ex_factory_zar"], values["landed_cost_zar"],
                     values["retail_price_zar"]))
        return cur.lastrowid


def update_product(product_id, values):
    """Update only the given columns (names are checked by the API against a fixed list)."""
    allowed = ("sku", "name", "ex_factory_zar", "landed_cost_zar", "retail_price_zar")
    columns = [c for c in allowed if c in values]
    with connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE products SET {', '.join(c + ' = %s' for c in columns)} WHERE id = %s",
                    (*[values[c] for c in columns], product_id))


# ---- single orders (create / read / update) ------------------------------------------------------
ORDER_SELECT = ("SELECT o.id, r.code AS region, o.order_month, o.order_type, o.units, p.sku, "
                "rt.code AS route, o.shipment_id, o.created_at, o.updated_at "
                "FROM orders o JOIN regions r ON r.id = o.region_id "
                "JOIN products p ON p.id = o.product_id JOIN routes rt ON rt.id = o.route_id")


def get_order(order_id):
    with connection() as conn, conn.cursor() as cur:
        cur.execute(ORDER_SELECT + " WHERE o.id = %s", (order_id,))
        return cur.fetchone()


def route_destination_region(route_code):
    """The region code a route delivers to (None if the route does not exist or ends in SA)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT r.code FROM routes rt JOIN locations l ON l.id = rt.destination_location_id "
                    "LEFT JOIN regions r ON r.id = l.region_id WHERE rt.code = %s", (route_code,))
        row = cur.fetchone()
    if row is None:
        return False  # no such route
    return row["code"]


def save_order(values, order_id=None):
    """Insert (order_id None) or update an order.  values hold codes (region, sku, route), which are
    turned into ids inside the same statement.  Returns the order id."""
    params = (values["region"], values["sku"], values["route"], values.get("shipment_id"),
              values["order_month"], values["order_type"], values["units"])
    lookups = ("(SELECT id FROM regions WHERE code = %s), (SELECT id FROM products WHERE sku = %s), "
               "(SELECT id FROM routes WHERE code = %s), %s, %s, %s, %s")
    with connection() as conn, conn.cursor() as cur:
        if order_id is None:
            cur.execute("INSERT INTO orders (region_id, product_id, route_id, shipment_id, order_month, "
                        f"order_type, units) VALUES ({lookups})", params)
            return cur.lastrowid
        cur.execute("UPDATE orders SET region_id = (SELECT id FROM regions WHERE code = %s), "
                    "product_id = (SELECT id FROM products WHERE sku = %s), "
                    "route_id = (SELECT id FROM routes WHERE code = %s), shipment_id = %s, "
                    "order_month = %s, order_type = %s, units = %s WHERE id = %s", (*params, order_id))
        return order_id


def demand_summary(region_code):
    """Actual consumer demand for one region from the orders table (used by GET /api/regions).
    The period runs from the first to the last month of order data across all regions."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(order_month) AS first, MAX(order_month) AS last FROM orders")
        span = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS orders, COALESCE(SUM(o.units), 0) AS units "
                    "FROM orders o JOIN regions r ON r.id = o.region_id "
                    "WHERE r.code = %s AND o.order_type = 'Consumer'", (region_code,))
        totals = cur.fetchone()
    if not span["first"]:
        return {"first_month": None, "last_month": None, "months": 0, "consumer_orders": 0,
                "consumer_units": 0, "avg_orders_per_month": 0.0}
    months = (span["last"].year - span["first"].year) * 12 + span["last"].month - span["first"].month + 1
    return {"first_month": span["first"], "last_month": span["last"], "months": months,
            "consumer_orders": totals["orders"], "consumer_units": int(totals["units"]),
            "avg_orders_per_month": round(totals["orders"] / months, 2)}


def get_scenario(scenario_id):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT s.id, r.code AS region, u.email AS created_by, s.monthly_volume, s.freight_pct, "
                    "s.customs_pct, s.lease_pct, s.growth_pct, s.break_even_volume, s.monthly_saving_zar, "
                    "s.recommendation, s.created_at FROM scenarios s JOIN regions r ON r.id = s.region_id "
                    "LEFT JOIN users u ON u.id = s.user_id WHERE s.id = %s", (scenario_id,))
        return cur.fetchone()


def product_exists(sku):
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM products WHERE sku = %s", (sku,))
        return cur.fetchone() is not None