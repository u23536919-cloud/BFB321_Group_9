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
    """Save one what-if run so the history is kept."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scenarios (region_id, user_id, monthly_volume, freight_pct, customs_pct, "
            "lease_pct, growth_pct, break_even_volume, monthly_saving_zar, recommendation) "
            "SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM regions WHERE code = %s",
            (user_id, volume, wi["freight"], wi["customs"], wi["lease"], wi["growth"],
             break_even, saving, recommendation, code))


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
