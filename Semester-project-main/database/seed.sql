-- =====================================================================
-- Fintr break-even tool (BFB 321, Group 9)  -  demo seed data
-- Run AFTER schema.sql (python db.py init runs both).
-- The model inputs at the top (region_cost_inputs, scenarios, reports) are
-- illustrative placeholders.  The section at the bottom is Fintr's ACTUAL data.
-- Parent tables are filled before the child tables that point to them.
-- =====================================================================

INSERT INTO roles (name) VALUES
    ('Finance'),
    ('Supply chain planner'),
    ('Senior management');

-- Demo password for all three users: demo123 (stored only as a salted hash)
INSERT INTO users (name, email, password_hash, role_id) VALUES
    ('Jiara Martins',  'jiara.martins@fintr.com',  'pbkdf2:sha256:1000000$BmNpFR5pNLUKonQg$3c94aedf8f4b5290e1cf1ec356374377ce6bf505ab2555d9a1958077b588b087', (SELECT id FROM roles WHERE name = 'Finance')),
    ('Thabo Nkosi',    'thabo.nkosi@fintr.com',    'pbkdf2:sha256:1000000$BmNpFR5pNLUKonQg$3c94aedf8f4b5290e1cf1ec356374377ce6bf505ab2555d9a1958077b588b087', (SELECT id FROM roles WHERE name = 'Supply chain planner')),
    ('Lerato Dlamini', 'lerato.dlamini@fintr.com', 'pbkdf2:sha256:1000000$BmNpFR5pNLUKonQg$3c94aedf8f4b5290e1cf1ec356374377ce6bf505ab2555d9a1958077b588b087', (SELECT id FROM roles WHERE name = 'Senior management'));

INSERT INTO currencies (code, name, symbol) VALUES
    ('GBP', 'British pound', '£'),
    ('USD', 'US dollar',     '$');

INSERT INTO regions (code, name, currency_code) VALUES
    ('uk', 'United Kingdom', 'GBP'),
    ('us', 'United States',  'USD');

-- ZAR per 1 unit of foreign currency.  The newest row on or before today is used.
INSERT INTO exchange_rates (currency_code, zar_per_unit, effective_date) VALUES
    ('GBP', 23.1000, '2025-10-01'),
    ('GBP', 23.3000, '2025-12-01'),
    ('GBP', 23.5000, '2026-01-01'),
    ('USD', 17.6000, '2025-10-01'),
    ('USD', 17.8000, '2025-12-01'),
    ('USD', 18.0000, '2026-01-01');

INSERT INTO inventory_assumptions (name, label, unit, value) VALUES
    ('unit_cost_zar',       'Product cost per unit (values inventory)', 'ZAR',    144.0000),
    ('holding_rate_annual', 'Annual holding rate (fraction of value)',  '0-1',     0.2000),
    ('lead_time_months',    'Replenishment lead time (L)',              'months',  1.5000),
    ('demand_cv',           'Demand variability (CV)',                  'ratio',   0.3000),
    ('service_z',           'Service level factor (z)',                 'z-score', 1.6500);

INSERT INTO region_cost_inputs
    (region_id, monthly_volume, direct_freight_zar, setup_zar, local_delivery_zar,
     rent_zar_month, inbound_shipment_fixed_zar, baseline_freight_zar, updated_by)
VALUES
    ((SELECT id FROM regions WHERE code = 'uk'),  780, 252.0000, 270000.0000, 81.0000, 45000.0000, 10800.0000, 252.0000, NULL),
    ((SELECT id FROM regions WHERE code = 'us'), 1050, 297.0000, 270000.0000, 93.6000, 57600.0000, 10800.0000, 297.0000, NULL);

-- Sample what-if runs (numbers calculated with models/breakeven.py)
INSERT INTO scenarios
    (region_id, user_id, monthly_volume, freight_pct, customs_pct, lease_pct, growth_pct,
     break_even_volume, monthly_saving_zar, recommendation)
