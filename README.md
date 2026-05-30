# pyforeflight

Python client for ForeFlight's APIs:

- **Web client** (`pyforeflight.Client`) — username/password session against
  `plan.foreflight.com`, covering the logbook (entries, aircraft, persons,
  custom fields, reports, import/export, CFI signatures) and tracklogs.
- **Dispatch client** (`pyforeflight.DispatchClient`) — API-key access to the
  documented Dispatch REST API at `public-api.foreflight.com` (flights,
  performance, briefings, files, quotation, schedules, and more).

## Installation

```bash
pip install git+https://github.com/unicornlines/pyforeflight.git
```

Or for local development:

```bash
git clone https://github.com/unicornlines/pyforeflight.git
cd pyforeflight
pip install -e .
```

Requires Python 3.9+ and `requests`.

## Web client

```python
from pyforeflight import Client

ff = Client()
ff.login("pilot@example.com", "password")

# Logbook entries (auto-paginated)
for entry in ff.get_all_entries():
    print(entry["objectId"])

# Aircraft, persons, reference data
aircraft = ff.get_aircraft()
roles = ff.get_roles()
airport = ff.get_airport("KAUS")

# Tracklogs
for tracklog in ff.get_all_tracklogs_available():
    gpx = ff.get_tracklog(tracklog["trackUuid"], tracklog_format="gpx")

ff.logout()
```

SSO accounts are not supported (login raises `AuthenticationException`).

### Smoke test

A read-only check that exercises the main endpoints:

```bash
FF_USERNAME=... FF_PASSWORD=... python -m pyforeflight.smoke_test
```

## Dispatch client

```python
from pyforeflight import DispatchClient

d = DispatchClient(api_key="...")          # optional: vendor_id="..."

flights = d.get_flights(from_date="2026-01-01", to_date="2026-02-01")
flight = d.get_flight(flight_id)
navlog_pdf = d.get_navlog(flight_id, format="pdf")   # returns bytes
```

JSON endpoints return parsed objects; report/file endpoints (briefing,
navlog, weight & balance, file downloads) return raw `bytes`. Manage API keys
in the Dispatch API Keys console.

## Disclaimer

The web client targets undocumented endpoints used by ForeFlight Web and is
not affiliated with or endorsed by ForeFlight. Use it with your own account
and at your own risk.

## License

[MIT](LICENSE)
