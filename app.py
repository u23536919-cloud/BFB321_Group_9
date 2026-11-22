"""We used AI to help build our frontend model.
The break-even cost inputs on the region pages are still default values. Fintr's actual orders, shipments and invoices
(data pack, 30 October 2026) are in MySQL and drive the dashboard's live KPIs.
Step 1 demo server. Pages call the Step 2 model directly. Step 3 added MySQL (see db.py, repository.py,
database/). Step 4 added the REST API: a Blueprint in api/ with JWT login (see README, API section).
Step 5 wired the pages to the API (static/js/api.js) and made the website login check real passwords."""
from collections import OrderedDict
from datetime import date
from dataclasses import fields, replace
from math import isfinite
from io import BytesIO
from flask import Flask, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
import logging
import repository as repo  # all the MySQL queries (see repository.py)
from config import get_config  # settings per environment (development / testing / production)
from db import DatabaseError  # raised when MySQL is down or a query fails
from models import breakeven as m
from report_pdf import build_report_pdf
from api import init_api  # Step 4: REST API blueprint (api/)
from api.auth import EDITOR_ROLES, create_token
from werkzeug.security import check_password_hash

REGIONS = {  
    "uk": {"name": "United Kingdom", "volume": 780, "over": {}},
    "us": {"name": "United States", "volume": 1050, "over": {
        "direct_freight_zar": 297.0, "local_delivery_zar": 93.6, "rent_zar_month": 57600.0}},
}
#The above code displays the default values for the different regions, on the website. The user can change these values on the scenario comparison tabs.

MARGIN = 0.10
DEFAULT_ZAR_PER_USD = 18.0
DEFAULT_ZAR_PER_GBP = 23.5
MAIN_FIELDS = ("direct_freight_zar", "setup_zar", "local_delivery_zar", "rent_zar_month")
INV_FIELDS = [f.name for f in fields(m.RegionInputs) if f.metadata["group"] == m.G_I]
SHIPMENT_COST_FIELD = "inbound_shipment_fixed_zar"
REGIONAL_SHIPMENT_FIELDS = tuple(
    (f"{SHIPMENT_COST_FIELD}_{code}", f"Fixed cost per inbound shipment (S) - {REGIONS[code]['name']}")
    for code in REGIONS
)
CUR = {"uk": {"code": "GBP", "axis": "£", "name": "British pounds"},
       "us": {"code": "USD", "axis": "USD", "name": "US dollars"}}

#The above code defines the constants and units for the rest of the code, for the different regions, on the website. The user can change these values on the scenario comparison tabs.

# ---- MySQL helpers -------------------------------------------------------------------------
# When USE_DATABASE is true the values come from MySQL (see database/schema.sql and seed.sql);
# the REGIONS dictionary above is then only the fallback used by the unit tests.
def use_db():
    return current_app.config.get("USE_DATABASE", False)


# snapshot() reads everything from MySQL once per request and keeps it in flask's g object,
# so a page does not run the same queries again and again.
def snapshot():
    if "snapshot" not in g:
        g.snapshot = repo.load_snapshot()
    return g.snapshot


# after we save something we throw the cached copy away so the next read sees the new values.
def refresh_snapshot():
    g.pop("snapshot", None)


def region_name(code):
    return snapshot()["regions"][code]["name"] if use_db() else REGIONS[code]["name"]


def region_choices():
    """{code: {"name": ...}} for the report page drop-down."""
    if use_db():
        return {c: {"name": r["name"]} for c, r in snapshot()["regions"].items() if c in REGIONS}
    return REGIONS


def current_user_id():
    return session.get("user_id") if use_db() else None


# the factory settings (used by the Reset button): code defaults, with shipment costs in ZAR.
def factory_settings():
    vals = {f["name"]: f["default"] for f in setting_fields() if f["group"] != "regional"}
    shipments = {code: m.RegionInputs.inbound_shipment_fixed_zar for code in REGIONS}
    return vals, shipments


# db_settings_values() puts the saved MySQL values on top of the defaults.
def db_settings_values(vals):
    data = snapshot()
    vals["fx_zar_per_usd"] = data["rates"]["USD"]
    vals["zar_per_gbp"] = data["rates"]["GBP"]
    for name, value in data["assumptions"].items():
        if name in vals:
            vals[name] = value
    for code in REGIONS:
        vals[f"{SHIPMENT_COST_FIELD}_{code}"] = data["inputs"][code]["inbound_shipment_fixed_zar"]
    return vals

