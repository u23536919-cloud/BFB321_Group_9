"""The website pages (a Flask Blueprint called "pages").

Every page route lives here; app.py only creates the app and registers this blueprint and the REST
API blueprint (api/).  The shared helpers are in services.py, the model in models/breakeven.py
and the MySQL queries in repository.py.  Step 5 pages (orders, products, live KPIs) are filled
in the browser from the REST API (static/js/api.js)."""
from dataclasses import fields, replace
from datetime import date
from io import BytesIO
from math import isfinite

from flask import (Blueprint, current_app, flash, redirect, render_template, request, send_file,
                   session, url_for)
from werkzeug.security import check_password_hash

import repository as repo
from api.auth import EDITOR_ROLES, create_token
from db import DatabaseError
from models import breakeven as m
from report_pdf import build_report_pdf
from services import (CUR, MAIN_FIELDS, MARGIN, REGIONS, SHIPMENT_COST_FIELD, build_results, current_user_id,
                      display_rate, factory_settings, freight_change, refresh_snapshot, region_choices,
                      region_inputs, region_name, region_state, region_volume, setting_fields,
                      settings_values, use_db)

bp = Blueprint("pages", __name__)


@bp.app_template_filter("n")
def n(v, d=0):
    return "n/a" if v is None else f"{v:,.{d}f}"

@bp.route("/")
def index():
    return redirect(url_for("pages.login"))

# ---- login (Step 5: real passwords) ---------------------------------------------------------
# With the database on, the email and password are checked against the users table (the
# password is stored only as a salted hash).  The user's role comes from the database.  The login
# also creates an API token, which the pages use to call the REST API (api/).
# With USE_DATABASE=false (demo mode, no accounts) any email works and the role is picked.
@bp.route("/login", methods=["GET", "POST"])
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
                current_app.logger.warning("Failed website login for %s", email)
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
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("pages.dashboard"))
    return render_template("login.html", email="")

@bp.route("/logout")
def logout():
    session.clear()
    flash("You are logged out.", "success")
    return redirect(url_for("pages.login"))

# With the database on, every page needs a login (the API checks its own token).
@bp.before_app_request
def require_login():
    open_pages = {"pages.login", "pages.logout", "pages.index", "static"}
    if request.endpoint is None or request.path.startswith("/api/"):
        return None  # unknown URL (normal 404) or the API, which checks its own token and answers in JSON
    if use_db() and request.endpoint not in open_pages and "user_id" not in session:
        return redirect(url_for("pages.login", next=request.full_path.rstrip("?")))

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
@bp.app_context_processor
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

@bp.route("/dashboard")
def dashboard():
    kpi, curve = overview()
    return render_template("dashboard.html", kpi=kpi, curve=curve, page="dashboard",
                           updated=date.today().strftime("%d %b %Y")) # uses kpi and curve to create and return the dashboard.html page 
# the function also displays the date and page name 

# Step 5: these two pages are filled in the browser from the REST API (see static/js/api.js)
@bp.route("/orders")
def orders():
    return render_template("orders.html", page="orders")

@bp.route("/products")
def products():
    return render_template("products.html", page="products")

@bp.route("/settings", methods=["GET", "POST"])
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
            return redirect(url_for("pages.settings"))
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
            return redirect(url_for("pages.settings"))
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

@bp.route("/alerts")
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

@bp.route("/reports")

#this function shows the report preview page 
def reports():
    code, show = report_args()
    d = report_data(code, show)
    return render_template("reports.html", d=d, s=d["s"], regions=region_choices(), code=code, page="reports", show=show,
                           saving=d["saving"], costs={"direct": d["direct"], "warehouse": d["warehouse"]},
                           analysis=ANALYSIS, generated=bool(request.args.get("g")), today=d["date"])

@bp.route("/reports/pdf")
# creates and downloads the PDF version of the report
def reports_pdf():
    code, show = report_args()
    if not show:
        flash("Select at least one analysis option before exporting.", "warning")
        return redirect(url_for("pages.reports", region=code, f=1))
    d = report_data(code, show)
    name = f"Fintr_report_{code.upper()}_{date.today().isoformat()}.pdf"
    if use_db():  # keep a log of every report that was generated
        repo.record_report(code, current_user_id(), ",".join(k for k, _ in ANALYSIS if k in show), name)
    return send_file(BytesIO(build_report_pdf(d)), mimetype="application/pdf", as_attachment=True,
                     download_name=name)


#the entire build_results function speaks to the alerts page. It shows a leaderboard of regions by progress - progress is tracking the amount that is required to reach the break even point. 
@bp.route("/region/<code>", methods=["GET", "POST"])
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
