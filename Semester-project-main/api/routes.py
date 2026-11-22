"""The API endpoints.  Every response is JSON.  See README.md (API section) for the full list.

The break-even numbers come from the same functions the website uses (region_inputs etc. in
app.py, which create_app shares through app.extensions["fintr"]), so the API and the pages
always agree."""
import time
from dataclasses import asdict, replace
from datetime import datetime
from math import ceil

from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash

import repository as repo
from db import DataRuleError
from models import breakeven as m

from . import bp
from .auth import EDITOR_ROLES, create_token, login_required, roles_required
from .errors import ApiError
from .helpers import (bool_arg, choice_arg, json_body, month_arg, paginated, pagination_args, to_json,
                      validate_numbers)

RECOMMENDATIONS = ("Ship from SA", "Review: inside margin", "Use warehouse")
ORDER_TYPES = ("Consumer", "Restock")
ROUTES = ("SA-UK-DIRECT", "SA-US-DIRECT", "SA-US-STOCK", "US-STOCK-CUST")

# what a client may change on a region (ZAR amounts, volume in units per month)
INPUT_SCHEMA = {"direct_freight_zar": (0, None), "setup_zar": (0, None), "local_delivery_zar": (0, None),
                "rent_zar_month": (0, None), "monthly_volume": (0, None)}
# a what-if run: percentage changes (-50 = 50% lower), volume optional
SCENARIO_SCHEMA = {"monthly_volume": (0, None), "freight_pct": (-100, 1000), "customs_pct": (-100, 1000),
                   "lease_pct": (-100, 1000), "growth_pct": (-100, 1000)}


# ---- helpers -------------------------------------------------------------------------------------
def fintr():
    """The shared website helpers (set up in create_app)."""
    return current_app.extensions["fintr"]


def require_db():
    if not fintr()["use_db"]():
        raise ApiError(503, "database_off", "This endpoint needs the database (USE_DATABASE=true in .env).")


def check_region(code):
    if code not in fintr()["regions"]:
        raise ApiError(404, "not_found", f"No region called '{code}'. Use one of: {', '.join(fintr()['regions'])}.")


def results(p, q):
    """The break-even answer for inputs p at monthly volume q."""
    return {"break_even_volume": m.break_even_volume(p),
            "decision_threshold": m.decision_threshold(p, fintr()["margin"]),
            "progress_to_break_even": m.progress_to_break_even(q, m.break_even_volume(p)),
            "direct_cost_per_unit_zar": m.total_cost_direct(p, q) / q if q else None,
            "warehouse_cost_per_unit_zar": m.total_cost_warehouse(p, q) / q if q else None,
            "monthly_saving_zar": m.monthly_saving(p, q),
            "recommendation": m.recommend(p, q, fintr()["margin"])}


def region_detail(code):
    p, q = fintr()["inputs"](code), fintr()["volume"](code)
    body = {"code": code, "name": fintr()["name"](code), "monthly_volume": q,
            "inputs_zar": asdict(p), "results": results(p, q)}
    if fintr()["use_db"]():  # Fintr's real demand, worked out from the orders table
        body["actual_demand"] = repo.demand_summary(code)
    return to_json(body)


# ---- logging: one line per API call ---------------------------------------------------------------
@bp.before_request
def start_timer():
    g.api_started = time.perf_counter()


@bp.after_request
def log_call(response):
    ms = (time.perf_counter() - g.get("api_started", time.perf_counter())) * 1000
    who = g.get("api_user", {}).get("email", "-")
    current_app.logger.info("API %s %s -> %s (%.0f ms, user %s)", request.method, request.full_path.rstrip("?"),
                            response.status_code, ms, who)
    return response


# ---- health and login -----------------------------------------------------------------------------
@bp.get("/health")
def health():
    """Is the API up, and can it reach MySQL?  (No login needed.)"""
    if not fintr()["use_db"]():
        db_state = "off"
    else:
        try:
            repo.get_user(0)
            db_state = "connected"
        except Exception:  # noqa: BLE001  any database problem means "unavailable"
            db_state = "unavailable"
    return jsonify({"status": "ok", "database": db_state})


