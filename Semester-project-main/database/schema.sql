-- =====================================================================
-- Fintr break-even tool (BFB 321, Group 9)  -  MySQL schema
-- Works on MySQL 8 and MariaDB 10.5+.  Run it on an EMPTY database
-- (python db.py init does this for you).  The DROP lines at the top
-- make the script repeatable: running it again resets the tables.
-- All money columns are DECIMAL (never FLOAT).  Costs are stored in ZAR.
-- Every main table has created_at / updated_at audit columns.
-- =====================================================================

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS invoice_lines;
DROP TABLE IF EXISTS charge_types;
DROP TABLE IF EXISTS invoices;
DROP TABLE IF EXISTS shipments;
DROP TABLE IF EXISTS routes;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS scenarios;
DROP TABLE IF EXISTS region_cost_inputs;
DROP TABLE IF EXISTS inventory_assumptions;
DROP TABLE IF EXISTS exchange_rates;
DROP TABLE IF EXISTS regions;
DROP TABLE IF EXISTS currencies;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS roles;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------
-- roles: lookup table for the three login roles (Finance, etc.)
-- ---------------------------------------------------------------------
CREATE TABLE roles (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name        VARCHAR(40)  NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_roles_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- users: people who log in.  password_hash is used by the API login
-- (POST /api/auth/login).  It is a salted hash, never the password itself.
-- It is empty for users created by the placeholder web login page.
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name           VARCHAR(60)  NOT NULL,
    email          VARCHAR(120) NOT NULL,
    password_hash  VARCHAR(255) NULL,
    role_id        INT UNSIGNED NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_email (email),
    KEY idx_users_role (role_id),
    CONSTRAINT fk_users_role FOREIGN KEY (role_id) REFERENCES roles (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- currencies: GBP and USD (ZAR is the base currency of the model)
-- ---------------------------------------------------------------------
CREATE TABLE currencies (
    code        CHAR(3)      NOT NULL,
    name        VARCHAR(40)  NOT NULL,
    symbol      VARCHAR(5)   NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- regions: the warehouse regions we compare (UK and US)
-- ---------------------------------------------------------------------
CREATE TABLE regions (
    id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    code           VARCHAR(2)   NOT NULL,
    name           VARCHAR(60)  NOT NULL,
    currency_code  CHAR(3)      NOT NULL,
    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_regions_code (code),
    KEY idx_regions_currency (currency_code),
    CONSTRAINT fk_regions_currency FOREIGN KEY (currency_code) REFERENCES currencies (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- exchange_rates: ZAR per 1 unit of foreign currency, one row per date
-- ---------------------------------------------------------------------
CREATE TABLE exchange_rates (
    id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    currency_code  CHAR(3)       NOT NULL,
    zar_per_unit   DECIMAL(10,4) NOT NULL,
    effective_date DATE          NOT NULL,
    created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_rates_currency_date (currency_code, effective_date),
    CONSTRAINT fk_rates_currency FOREIGN KEY (currency_code) REFERENCES currencies (code),
    CONSTRAINT ck_rates_positive CHECK (zar_per_unit > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- inventory_assumptions: settings shared by both regions
-- (unit cost, holding rate, lead time, demand variability, service level)
-- ---------------------------------------------------------------------
CREATE TABLE inventory_assumptions (
    id          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    name        VARCHAR(60)   NOT NULL,
    label       VARCHAR(120)  NOT NULL,
    unit        VARCHAR(20)   NOT NULL,
    value       DECIMAL(14,4) NOT NULL,
    created_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_assumptions_name (name),
    CONSTRAINT ck_assumptions_value CHECK (value >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- region_cost_inputs: the current editable inputs of each region
-- (exactly one row per region).  Costs are in ZAR.
-- baseline_freight_zar is the starting freight rate used by the alerts.
-- Four decimal places because values are converted from GBP/USD.
-- ---------------------------------------------------------------------
CREATE TABLE region_cost_inputs (
    id                          INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    region_id                   INT UNSIGNED  NOT NULL,
    monthly_volume              DECIMAL(12,2) NOT NULL DEFAULT 0,
    direct_freight_zar          DECIMAL(14,4) NOT NULL,
    setup_zar                   DECIMAL(14,4) NOT NULL,
    local_delivery_zar          DECIMAL(14,4) NOT NULL,
    rent_zar_month              DECIMAL(14,4) NOT NULL,
    inbound_shipment_fixed_zar  DECIMAL(14,4) NOT NULL,
    baseline_freight_zar        DECIMAL(14,4) NOT NULL,
    updated_by                  INT UNSIGNED  NULL,
    created_at                  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_inputs_region (region_id),
    KEY idx_inputs_updated_by (updated_by),
    CONSTRAINT fk_inputs_region FOREIGN KEY (region_id) REFERENCES regions (id),
    CONSTRAINT fk_inputs_user FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_inputs_volume CHECK (monthly_volume >= 0),
    CONSTRAINT ck_inputs_costs CHECK (direct_freight_zar >= 0 AND setup_zar >= 0 AND local_delivery_zar >= 0
                                      AND rent_zar_month >= 0 AND inbound_shipment_fixed_zar >= 0
                                      AND baseline_freight_zar >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- scenarios: every what-if run saved from a region page
-- user_id may be empty when someone uses the app without logging in
-- ---------------------------------------------------------------------
CREATE TABLE scenarios (
    id                  INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    region_id           INT UNSIGNED  NOT NULL,
    user_id             INT UNSIGNED  NULL,
    monthly_volume      DECIMAL(12,2) NOT NULL,
    freight_pct         DECIMAL(6,2)  NOT NULL DEFAULT 0,
    customs_pct         DECIMAL(6,2)  NOT NULL DEFAULT 0,
    lease_pct           DECIMAL(6,2)  NOT NULL DEFAULT 0,
    growth_pct          DECIMAL(6,2)  NOT NULL DEFAULT 0,
    break_even_volume   DECIMAL(12,2) NULL,
    monthly_saving_zar  DECIMAL(14,2) NOT NULL,
    recommendation      VARCHAR(40)   NOT NULL,
    created_at          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_scenarios_region_created (region_id, created_at),
    KEY idx_scenarios_user (user_id),
    KEY idx_scenarios_recommendation (recommendation),
    CONSTRAINT fk_scenarios_region FOREIGN KEY (region_id) REFERENCES regions (id),
    CONSTRAINT fk_scenarios_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_scenarios_volume CHECK (monthly_volume >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- reports: a log of every PDF report that was generated
-- ---------------------------------------------------------------------
CREATE TABLE reports (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    region_id   INT UNSIGNED NOT NULL,
    user_id     INT UNSIGNED NULL,
    sections    VARCHAR(120) NOT NULL,
    file_name   VARCHAR(120) NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_reports_region_created (region_id, created_at),
    KEY idx_reports_user (user_id),
    CONSTRAINT fk_reports_region FOREIGN KEY (region_id) REFERENCES regions (id),
    CONSTRAINT fk_reports_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =====================================================================
-- FINTR'S ACTUAL DATA (data pack, 30 October 2026)
-- The tables below store the real orders, courier invoices and routes
-- from Fintr's accounting system and courier invoices.  They are the
-- evidence the model inputs above are based on.
-- =====================================================================

-- ---------------------------------------------------------------------
-- products: what Fintr sells (one product today, valued in ZAR)
-- ---------------------------------------------------------------------
CREATE TABLE products (
    id                INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    sku               VARCHAR(30)   NOT NULL,
    name              VARCHAR(80)   NOT NULL,
    ex_factory_zar    DECIMAL(12,2) NOT NULL,
    landed_cost_zar   DECIMAL(12,2) NOT NULL,
    retail_price_zar  DECIMAL(12,2) NOT NULL,
    created_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_products_sku (sku),
    CONSTRAINT ck_products_money CHECK (ex_factory_zar >= 0 AND landed_cost_zar >= ex_factory_zar
                                        AND retail_price_zar >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- locations: places stock moves between (SA dispatch, the US stock
-- point in Indianapolis, and customers in each market).
-- region_id is empty for South Africa (it is not a target market).
-- ---------------------------------------------------------------------
CREATE TABLE locations (
    id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
    name           VARCHAR(80)  NOT NULL,
    location_type  VARCHAR(20)  NOT NULL,
    country_code   CHAR(2)      NOT NULL,
    region_id      INT UNSIGNED NULL,
    created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_locations_name (name),
    KEY idx_locations_region (region_id),
    CONSTRAINT fk_locations_region FOREIGN KEY (region_id) REFERENCES regions (id),
    CONSTRAINT ck_locations_type CHECK (location_type IN ('Dispatch', 'Stock point', 'Customer'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- routes: an origin -> destination lane with its typical transit time
-- ---------------------------------------------------------------------
CREATE TABLE routes (
    id                       INT UNSIGNED NOT NULL AUTO_INCREMENT,
    code                     VARCHAR(20)  NOT NULL,
    origin_location_id       INT UNSIGNED NOT NULL,
    destination_location_id  INT UNSIGNED NOT NULL,
    mode                     VARCHAR(40)  NOT NULL,
    transit_days_min         SMALLINT UNSIGNED NOT NULL,
    transit_days_max         SMALLINT UNSIGNED NOT NULL,
    created_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_routes_code (code),
    UNIQUE KEY uq_routes_lane (origin_location_id, destination_location_id),
    KEY idx_routes_destination (destination_location_id),
    CONSTRAINT fk_routes_origin FOREIGN KEY (origin_location_id) REFERENCES locations (id),
    CONSTRAINT fk_routes_destination FOREIGN KEY (destination_location_id) REFERENCES locations (id),
    CONSTRAINT ck_routes_lane CHECK (origin_location_id <> destination_location_id),
    CONSTRAINT ck_routes_transit CHECK (transit_days_max >= transit_days_min)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- shipments: one physical international consignment.
-- When only the month is known, ship_date is the 1st and
-- date_precision = 'month'.  carrier is empty when the invoice did not say.
-- ---------------------------------------------------------------------
CREATE TABLE shipments (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    route_id        INT UNSIGNED NOT NULL,
    carrier         VARCHAR(40)  NULL,
    cartons         SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    ship_date       DATE         NOT NULL,
    date_precision  VARCHAR(5)   NOT NULL DEFAULT 'day',
    had_exception   TINYINT(1)   NOT NULL DEFAULT 0,
    exception_note  VARCHAR(200) NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_shipments_route_date (route_id, ship_date),
    KEY idx_shipments_exception (had_exception),
    CONSTRAINT fk_shipments_route FOREIGN KEY (route_id) REFERENCES routes (id),
    CONSTRAINT ck_shipments_precision CHECK (date_precision IN ('day', 'month')),
    CONSTRAINT ck_shipments_cartons CHECK (cartons > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- invoices: a courier invoice (all booked through the TUNL aggregator).
-- Usually one invoice per shipment.  shipment_id is empty for a charge
-- that is not tied to a known shipment (the June FedEx surcharge).
-- The invoice total is NOT stored: it is SUM(invoice_lines.amount_zar),
-- so it can never disagree with its lines (3NF).
-- ---------------------------------------------------------------------
CREATE TABLE invoices (
    id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
    shipment_id     INT UNSIGNED NULL,
    billed_via      VARCHAR(40)  NOT NULL,
    invoice_date    DATE         NOT NULL,
    date_precision  VARCHAR(5)   NOT NULL DEFAULT 'day',
    note            VARCHAR(200) NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_invoices_shipment (shipment_id),
    KEY idx_invoices_date (invoice_date),
    CONSTRAINT fk_invoices_shipment FOREIGN KEY (shipment_id) REFERENCES shipments (id),
    CONSTRAINT ck_invoices_precision CHECK (date_precision IN ('day', 'month'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- charge_types: lookup for invoice line types (courier, duties, ...)
-- ---------------------------------------------------------------------
CREATE TABLE charge_types (
    id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
    code        VARCHAR(20)  NOT NULL,
    name        VARCHAR(60)  NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_charge_types_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- invoice_lines: the cost breakdown of each invoice in ZAR.
-- Credits are negative amounts.  Exports are zero-rated (no VAT).
-- ---------------------------------------------------------------------
CREATE TABLE invoice_lines (
    id              INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    invoice_id      INT UNSIGNED  NOT NULL,
    charge_type_id  INT UNSIGNED  NOT NULL,
    amount_zar      DECIMAL(12,2) NOT NULL,
    note            VARCHAR(200)  NULL,
    created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lines_invoice_type (invoice_id, charge_type_id),
    KEY idx_lines_charge_type (charge_type_id),
    CONSTRAINT fk_lines_invoice FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE,
    CONSTRAINT fk_lines_charge_type FOREIGN KEY (charge_type_id) REFERENCES charge_types (id),
    CONSTRAINT ck_lines_amount CHECK (amount_zar <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------
-- orders: every international order (consumer orders and stock-point
-- restocks).  The data pack gives months, not days, so order_month is
-- the 1st of the month.  route_id says how the order was fulfilled
-- (direct from SA or from the US stock point).  shipment_id links the
-- order to the international consignment that carried it (empty for
-- orders sent domestically from the US stock point).
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    id           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    region_id    INT UNSIGNED NOT NULL,
    product_id   INT UNSIGNED NOT NULL,
    route_id     INT UNSIGNED NOT NULL,
    shipment_id  INT UNSIGNED NULL,
    order_month  DATE         NOT NULL,
    order_type   VARCHAR(10)  NOT NULL,
    units        INT UNSIGNED NOT NULL,
    created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_orders_region_month (region_id, order_month),
    KEY idx_orders_product (product_id),
    KEY idx_orders_route (route_id),
    KEY idx_orders_shipment (shipment_id),
    CONSTRAINT fk_orders_region FOREIGN KEY (region_id) REFERENCES regions (id),
    CONSTRAINT fk_orders_product FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT fk_orders_route FOREIGN KEY (route_id) REFERENCES routes (id),
    CONSTRAINT fk_orders_shipment FOREIGN KEY (shipment_id) REFERENCES shipments (id),
    CONSTRAINT ck_orders_type CHECK (order_type IN ('Consumer', 'Restock')),
    CONSTRAINT ck_orders_units CHECK (units > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
