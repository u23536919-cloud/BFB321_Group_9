# Title: Fintr break-even tool (BFB 321, Group 9): 
Project Description: This web application helps Fintr decide when it becomes cheaper to hold stock in a regional warehouse (UK or US) than to ship directly from South Africa. For each region it calculates the break-even volume, compares direct shipping and warehouse costs, shows potential monthly savings, and gives a recommendation. Users can change the inputs, run what-if scenarios (freigh rate, volume growth and warehouse lease changes), view alerts, and export a PDF report.

Model cost calculations, dashboard charts and reports use ZAR as the common currency

Group Members:
Farah Hassim u23536919 
Katarina Stamenic u23635119 
Keratile Maboka u24648702 

Setup intsructions:

1. Clone the repository: 

git clone <paste-your-repository-URL-here>
cd <repository-folder-name>

2. Intallation and setup instructions
Type in the terminal and press enter after each one: 
'python -m venv venv'
venv\Scripts\activate
`pip install -r requirements.txt`
`python -m pytest`
`python app.py`
Open http://127.0.0.1:5000

3. Click into "Semester-project-main" inside the repository to view all the files


### REST API (Step 4)

The API is a Flask Blueprint in `api/`, mounted at `/api`. Every request and response is JSON. The full contract (request and response schemas for every endpoint) is in [`docs/openapi.json`](docs/openapi.json): paste it into <https://editor.swagger.io> to browse it.

| File | What it does |
| --- | --- |
| `api/__init__.py` | Creates the blueprint and attaches it to the app |
| `api/routes.py` | The endpoints |
| `api/auth.py` | JWT login tokens and the role checks |
| `api/helpers.py` | Pagination, filters, validation, JSON conversion |
| `api/errors.py` | One JSON error format and the error handlers |

### Endpoints

| Method | Endpoint | Who | What it does |
| --- | --- | --- | --- |
| GET | `/api/health` | anyone | API and database status |
| POST | `/api/auth/login` | anyone | Email + password → JWT token |
| GET | `/api/auth/me` | logged in | The current user and role |
| GET | `/api/regions` | logged in | Both regions: inputs and break-even results |
| GET | `/api/regions/{code}` | logged in | One region (`uk` or `us`) |
| PUT | `/api/regions/{code}/inputs` | Finance, Supply chain planner | Change cost inputs and/or monthly volume |
| GET | `/api/scenarios` | logged in | Saved what-if runs. Filters: `region`, `recommendation` |
| GET | `/api/scenarios/{id}` | logged in | One saved what-if run |
| POST | `/api/scenarios` | Finance, Supply chain planner | Run a what-if (freight/customs/lease/growth %) and save it |
| GET | `/api/products` | logged in | All products |
| GET | `/api/products/{id}` | logged in | One product |
| POST | `/api/products` | Finance, Supply chain planner | Create a product (duplicate SKU → `409`) |
| PUT | `/api/products/{id}` | Finance, Supply chain planner | Change a product (send only what changes) |
| GET | `/api/orders` | logged in | Fintr's actual orders. Filters: `region`, `order_type`, `from`, `to` (YYYY-MM) |
| GET | `/api/orders/{id}` | logged in | One order |
| POST | `/api/orders` | Finance, Supply chain planner | Record a new order |
| PUT | `/api/orders/{id}` | Finance, Supply chain planner | Change an order (send only what changes) |
| GET | `/api/shipments` | logged in | Shipments with invoice totals. Filters: `region`, `route`, `exception` |
| GET | `/api/shipments/{id}` | logged in | One shipment with its invoice cost breakdown |
| GET | `/api/reports` | logged in | Log of generated PDF reports. Filter: `region` |

**Create, read, update:** products and orders can be created (POST), read one and many (GET) and updated (PUT). Region inputs are read and updated (the two regions are fixed, so they are not created through the API). Scenarios are created and read but not edited, because they are a history of what-if runs.

**Related data stays consistent:** an order's route must deliver to the order's region, its SKU must exist and its shipment must exist (otherwise `400`). A new or changed order immediately updates the region's `actual_demand` (average consumer orders per month) in `GET /api/regions/{code}`, which is what the break-even question is about.