@bp.post("/auth/login")
def login():
    require_db()
    data = json_body()
    email = str(data.get("email", "")).strip().lower()
    password = str(data.get("password", ""))
    if not email or not password:
        raise ApiError(400, "validation_failed", "Send both email and password.",
                       {k: "This field is required." for k in ("email", "password") if not data.get(k)})
    user = repo.find_user_by_email(email)
    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        raise ApiError(401, "invalid_credentials", "Wrong email or password.")
    token = create_token(user)
    return jsonify({"token": token, "token_type": "Bearer",
                    "expires_in_seconds": int(current_app.config["JWT_EXPIRES_HOURS"] * 3600),
                    "user": {"id": user["id"], "name": user["name"], "email": user["email"], "role": user["role"]}})


@bp.get("/auth/me")
@login_required
def me():
    return jsonify({"user": g.api_user, "can_edit": g.api_user["role"] in EDITOR_ROLES})


# ---- regions and the break-even model -------------------------------------------------------------
@bp.get("/regions")
@login_required
def list_regions():
    return jsonify({"data": [region_detail(code) for code in fintr()["regions"]]})


@bp.get("/regions/<code>")
@login_required
def get_region(code):
    check_region(code)
    return jsonify(region_detail(code))


@bp.put("/regions/<code>/inputs")
@login_required
@roles_required(*EDITOR_ROLES)
def update_region_inputs(code):
    """Change one or more of the region's cost inputs and/or its monthly volume (send only what changes)."""
    check_region(code)
    require_db()
    new = validate_numbers(json_body(), INPUT_SCHEMA, partial=True)
    if not new:
        raise ApiError(400, "validation_failed", "Send at least one field to change.",
                       {name: "Optional field." for name in INPUT_SCHEMA})
    p = fintr()["inputs"](code)
    volume = new.pop("monthly_volume", fintr()["volume"](code))
    p = replace(p, **new)  # the model re-checks every value (raises ModelError -> 400)
    repo.save_region_inputs(code, {k: getattr(p, k) for k in INPUT_SCHEMA if k != "monthly_volume"},
                            volume, g.api_user["id"])
    fintr()["refresh"]()
    return jsonify(region_detail(code))


# ---- what-if scenarios ----------------------------------------------------------------------------
@bp.get("/scenarios")
@login_required
def list_scenarios():
    require_db()
    region = choice_arg("region", tuple(fintr()["regions"]))
    rec = choice_arg("recommendation", RECOMMENDATIONS)
    page, per_page, limit, offset = pagination_args()
    rows, total = repo.list_scenarios(region, rec, limit, offset)
    return jsonify(paginated(rows, total, page, per_page))


@bp.post("/scenarios")
@login_required
@roles_required(*EDITOR_ROLES)
def run_scenario():
    """Run a what-if on a region and save it in the scenarios table."""
    data = json_body()
    code = data.pop("region", None)
    if not isinstance(code, str):
        raise ApiError(400, "validation_failed", "Some fields are not valid.", {"region": "This field is required."})
    check_region(code)
    wi = validate_numbers(data, SCENARIO_SCHEMA, partial=True)
    p = fintr()["inputs"](code)
    volume = wi.pop("monthly_volume", fintr()["volume"](code))
    pct = {k: wi.get(f"{k}_pct", 0.0) for k in ("freight", "customs", "lease", "growth")}
    sp = m.apply_scenario(p, pct["freight"] / 100, pct["customs"] / 100, pct["lease"] / 100)
    sq = volume * (1 + pct["growth"] / 100)
    out = results(sp, sq)
    scenario_id = None
    if fintr()["use_db"]():
        scenario_id = repo.record_scenario(code, g.api_user["id"], volume, pct, out["break_even_volume"],
                                           out["monthly_saving_zar"], out["recommendation"])
    body = {"id": scenario_id, "region": code, "monthly_volume": volume, "scenario_volume": sq,
            "changes_pct": pct, "base": results(p, volume), "scenario": out,
            # the region page's sensitivity table and cost curve (same function the page uses)
            "analysis": fintr()["analysis"](p, volume, pct)}
    return jsonify(to_json(body)), 201


@bp.get("/scenarios/<int:scenario_id>")
@login_required
def get_scenario(scenario_id):
    require_db()
    scenario = repo.get_scenario(scenario_id)
    if scenario is None:
        raise ApiError(404, "not_found", f"No scenario with id {scenario_id}.")
    return jsonify(to_json(scenario))
# Scenarios are a record of what-if runs, so they are created and read but not edited:
# changing an old run would rewrite history.  To try other numbers, POST a new scenario.