#The def setting_fields() function is used to display the default values for the different inventory and replenishment fields on the website. 
def setting_fields():
    """Inputs shown on the Settings page: exchange rates, then inventory and replenishment."""
    by_name = {f.name: f for f in fields(m.RegionInputs)}
    out = [{"name": "fx_zar_per_usd", "label": "Exchange rate", "unit": "ZAR per USD", "group": "rates", "default": DEFAULT_ZAR_PER_USD},
           {"name": "zar_per_gbp", "label": "Exchange rate", "unit": "ZAR per GBP", "group": "rates", "default": DEFAULT_ZAR_PER_GBP}]
    #This is displaying the exchange rates, for pounds and dollars.
    
    
    #The inventory and replenishment fields are the different costs associated with shipping and storing products in different regions.
    for n in INV_FIELDS:
        if n == SHIPMENT_COST_FIELD:
            continue
        f = by_name[n]
        out.append({"name": n, "label": f.metadata["label"], "unit": f.metadata["unit"], "group": "inv", "default": f.default})
    shipment = by_name[SHIPMENT_COST_FIELD]

    # This for loop is displaying the default values for the different shipment costs, on the website. The user can change these values on the settings page.
    for name, label in REGIONAL_SHIPMENT_FIELDS:
        code = name.rsplit("_", 1)[-1]
        unit = "GBP" if code == "uk" else "USD"
        rate = DEFAULT_ZAR_PER_GBP if code == "uk" else DEFAULT_ZAR_PER_USD
        out.append({"name": name, "label": label, "unit": unit, "group": "regional",
                    "default": shipment.default / rate})
    return out


# The def settings_values() function is used to display the default values but the user can change the values.
# The function will then update to use the new values that the user inputted .
def settings_values():
    """Saved assumptions on top of the defaults: from MySQL, or from the session when the database is off."""
    vals = {f["name"]: f["default"] for f in setting_fields()}
    if use_db():
        return db_settings_values(vals)
    regional_names = {name for name, _ in REGIONAL_SHIPMENT_FIELDS}
    for name, _ in REGIONAL_SHIPMENT_FIELDS:
        vals[name] = m.RegionInputs.inbound_shipment_fixed_zar
    saved = session.get("assump", {})
    vals.update({k: v for k, v in saved.items() if k in vals and k not in regional_names})
    fx = vals["fx_zar_per_usd"]
    legacy = saved.get("inbound_shipment_fixed_usd")
    for code in REGIONS:
        name = f"{SHIPMENT_COST_FIELD}_{code}"
        old_name = f"inbound_shipment_fixed_usd_{code}"
        if name in saved:
            vals[name] = saved[name]
        elif old_name in saved:
            vals[name] = saved[old_name] * fx
        elif legacy is not None:
            vals[name] = legacy * fx
    return vals


# The def region_state() function is used to display the state we are in.
def region_state(code):
    if use_db():
        # the saved inputs and volume live in MySQL; only the what-if slider positions stay in the session
        row = snapshot()["inputs"][code]
        state = {name: row[name] for name in MAIN_FIELDS}
        state["volume"] = row["monthly_volume"]
        state["wi"] = session.get("state", {}).get(code, {}).get("wi", {})
        return state
    state = dict(session.get("state", {}).get(code, {}))
    fx = settings_values()["fx_zar_per_usd"]
    legacy_fields = {
        "direct_freight_usd": "direct_freight_zar",
        "setup_usd": "setup_zar",
        "local_delivery_usd": "local_delivery_zar",
        "rent_usd_month": "rent_zar_month",
    }
    for old_name, name in legacy_fields.items():
        if old_name in state and name not in state:
            state[name] = state[old_name] * fx
    return state


# In the def region_inputs() function, it combines and adds the required the default values for the different regions based on what already given above, on the website.
# The user can change these values on the scenario comparison tabs.
def region_inputs(code):
    settings = settings_values()

# The values is a variable that is used to store the default values for the different regions, on the website.
    values = {
        **REGIONS[code]["over"],
        **{
            name: settings[name]
            for name in INV_FIELDS if name != SHIPMENT_COST_FIELD
        },
    }