**Pagination:** list endpoints take `?page=1&per_page=20` (max 100) and return `{"data": [...], "pagination": {"page", "per_page", "total", "pages"}}`.

**Roles:** Finance and Supply chain planner can create and change products, orders and region inputs, and run scenarios. Senior management can read everything but gets `403 forbidden` on changes.

**Errors** always look like this (also for 404/405 on unknown `/api` URLs):

```json
{"error": {"status": 400, "code": "validation_failed", "message": "Some fields are not valid.",
           "details": {"monthly_volume": "Must be 0 or more."}}}
```

| Status | Code | When |
| --- | --- | --- |
| 400 | `validation_failed`, `invalid_input`, `bad_request`, `bad_query`, `inconsistent`, `bad_reference` | A field is missing, the wrong type or out of range; the body is not JSON; a bad filter or page value; or the record does not match related records |
| 401 | `unauthorized`, `invalid_token`, `token_expired`, `invalid_credentials` | Not logged in or wrong password |
| 403 | `forbidden` | Your role cannot do this |
| 404 | `not_found` | Unknown id, region or URL |
| 405 | `method_not_allowed` | Wrong HTTP method |
| 409 | `duplicate` | The value must be unique (for example a SKU that already exists) |
| 503 | `database_unavailable`, `database_off` | MySQL is down or `USE_DATABASE=false` |
| 500 | `internal_error` | Unexpected error (full details go to the log, not the client) |

**Logging:** every API call is logged as one line, e.g. `API GET /api/orders?region=us -> 200 (4 ms, user jiara.martins@fintr.com)`. Failed database writes are rolled back and logged (`db.py`), and unexpected errors are logged with the full traceback but the client only sees a short JSON message.


### Try it (with `python app.py` running)

```bash
# 1. log in and copy the "token" from the reply
curl -X POST http://127.0.0.1:5000/api/auth/login -H "Content-Type: application/json" -d "{\"email\":\"jiara.martins@fintr.com\",\"password\":\"demo123\"}"

# 2. use it
curl http://127.0.0.1:5000/api/regions/uk -H "Authorization: Bearer PASTE_TOKEN_HERE"
curl "http://127.0.0.1:5000/api/orders?region=us&per_page=3" -H "Authorization: Bearer PASTE_TOKEN_HERE"
curl -X POST http://127.0.0.1:5000/api/scenarios -H "Authorization: Bearer PASTE_TOKEN_HERE" -H "Content-Type: application/json" -d "{\"region\":\"us\",\"freight_pct\":10}"
```

## Test Sign-in

**Website:** the login page is still a placeholder. It finds or creates the user from the email address, so any email and a password of at least 6 characters will work.

**API:** `POST /api/auth/login` checks the real password (stored as a salted hash in `users.password_hash`). The three seeded users below all have the password `demo123`.

Seeded demo users:

| Email | Role |
| --- | --- |
| jiara.martins@fintr.com | Finance |
| thabo.nkosi@fintr.com | Supply chain planner |
| lerato.dlamini@fintr.com | Senior management |

Use any password of 6 or more characters, for example `demo123`.

## Project Structure

- `app.py`: Flask pages (uses the model and the database)
- `config.py`: development, testing and production settings
- `db.py`: MySQL connection and the `init` command
- `repository.py`: every SQL query (parameterised)
- `models/breakeven.py`: standalone break-even model (formulae, units and assumptions are in the module docstring)
- `database/schema.sql`, `database/seed.sql`: tables and demo data
- `docs/`: ERD image, the script that draws it from `schema.sql`, and `openapi.json` (the API contract)
- `templates/` and `static/`: Bootstrap frontend
- `api/`: the REST API blueprint (Step 4)
- `report_pdf.py`: builds the PDF report
- `tests/`: unit tests (model, settings and API) and database tests (`test_database.py`, the second half of `test_api.py`)
- `.env.example`: template for your private `.env` file

## Notes

Step 4 added the REST API (`api/`). 