# ---- products: create, read, update ---------------------------------------------------------------
PRODUCT_MONEY = ("ex_factory_zar", "landed_cost_zar", "retail_price_zar")


def validate_product(data, partial=False):
    """Check a product body.  Returns the clean values; all problems are reported together."""
    allowed = {"sku", "name", *PRODUCT_MONEY}
    problems = {k: "Unknown field." for k in sorted(set(data) - allowed)}
    out = {}
    for name, longest in (("sku", 30), ("name", 80)):
        if name not in data:
            if not partial:
                problems[name] = "This field is required."
            continue
        value = data[name]
        if not isinstance(value, str) or not value.strip():
            problems[name] = "Must be text."
        elif len(value.strip()) > longest:
            problems[name] = f"Must be {longest} characters or fewer."
        else:
            out[name] = value.strip().upper() if name == "sku" else value.strip()
    try:
        out.update(validate_numbers({k: data[k] for k in PRODUCT_MONEY if k in data},
                                    {k: (0, None) for k in PRODUCT_MONEY}, partial=partial))
    except ApiError as e:
        problems.update(e.details)
    if problems:
        raise ApiError(400, "validation_failed", "Some fields are not valid.", problems)
    return out


def check_product_prices(values):
    if values["landed_cost_zar"] < values["ex_factory_zar"]:
        raise ApiError(400, "validation_failed", "Some fields are not valid.",
                       {"landed_cost_zar": "Cannot be lower than ex_factory_zar."})


def duplicate_sku(error, sku):
    """The UNIQUE key on products.sku refused the write: say which SKU, as a 409."""
    if error.errno == DataRuleError.DUPLICATE:
        raise ApiError(409, "duplicate", f"A product with SKU '{sku}' already exists.")
    raise error


@bp.get("/products")
@login_required
def list_products():
    require_db()
    page, per_page, limit, offset = pagination_args()
    rows, total = repo.list_products(limit, offset)
    return jsonify(paginated(rows, total, page, per_page))


@bp.get("/products/<int:product_id>")
@login_required
def get_product(product_id):
    require_db()
    product = repo.get_product(product_id)
    if product is None:
        raise ApiError(404, "not_found", f"No product with id {product_id}.")
    return jsonify(to_json(product))


@bp.post("/products")
@login_required
@roles_required(*EDITOR_ROLES)
def create_product():
    require_db()
    values = validate_product(json_body())
    check_product_prices(values)
    try:
        new_id = repo.create_product(values)
    except DataRuleError as e:
        duplicate_sku(e, values["sku"])
    return jsonify(to_json(repo.get_product(new_id))), 201


@bp.put("/products/<int:product_id>")
@login_required
@roles_required(*EDITOR_ROLES)
def update_product(product_id):
    """Change one or more fields of a product (send only what changes)."""
    require_db()
    current = repo.get_product(product_id)
    if current is None:
        raise ApiError(404, "not_found", f"No product with id {product_id}.")
    changes = validate_product(json_body(), partial=True)
    if not changes:
        raise ApiError(400, "validation_failed", "Send at least one field to change.")
    check_product_prices({**{k: float(current[k]) for k in PRODUCT_MONEY}, **changes})
    try:
        repo.update_product(product_id, changes)
    except DataRuleError as e:
        duplicate_sku(e, changes.get("sku"))
    return jsonify(to_json(repo.get_product(product_id)))


# ---- Fintr's actual data (Step 3 tables) ----------------------------------------------------------
@bp.get("/orders")
@login_required
def list_orders():
    require_db()
    region = choice_arg("region", tuple(fintr()["regions"]))
    order_type = choice_arg("order_type", ORDER_TYPES)
    month_from, month_to = month_arg("from"), month_arg("to")
    page, per_page, limit, offset = pagination_args()
    rows, total = repo.list_orders(region, order_type, month_from, month_to, limit, offset)
    return jsonify(paginated(rows, total, page, per_page))