VALUES
    ((SELECT id FROM regions WHERE code = 'uk'), (SELECT id FROM users WHERE email = 'jiara.martins@fintr.com'),
      780,   0, 0,  0,  0, 1137.08, -36797.77, 'Ship from SA'),
    ((SELECT id FROM regions WHERE code = 'uk'), (SELECT id FROM users WHERE email = 'jiara.martins@fintr.com'),
      780,  10, 0,  0,  0,  951.28, -21072.97, 'Ship from SA'),
    ((SELECT id FROM regions WHERE code = 'uk'), (SELECT id FROM users WHERE email = 'thabo.nkosi@fintr.com'),
      780,   0, 0,  0, 10, 1137.08, -28782.02, 'Ship from SA'),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM users WHERE email = 'thabo.nkosi@fintr.com'),
     1050,   0, 0,  0,  0,  958.34,  12424.45, 'Review: inside margin'),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM users WHERE email = 'lerato.dlamini@fintr.com'),
     1050,   0, 0, 10,  0, 1000.85,   6664.45, 'Review: inside margin'),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM users WHERE email = 'lerato.dlamini@fintr.com'),
     1050, -10, 0,  0,  0, 1171.26, -13468.55, 'Ship from SA');

INSERT INTO reports (region_id, user_id, sections, file_name) VALUES
    ((SELECT id FROM regions WHERE code = 'uk'), (SELECT id FROM users WHERE email = 'jiara.martins@fintr.com'),
     'be,cost,savings,rec', 'Fintr_report_UK_2026-01-15.pdf'),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM users WHERE email = 'lerato.dlamini@fintr.com'),
     'be,savings,rec',      'Fintr_report_US_2026-01-20.pdf');

-- =====================================================================
-- FINTR'S ACTUAL DATA (data pack as at 30 October 2026, from Fintr's
-- accounting system and courier invoices).  All amounts in ZAR.
-- Shipments and invoices use fixed ids so the rows can point to each other.
-- =====================================================================

-- The data pack gives no SKU or product name, so these two are our labels.
INSERT INTO products (sku, name, ex_factory_zar, landed_cost_zar, retail_price_zar) VALUES
    ('FINTR-01', 'Fintr product (single SKU)', 100.00, 125.00, 899.00);

INSERT INTO locations (name, location_type, country_code, region_id) VALUES
    ('Fintr dispatch, South Africa',  'Dispatch',    'ZA', NULL),
    ('US stock point, Indianapolis',  'Stock point', 'US', (SELECT id FROM regions WHERE code = 'us')),
    ('UK customers',                  'Customer',    'GB', (SELECT id FROM regions WHERE code = 'uk')),
    ('US customers',                  'Customer',    'US', (SELECT id FROM regions WHERE code = 'us'));

-- Transit times from the data pack (section 3)
INSERT INTO routes (code, origin_location_id, destination_location_id, mode, transit_days_min, transit_days_max) VALUES
    ('SA-UK-DIRECT',
     (SELECT id FROM locations WHERE name = 'Fintr dispatch, South Africa'),
     (SELECT id FROM locations WHERE name = 'UK customers'),
     'International courier, door to door', 7, 14),
    ('SA-US-DIRECT',
     (SELECT id FROM locations WHERE name = 'Fintr dispatch, South Africa'),
     (SELECT id FROM locations WHERE name = 'US customers'),
     'International courier, door to door', 7, 14),
    ('SA-US-STOCK',
     (SELECT id FROM locations WHERE name = 'Fintr dispatch, South Africa'),
     (SELECT id FROM locations WHERE name = 'US stock point, Indianapolis'),
     'International courier, bulk', 28, 28),
    ('US-STOCK-CUST',
     (SELECT id FROM locations WHERE name = 'US stock point, Indianapolis'),
     (SELECT id FROM locations WHERE name = 'US customers'),
     'Domestic ground', 2, 5);

INSERT INTO shipments (id, route_id, carrier, cartons, ship_date, date_precision, had_exception, exception_note) VALUES
    (1, (SELECT id FROM routes WHERE code = 'SA-UK-DIRECT'), NULL,  1, '2026-06-01', 'month', 0, NULL),
    (2, (SELECT id FROM routes WHERE code = 'SA-US-DIRECT'), NULL,  1, '2026-06-01', 'month', 0, NULL),
    (3, (SELECT id FROM routes WHERE code = 'SA-US-DIRECT'), NULL,  1, '2026-07-01', 'month', 0, NULL),
    (4, (SELECT id FROM routes WHERE code = 'SA-US-STOCK'),  'UPS', 3, '2026-08-19', 'day',   1,
     'Customs hold 2-4 Sep (missing US importer forms), delivery exception 16 Sep, delivered mid to late Sep');