# The shipment_cost variable is used to store the default values for the different shipment costs, on the website.
    shipment_cost = settings[f"{SHIPMENT_COST_FIELD}_{code}"]
    values[SHIPMENT_COST_FIELD] = shipment_cost

# The code will overwite the default value with the values that the user inputs.
    values.update({
        name:value
        for name, value in region_state(code).items()
        if name in MAIN_FIELDS
    })

    return m.RegionInputs(**values)


# the def region volume function updates the volume of the specific region with whatever the user inputs it as.
def region_volume(code):
    return region_state(code).get("volume", REGIONS[code]["volume"])


# The def display_rate() function converts ZAR inputs to the currency shown on that region page.
def display_rate(code):
    """Convert ZAR model inputs to the currency shown on that region page."""
    s = settings_values()
    return 1 / s["zar_per_gbp"] if code == "uk" else 1 / s["fx_zar_per_usd"]

# def freight_change works out how much the freight rate has changed (in %) compared to the starting value.
def freight_change(code):
    """% change of the current freight rate (saved input and freight slider) against the starting rate."""
    st = region_state(code)
    #base is the starting freight rate.
    if use_db():
        base = snapshot()["inputs"][code]["baseline_freight_zar"]
    else:
        base = REGIONS[code]["over"].get("direct_freight_zar", m.RegionInputs.direct_freight_zar)
    if not st or not base:
        return 0.0 #If nothing has been inputted, the freight rate change will be 0.
    #if the user has inputted a new freight rate, the function will calculate the % change of the current freight rate against the starting rate.
    return (st["direct_freight_zar"] * (1 + st.get("wi", {}).get("freight", 0) / 100) / base - 1) * 100


#This function groups the models fields by their metadata group, so that they can be displayed in the settings page in a more organized way.
def grouped_fields():
    groups = OrderedDict()
    for f in fields(m.RegionInputs):
        groups.setdefault(f.metadata["group"], []).append(f) #'make the group if its new, then add the field.
    return groups