def validate_order(data, partial=False):
    """Check an order body.  Codes are used instead of ids so the API is easy to read:
    {"region": "us", "sku": "FINTR-01", "route": "SA-US-DIRECT", "order_month": "2026-11",
     "order_type": "Consumer", "units": 2, "shipment_id": null}"""
    choices = {"region": tuple(fintr()["regions"]), "route": ROUTES, "order_type": ORDER_TYPES}
    required = ("region", "sku", "route", "order_month", "order_type", "units")
    allowed = {*required, "shipment_id"}
    problems = {k: "Unknown field." for k in sorted(set(data) - allowed)}
    out = {}
    for name in required:
        if name not in data:
            if not partial:
                problems[name] = "This field is required."
            continue
        value = data[name]
        if name in choices:
            if value not in choices[name]:
                problems[name] = f"Must be one of: {', '.join(choices[name])}."
            else:
                out[name] = value
        elif name == "sku":
            if not isinstance(value, str) or not value.strip():
                problems[name] = "Must be text."
            else:
                out[name] = value.strip().upper()
        elif name == "order_month":
            try:
                out[name] = datetime.strptime(str(value), "%Y-%m").date()
            except ValueError:
                problems[name] = "Must be a month like 2026-11."
        elif name == "units":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems[name] = "Must be a whole number of 1 or more."
            else:
                out[name] = value
    if "shipment_id" in data:
        value = data["shipment_id"]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            problems["shipment_id"] = "Must be a shipment id or null."
        else:
            out["shipment_id"] = value
    if problems:
        raise ApiError(400, "validation_failed", "Some fields are not valid.", problems)
    return out


def check_order_is_consistent(order, previous=None):
    """Related data must agree: the product must exist, the route must deliver to the order's region,
    and an order sent from a stock point cannot use more units than the stock point has.
    previous is the order as it was before an update (its own units are given back first)."""
    problems = {}
    on_hand = repo.stock_on_hand_for_route(order["route"])
    if on_hand is not None:  # this route starts at a stock point, so the order uses its stock
        if previous and previous["route"] == order["route"]:
            on_hand += previous["units"]
        if order["units"] > on_hand:
            problems["units"] = f"Only {on_hand} units are in stock at the start of route {order['route']}."
    if not repo.product_exists(order["sku"]):
        problems["sku"] = f"No product with SKU '{order['sku']}'."
    if repo.route_destination_region(order["route"]) != order["region"]:
        problems["route"] = f"Route {order['route']} does not deliver to region '{order['region']}'."
    if problems:
        raise ApiError(400, "inconsistent", "The order does not match the related records.", problems)


@bp.get("/orders/<int:order_id>")
@login_required
def get_order(order_id):
    require_db()
    order = repo.get_order(order_id)
    if order is None:
        raise ApiError(404, "not_found", f"No order with id {order_id}.")
    return jsonify(to_json(order))


@bp.post("/orders")
@login_required
@roles_required(*EDITOR_ROLES)
def create_order():
    """Record a new order.  It immediately counts in the region's actual_demand (GET /api/regions/<code>)."""
    require_db()
    order = validate_order(json_body())
    check_order_is_consistent(order)
    new_id = repo.save_order(order)  # an unknown shipment_id -> 400 bad_reference (see errors.py)
    return jsonify(to_json(repo.get_order(new_id))), 201


@bp.put("/orders/<int:order_id>")
@login_required
@roles_required(*EDITOR_ROLES)
def update_order(order_id):
    """Change one or more fields of an order (send only what changes)."""
    require_db()
    current = repo.get_order(order_id)
    if current is None:
        raise ApiError(404, "not_found", f"No order with id {order_id}.")
    changes = validate_order(json_body(), partial=True)
    if not changes:
        raise ApiError(400, "validation_failed", "Send at least one field to change.")
    order = {k: current[k] for k in ("region", "sku", "route", "order_month", "order_type", "units",
                                     "shipment_id")}
    order.update(changes)
    check_order_is_consistent(order, previous=current)
    repo.save_order(order, order_id)
    return jsonify(to_json(repo.get_order(order_id)))


@bp.get("/shipments")
@login_required
def list_shipments():
    require_db()
    region = choice_arg("region", tuple(fintr()["regions"]))
    route = choice_arg("route", ROUTES)
    exception = bool_arg("exception")
    page, per_page, limit, offset = pagination_args()
    rows, total = repo.list_shipments(region, route, exception, limit, offset)
    for row in rows:
        row["had_exception"] = bool(row["had_exception"])
        row["units"] = None if row["units"] is None else int(row["units"])
    return jsonify(paginated(rows, total, page, per_page))


@bp.get("/shipments/<int:shipment_id>")
@login_required
def get_shipment(shipment_id):
    require_db()
    shipment = repo.get_shipment(shipment_id)
    if shipment is None:
        raise ApiError(404, "not_found", f"No shipment with id {shipment_id}.")
    shipment["had_exception"] = bool(shipment["had_exception"])
    return jsonify(to_json(shipment))


