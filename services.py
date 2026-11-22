"""Shared helpers for the website pages and the REST API (api/).

They read the region inputs, settings and exchange rates (from MySQL when USE_DATABASE is on,
otherwise from the defaults below) and run the break-even model for the region pages.
Keeping them here means pages.py only holds routes and app.py only sets the app up."""
from collections import OrderedDict
from dataclasses import fields

from flask import current_app, g, session

import repository as repo  # all the MySQL queries (see repository.py)
from models import breakeven as m

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