#This function sets up the Flask app and all the pages (routes) inside it. 
def create_app(config_name=None, **overrides):
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))  # development / testing / production (config.py)
    app.config.update(overrides)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")
    # log lines have a time, a level and the message; LOG_FILE in .env also keeps them in a file
    handlers = [logging.StreamHandler()]
    if app.config.get("LOG_FILE"):
        handlers.append(logging.FileHandler(app.config["LOG_FILE"], encoding="utf-8"))
    logging.basicConfig(level=app.config["LOG_LEVEL"], format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)

    # if MySQL is down we show a friendly page instead of a raw error
    @app.errorhandler(DatabaseError)
    def database_problem(e):
        app.logger.error("Database problem: %s", e)
        return render_template("error.html", title="Database unavailable", msg=str(e)), 503

    
    # Step 4: the REST API under /api (see the api/ package).  The API reuses the same helpers as
    # the pages, so both always show the same numbers.
    app.extensions["fintr"] = {"inputs": region_inputs, "volume": region_volume, "name": region_name,
                               "use_db": use_db, "refresh": refresh_snapshot, "regions": REGIONS,
                               "margin": MARGIN}
    init_api(app)

    @app.template_filter("n")
    def n(v, d=0):
        return "n/a" if v is None else f"{v:,.{d}f}"

    @app.route("/")
    def index():
        return redirect(url_for("login"))

    # ---- login (Step 5: real passwords) ---------------------------------------------------------
    # With the database on, the email and password are checked against the users table (the
    # password is stored only as a salted hash).  The user's role comes from the database.  The login
    # also creates an API token, which the pages use to call the REST API (api/).
    # With USE_DATABASE=false (demo mode, no accounts) any email works and the role is picked.
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()[:120]
            password = request.form.get("password", "")
            if not email or not password:
                flash("Enter your work email and password.", "danger")
                return render_template("login.html", email=email), 400
            if use_db():
                user = repo.find_user_by_email(email)
                if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
                    app.logger.warning("Failed website login for %s", email)
                    flash("Wrong email or password.", "danger")
                    return render_template("login.html", email=email), 401
                session.clear()
                session.update(user_id=user["id"], name=user["name"], role=user["role"], email=user["email"],
                               api_token=create_token(user))
            else:
                session.clear()
                session.update(name=email.split("@")[0], role=request.form.get("role", "Finance")[:40], email=email)
            nxt = request.args.get("next", "")
            # only go back to a page on this site (never to another website)
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        return render_template("login.html", email="")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You are logged out.", "success")
        return redirect(url_for("login"))

    # With the database on, every page needs a login (the API checks its own token).
    @app.before_request
    def require_login():
        open_pages = {"login", "logout", "static", "index"}
        if (use_db() and request.endpoint not in open_pages and not (request.endpoint or "").startswith("api.")
                and "user_id" not in session):
            return redirect(url_for("login", next=request.full_path.rstrip("?")))

    #This function collects the key numbers for one region (break-even, cost per unit, progress and recommendations)
    def summary(code):
        p, q = region_inputs(code), region_volume(code)
        be = m.break_even_volume(p)
        return {"code": code, "name": region_name(code), "q": q, "be": be,
                "direct_unit": m.total_cost_direct(p, q) / q if q else None,
                "wh_unit": m.total_cost_warehouse(p, q) / q if q else None,
                "progress": m.progress_to_break_even(q, be), "rec": m.recommend(p, q, MARGIN)}


    #This functions creates the list of alert messages on the alerts and monitoring page.
    def active_alerts():
        out = []
        #go through the regions and check if they are approaching or have reached break-even, and add an alert message to the list if they are.
        for r in sorted((summary(c) for c in REGIONS), key=lambda r: r["progress"] or 0, reverse=True):
            pct = round((r["progress"] or 0) * 100)
            if r["be"] is None:
                out.append(f"{r['name']}: warehouse costs more per unit than direct shipping, so there is no break-even")
            elif pct >= 100:
                out.append(f"{r['name']} has reached break-even: review regional warehousing")
            elif pct >= 80:
                out.append(f"{r['name']} approaching break-even threshold")
        #check if the freight rate changed and add an alert message to the alerts page if it did.
        for code in REGIONS:
            ch = freight_change(code)
            if abs(ch) >= 0.05:
                amount = f"{abs(ch):.1f}".rstrip("0").rstrip(".") # removes the spaces and trailing zeros from the number, so that it looks cleaner on the page.
                out.append(f"{code.upper()} freight rate {'increased' if ch > 0 else 'decreased'} {amount}%")
        return out

    
    #shell() adds the users name , role and alert count to every template automatically, so we dont have to enter them manually on every page.
    @app.context_processor
    def shell():
        try:
            count = len(active_alerts())
        except DatabaseError:  # database down: still show the error page
            count = 0
        return {"user_name": session.get("name", ""), "user_role": session.get("role", ""),
                "alert_count": count, "api_token": session.get("api_token", ""),
                "api_ready": use_db() and bool(session.get("api_token")),
                "can_edit": session.get("role") in EDITOR_ROLES}

    #overview() works out the combined UK + US numbers shown on the dashboard
    def overview():
        """Combined UK + US picture. Total volume is split between regions in today's proportions;
        each region keeps its own warehouse, so costs are summed region by region."""
        parts = [(region_inputs(c), region_volume(c)) for c in REGIONS]
        # The total variable is used to store the total volume of all the regions combined.
        # The tot variable is used to store the total volume or 1 if the total volume is 0.
        total = sum(q for _, q in parts)
        tot = total or 1

       
         #Adds up a cost function across both regions, giving each region its share volume
        def cost(fn, vol):
            return sum(fn(p, vol * q / tot) for p, q in parts) 

        def gap(vol):  # Warehouse minus direct: positive means shipping from SA is cheaper
            return cost(m.total_cost_warehouse, vol) - cost(m.total_cost_direct, vol)

        be = None
        hi = max(total, 1.0)