# ---- live KPIs for the dashboard (Step 5) --------------------------------------------------------
def _months_between(first, last):
    return (last.year - first.year) * 12 + last.month - first.month + 1


def _month_list(first, last):
    out, y, mth = [], first.year, first.month
    while (y, mth) <= (last.year, last.month):
        out.append(f"{y}-{mth:02d}")
        y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    return out


@bp.get("/kpis")
@login_required
def kpis():
    """Everything the dashboard's live KPI section shows, worked out from MySQL on every call, so the
    numbers change as soon as an order, product or input changes."""
    require_db()
    raw = repo.kpi_data()
    route_cost = {r["route"]: (float(r["cost"]), int(r["units"])) for r in raw["routes"]}

    def per_unit(route):
        cost, units = route_cost.get(route, (0.0, 0))
        return cost / units if units else None

    regions = []
    for code in fintr()["regions"]:
        demand = repo.demand_summary(code)
        p, plan_volume = fintr()["inputs"](code), fintr()["volume"](code)
        be = m.break_even_volume(p)
        actual = demand["consumer_units"] / demand["months"] if demand["months"] else 0.0
        regions.append({
            "code": code, "name": fintr()["name"](code),
            "consumer_orders": demand["consumer_orders"], "consumer_units": demand["consumer_units"],
            "avg_orders_per_month": demand["avg_orders_per_month"], "avg_units_per_month": round(actual, 2),
            "planning_volume": plan_volume, "break_even_volume": be,
            "actual_share_of_break_even": (actual / be) if be else None,
            "recommendation_at_actual_demand": m.recommend(p, actual, fintr()["margin"]),
            "recommendation_at_planning_volume": m.recommend(p, plan_volume, fintr()["margin"]),
            "direct_cost_per_unit_zar": per_unit(f"SA-{code.upper()}-DIRECT")})

    stock_points = []
    for s in repo.stock_levels():
        months = _months_between(s["first_restock"], s["last_month"]) if s["first_restock"] else 0
        monthly = s["units_out"] / months if months else 0.0
        # reorder point = expected demand during the restock lead time (transit days / 30)
        reorder_point = ceil(monthly * (s["lead_days"] or 0) / 30)
        stock_points.append({
            "name": s["name"], "region": s["region"], "units_in": s["units_in"], "units_out": s["units_out"],
            "on_hand": s["on_hand"], "avg_units_out_per_month": round(monthly, 2),
            "months_of_cover": round(s["on_hand"] / monthly, 1) if monthly else None,
            "reorder_point": reorder_point,
            "status": "Out of stock" if s["on_hand"] <= 0 else
                      "Restock now" if s["on_hand"] <= reorder_point else "OK"})

    months = sorted({row["month"] for row in raw["monthly"]})
    labels = _month_list(datetime.strptime(months[0], "%Y-%m"), datetime.strptime(months[-1], "%Y-%m")) if months else []
    series = {code: [0] * len(labels) for code in fintr()["regions"]}
    for row in raw["monthly"]:
        series[row["region"]][labels.index(row["month"])] = int(row["units"])

    ships = raw["shipments"]
    body = {
        "totals": {"products": raw["products"], "consumer_orders": raw["consumer"]["orders"],
                   "consumer_units": int(raw["consumer"]["units"]), "revenue_zar": raw["consumer"]["revenue"],
                   "shipments": ships["shipments"], "late_shipments": int(ships["late"]),
                   "late_rate": int(ships["late"]) / ships["shipments"] if ships["shipments"] else None,
                   "courier_spend_zar": raw["courier_spend_zar"]},
        "regions": regions, "stock_points": stock_points,
        "unit_cost_by_route_zar": {"direct_to_us_customer": per_unit("SA-US-DIRECT"),
                                   "bulk_to_us_stock_point": per_unit("SA-US-STOCK")},
        "monthly_units": {"months": labels, **series},
        "updated_at": datetime.now().isoformat(timespec="seconds")}
    return jsonify(to_json(body))


# ---- PDF report log -------------------------------------------------------------------------------
@bp.get("/reports")
@login_required
def list_reports():
    require_db()
    region = choice_arg("region", tuple(fintr()["regions"]))
    page, per_page, limit, offset = pagination_args()
    rows, total = repo.list_reports(region, limit, offset)
    return jsonify(paginated(rows, total, page, per_page))