INSERT INTO invoices (id, shipment_id, billed_via, invoice_date, date_precision, note) VALUES
    (1, 1,    'TUNL', '2026-06-01', 'month', 'Courier, protection, customs clearance (not itemised)'),
    (2, 2,    'TUNL', '2026-06-01', 'month', 'Courier, protection, customs (not itemised) less a credit'),
    (3, 3,    'TUNL', '2026-07-01', 'month', 'Courier, protection, signature fee, duties and clearance (not itemised)'),
    (4, 4,    'TUNL', '2026-08-19', 'day',   '24 units in 3 cartons of 8'),
    (5, NULL, 'TUNL', '2026-06-01', 'month', 'FedEx surcharge, shipment not stated in the data pack');

INSERT INTO charge_types (code, name) VALUES
    ('courier',    'Courier freight'),
    ('protection', 'Shipment protection (insurance)'),
    ('signature',  'Signature fee'),
    ('duties',     'Destination customs duties'),
    ('clearance',  'Customs clearance fees'),
    ('combined',   'Combined charges (not itemised on the invoice)'),
    ('credit',     'Credit'),
    ('surcharge',  'Carrier surcharge');

INSERT INTO invoice_lines (invoice_id, charge_type_id, amount_zar, note) VALUES
    (1, (SELECT id FROM charge_types WHERE code = 'combined'),    882.18, NULL),
    (2, (SELECT id FROM charge_types WHERE code = 'combined'),   1217.83, NULL),
    (2, (SELECT id FROM charge_types WHERE code = 'credit'),     -146.65, NULL),
    (3, (SELECT id FROM charge_types WHERE code = 'combined'),   1196.86, NULL),
    (4, (SELECT id FROM charge_types WHERE code = 'courier'),    5999.79, 'UPS'),
    (4, (SELECT id FROM charge_types WHERE code = 'protection'),  378.00, NULL),
    (4, (SELECT id FROM charge_types WHERE code = 'signature'),    50.00, NULL),
    (4, (SELECT id FROM charge_types WHERE code = 'duties'),     2400.00, 'US customs duties'),
    (4, (SELECT id FROM charge_types WHERE code = 'clearance'),   657.99, NULL),
    (5, (SELECT id FROM charge_types WHERE code = 'surcharge'),   149.36, 'FedEx dimensional weight remeasurement');

-- Every month not listed had 0 international orders.
INSERT INTO orders (region_id, product_id, route_id, shipment_id, order_month, order_type, units) VALUES
    ((SELECT id FROM regions WHERE code = 'uk'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'SA-UK-DIRECT'),  1,    '2026-06-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'SA-US-DIRECT'),  2,    '2026-06-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'SA-US-DIRECT'),  3,    '2026-07-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'SA-US-STOCK'),   4,    '2026-08-01', 'Restock',  24),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'US-STOCK-CUST'), NULL, '2026-10-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'US-STOCK-CUST'), NULL, '2026-10-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'US-STOCK-CUST'), NULL, '2026-10-01', 'Consumer', 1),
    ((SELECT id FROM regions WHERE code = 'us'), (SELECT id FROM products WHERE sku = 'FINTR-01'),
     (SELECT id FROM routes WHERE code = 'US-STOCK-CUST'), NULL, '2026-10-01', 'Consumer', 4);

-- Fintr's SA packing estimates and working warehouse threshold (data pack sections 4 and 6).
-- The app only reads the assumption names it knows, so these extra rows do not change the model.
INSERT INTO inventory_assumptions (name, label, unit, value) VALUES
    ('sa_packaging_zar_unit',       'SA packaging per unit (Fintr estimate)',            'ZAR',        15.0000),
    ('sa_labour_zar_order',         'SA packing labour per order (Fintr estimate)',      'ZAR',        50.0000),
    ('warehouse_threshold_low',     'Warehouse starts to make sense from (Fintr view)',  'orders/mo',  15.0000),
    ('warehouse_threshold_high',    'Warehouse clearly makes sense by (Fintr view)',     'orders/mo',  40.0000);