# hi is a variable that represents the largest order volume the code is currently checking while it searches for the break-even point.
# hi starts at a specific order volume and doubles until break even is reached - when doing this the direct cost and warehouse cost updates and an analysis is made on which option is cheaper between direct shipping and warehouse.
# after doing this, it determines the order volume where the break even lies, this estimated volume is stored in the variable be.
        while gap(hi) > 0 and hi < 1e7: 
            hi *= 2
        if gap(hi) <= 0:
            lo = 0.0
            for _ in range(60):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if gap(mid) > 0 else (lo, mid)
            be = hi

        top = max(total * 1.3, (be or 0) * 1.5, 100)
        pts = 12
        curve = [{"q": round(i * top / pts), "direct": cost(m.total_cost_direct, i * top / pts) / 1000,
                  "warehouse": cost(m.total_cost_warehouse, i * top / pts) / 1000} for i in range(pts + 1)]
        kpi = {"volume": total, # kpi is a small summary of important numbers in the dashboard page like total order volume, average direct-shipping cost, average warehouse-shipping cost and break-even order volume.
               "direct_zar": cost(m.total_cost_direct, total) / total if total else None,
               "wh_zar": cost(m.total_cost_warehouse, total) / total if total else None, "be": be}
        return kpi, curve

    @app.route("/dashboard")
    def dashboard():
        kpi, curve = overview()
        return render_template("dashboard.html", kpi=kpi, curve=curve, page="dashboard",
                               updated=date.today().strftime("%d %b %Y")) # uses kpi and curve to create and return the dashboard.html page 
    # the function also displays the date and page name 

    # Step 5: these two pages are filled in the browser from the REST API (see static/js/api.js)
    @app.route("/orders")
    def orders():
        return render_template("orders.html", page="orders")

    @app.route("/products")
    def products():
        return render_template("products.html", page="products")

    @app.route("/settings", methods=["GET", "POST"])
    # this functions gives information to the settings page
    def settings():
        fields_ = setting_fields()
        if request.method == "POST":
            if "reset" in request.form: # the reset button removes saved assumptions 
                if use_db():
                    vals, shipments = factory_settings()
                    repo.save_settings(vals, shipments, current_user_id())
                    refresh_snapshot()
                else:
                    session.pop("assump", None)
                flash("Assumptions reset to the defaults.", "success")
                return redirect(url_for("settings"))
            try: # if  it does not reset, it converts it to a number, checks if the numbers are valid, displays the required currency and saves the new assumptions given all the values are valid
                new = {}
                for f in fields_:
                    v = float(request.form[f["name"]])
                    name, where = f["name"], f"{f['label']} ({f['unit']})" if f["group"] == "rates" else f["label"]
                    if not isfinite(v) or v < 0:
                        raise m.ModelError(f"{where} must be a number of 0 or more.")
                    if name in ("fx_zar_per_usd", "zar_per_gbp", "unit_cost_zar", "holding_rate_annual") and v <= 0:
                        raise m.ModelError(f"{where} must be above 0.")
                    if name == "holding_rate_annual" and v > 1:
                        raise m.ModelError(f"{where} must be between 0 and 1.")
                    new[name] = v
                current = settings_values()
                for code in REGIONS:
                    name = f"{SHIPMENT_COST_FIELD}_{code}"
                    old_rate = current["zar_per_gbp"] if code == "uk" else current["fx_zar_per_usd"]
                    if abs(new[name] - round(current[name] / old_rate, 2)) < 0.005:
                        new[name] = current[name]
                    else:
                        rate = new["zar_per_gbp"] if code == "uk" else new["fx_zar_per_usd"]
                        new[name] *= rate
                model_fields = {f.name for f in fields(m.RegionInputs)}
                m.RegionInputs(**{k: v for k, v in new.items() if k in model_fields})
                if use_db():
                    shipments = {code: new[f"{SHIPMENT_COST_FIELD}_{code}"] for code in REGIONS}
                    repo.save_settings(new, shipments, current_user_id())  # saved in MySQL
                    refresh_snapshot()
                else:
                    session["assump"] = new
                flash("Assumptions saved.", "success")
                return redirect(url_for("settings"))
            except (ValueError, KeyError) as e:
                flash(str(e) if isinstance(e, m.ModelError) else "Enter a number in every assumption field.", "danger")
        vals = settings_values()
        regional = []
        for f in fields_:
            if f["group"] != "regional":
                continue
            code = f["name"].rsplit("_", 1)[-1]
            rate = vals["zar_per_gbp"] if code == "uk" else vals["fx_zar_per_usd"]
            regional.append(dict(f, value=round(vals[f["name"]] / rate, 2)))
        return render_template("settings.html", page="settings", margin=int(MARGIN * 100),
                               rates=[dict(f, value=vals[f["name"]]) for f in fields_ if f["group"] == "rates"],
                               inv=[dict(f, value=vals[f["name"]]) for f in fields_ if f["group"] == "inv"],
                               regional=regional)

    @app.route("/alerts")
    def alerts():
        rows = sorted((summary(c) for c in REGIONS), key=lambda r: r["progress"] or 0, reverse=True)
        return render_template("alerts.html", rows=rows, alerts=active_alerts(), page="alerts")
# this function displays the alerts page: it gives a summary of each region, and shows the active alerts 


    ANALYSIS = [("be", "Break even volume"), ("cost", "Current vs Alternative cost"),
                ("savings", "Potential savings"), ("rec", "Recommendation")]

    def report_data(code, show):
        """Everything the report preview and the PDF need, so both always say the same thing."""
        p, q = region_inputs(code), region_volume(code)
        s = summary(code)
        saving, threshold = m.monthly_saving(p, q), m.decision_threshold(p, MARGIN)
        if s["be"] is None:
            text = "The warehouse costs more per unit than direct shipping, so volume alone will not make it cheaper."
        else:
            text = (f"Current volume is {q:,.0f} units per month, which is {round((s['progress'] or 0) * 100)}% of the "
                    f"break-even volume of {s['be']:,.0f}. With a {int(MARGIN * 100)}% margin, the decision "
                    f"threshold is {threshold:,.0f}.")
        text += (f" At the current volume a regional warehouse would save about ZAR {saving:,.0f} per month."
                 if saving >= 0 else
                 f" At the current volume a regional warehouse would cost about ZAR {-saving:,.0f} more per month "
                 f"than shipping from SA.")
        who = " - ".join(x for x in (session.get("name", ""), session.get("role", "")) if x)
        return {"region": s["name"], "date": date.today().strftime("%d %b %Y"), "user": who, "show": show,
                "labels": [label for key, label in ANALYSIS if key in show], "q": q, "be": s["be"], "saving": saving,
                "direct": m.total_cost_direct(p, q), "warehouse": m.total_cost_warehouse(p, q), "rec": s["rec"],
                "rec_text": text, "threshold": threshold, "s": s}
# this gathers the information needed for the pdf report for one region 

    def report_args():
        code = request.args.get("region", "uk")
        code = code if code in REGIONS else "uk"
        # This allows the user to pick what they want to be shown in the generated report.
        keys = {k for k, _ in ANALYSIS}
        show = {k for k in request.args.getlist("show") if k in keys} if request.args.get("f") else keys
        return code, show

    @app.route("/reports")

    #this function shows the report preview page 
    def reports():
        code, show = report_args()
        d = report_data(code, show)
        return render_template("reports.html", d=d, s=d["s"], regions=region_choices(), code=code, page="reports", show=show,
                               saving=d["saving"], costs={"direct": d["direct"], "warehouse": d["warehouse"]},
                               analysis=ANALYSIS, generated=bool(request.args.get("g")), today=d["date"])

    @app.route("/reports/pdf")
    # creates and downloads the PDF version of the report
    def reports_pdf():
        code, show = report_args()
        if not show:
            flash("Select at least one analysis option before exporting.", "warning")
            return redirect(url_for("reports", region=code, f=1))
        d = report_data(code, show)
        name = f"Fintr_report_{code.upper()}_{date.today().isoformat()}.pdf"
        if use_db():  # keep a log of every report that was generated
            repo.record_report(code, current_user_id(), ",".join(k for k, _ in ANALYSIS if k in show), name)
        return send_file(BytesIO(build_report_pdf(d)), mimetype="application/pdf", as_attachment=True,
                         download_name=name)


#the entire build_results function speaks to the alerts page. It shows a leaderboard of regions by progress - progress is tracking the amount that is required to reach the break even point. 
# it also shows a list of active alerts 
    def build_results(p, vol, wi):
        """Everything the region page shows; all model costs are in ZAR."""
        sp = m.apply_scenario(p, wi["freight"] / 100, wi["customs"] / 100, wi["lease"] / 100)
        sq = vol * (1 + wi["growth"] / 100)
        be = m.break_even_volume(p)
        base_saving = m.monthly_saving(p, vol)

        def row(label, pp, qq, base=False):
            # change in the monthly saving from a warehouse versus the base case (postive value means that Fintr should favour the warehouse)
            impact = None if base else m.monthly_saving(pp, qq) - base_saving
            return {"label": label, "be": m.break_even_volume(pp), "impact": impact,
                    "rec": m.recommend(pp, qq, MARGIN)}

        scen = [row("Base case", p, vol, base=True),
                row("Freight rate +10%", m.apply_scenario(p, freight_pct=0.10), vol),
                row("Freight rate -10%", m.apply_scenario(p, freight_pct=-0.10), vol),
                row("Volume growth +10%", p, vol * 1.10),
                row("Volume growth -10%", p, vol * 0.90),
                row("Warehouse lease +10%", m.apply_scenario(p, lease_pct=0.10), vol),
                row("Warehouse lease -10%", m.apply_scenario(p, lease_pct=-0.10), vol)]
        if any(wi.values()):
            scen.append(row("Your what-if (sliders)", sp, sq))
        top = max(vol, sq, (be or 0) * 1.5, 100)
        return {"be": be, "threshold": m.decision_threshold(p, MARGIN), "rec": m.recommend(p, vol, MARGIN),
                "saving": m.monthly_saving(p, vol), "curve": m.cost_curve(p, top, 13), "scen": scen}

    app.extensions["fintr"]["analysis"] = build_results  # the API sends the same table + chart data

    @app.route("/region/<code>", methods=["GET", "POST"])
    def region(code):
        if code not in REGIONS:
            return render_template("error.html", msg="That region is not set up yet."), 404
        rate = display_rate(code)  # ZAR -> region currency
        p, vol = region_inputs(code), region_volume(code)
        wi = {k: region_state(code).get("wi", {}).get(k, 0) for k in ("freight", "customs", "lease", "growth")}
        calc = None
        if request.method == "POST":
            try:
                new = {}
                for k in MAIN_FIELDS:  # a value left as displayed keeps its exact stored amount (no rounding drift)
                    shown, now = float(request.form[k]), getattr(p, k)
                    new[k] = now if abs(shown - round(now * rate, 2)) < 0.005 else shown / rate
                nvol = float(request.form["volume"])
                nwi = {k: float(request.form.get("wi_" + k, 0)) for k in wi}
                if not all(isfinite(v) for v in [nvol, *nwi.values(), *new.values()]):
                    raise m.ModelError("Enter numbers only.")
                np_ = replace(p, **new)
                calc = build_results(np_, nvol, nwi)
                p, vol, wi = np_, nvol, nwi
                if use_db():
                    # save the new inputs and volume, and keep this what-if run in the scenarios table
                    sp = m.apply_scenario(p, wi["freight"] / 100, wi["customs"] / 100, wi["lease"] / 100)
                    sq = vol * (1 + wi["growth"] / 100)
                    repo.save_region_inputs(code, new, vol, current_user_id())
                    repo.record_scenario(code, current_user_id(), vol, wi, m.break_even_volume(sp),
                                         m.monthly_saving(sp, sq), m.recommend(sp, sq, MARGIN))
                    refresh_snapshot()
                state = session.get("state", {})
                state[code] = {**{k: getattr(p, k) for k in MAIN_FIELDS}, "volume": vol, "wi": wi}
                session["state"] = state
            except (ValueError, KeyError) as e:
                flash(str(e) if isinstance(e, m.ModelError) else "Check every field: enter numbers only.", "danger")
        if calc is None:
            calc = build_results(p, vol, wi)
        vals = {k: round(getattr(p, k) * rate, 2) for k in MAIN_FIELDS}
        zar_vals = {k: getattr(p, k) for k in MAIN_FIELDS}  # exact stored ZAR values (for the API)
        return render_template("region.html", code=code, name=region_name(code), vals=vals, vol=vol, wi=wi,
                               cur=CUR[code], page=code, margin=int(MARGIN * 100), calc=calc,
                               rate=rate, zar_vals=zar_vals)
# the above region function allows users to fill in the relevant data with their own values. 
# Once they click "Calculate", this function will use those input values to recalculate the results, updates the graph and sensitivity analysis and displays the updated page
   
    return app

# this starts the web app when app.py runs directly
if __name__ == "__main__":
    application = create_app()
    # debug (with its error pages) only in development: production never shows stack traces
    application.run(debug=application.config["DEBUG"])
