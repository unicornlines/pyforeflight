import logging
from typing import Optional

import requests

from .exceptions import APIException

JSON_CONTENT = "application/json"


class DispatchClient(object):
    """
    ForeFlight Dispatch public API client.

    Wraps the documented Dispatch REST API at ``public-api.foreflight.com``.
    Unlike the web :class:`~pyforeflight.client.Client` (which uses a username /
    password session), Dispatch authenticates with an API key sent in the
    ``x-api-key`` header. Manage keys in the Dispatch API Keys console.

    All endpoints accept an optional ``x-vendorId`` header; set ``vendor_id``
    on the client if your key operates on behalf of a vendor.

    Example::

        from pyforeflight import DispatchClient

        d = DispatchClient(api_key="...")
        for flight in d.get_flights(from_date="2026-01-01", to_date="2026-02-01"):
            print(flight["flightId"])
        navlog_pdf = d.get_navlog(flight_id, format="pdf")  # returns bytes

    Methods returning JSON give parsed objects; report/file endpoints
    (briefing, navlog, W&B, file download, ...) return raw ``bytes``.
    """

    base_url = "https://public-api.foreflight.com"

    def __init__(
        self,
        api_key: str,
        vendor_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key
        self.vendor_id = vendor_id
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Accept": JSON_CONTENT})

    # ------------------------------------------------------------------ #
    # Low-level plumbing
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict:
        return {"x-vendorId": self.vendor_id} if self.vendor_id else {}

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = self.base_url + path
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        logging.debug("%s %s params=%s", method, url, kwargs.get("params"))
        r = self.session.request(method, url, headers=headers, **kwargs)
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise APIException(
                f"{method} {url} failed ({r.status_code}): {r.text}"
            ) from e
        return r

    @staticmethod
    def _payload(r: requests.Response):
        """Return parsed JSON when the response is JSON, otherwise raw bytes."""
        if JSON_CONTENT in r.headers.get("Content-Type", ""):
            return r.json()
        return r.content

    def _get(self, path: str, params: Optional[dict] = None):
        return self._payload(self._request("GET", path, params=self._clean(params)))

    def _post(self, path: str, json_body=None, params: Optional[dict] = None):
        return self._payload(
            self._request("POST", path, json=json_body, params=self._clean(params))
        )

    def _put(self, path: str, json_body=None, params: Optional[dict] = None):
        return self._payload(
            self._request("PUT", path, json=json_body, params=self._clean(params))
        )

    def _delete(self, path: str, params: Optional[dict] = None):
        return self._payload(self._request("DELETE", path, params=self._clean(params)))

    @staticmethod
    def _clean(params: Optional[dict]) -> Optional[dict]:
        """Drop query params whose value is ``None``."""
        if not params:
            return None
        return {k: v for k, v in params.items() if v is not None}

    # ------------------------------------------------------------------ #
    # API key info
    # ------------------------------------------------------------------ #

    def get_api_keys(self) -> list:
        """Return information about the API keys on the account."""
        return self._get("/public/api/apiKeyInfo")

    def get_webhook_sample_data(self):
        """Return sample webhook payload data."""
        return self._get("/public/api/apiKeyInfo/WebHook")

    def update_webhook_subscription(self, url: str, secret: Optional[str] = None):
        """Set the webhook callback URL (and optional signing secret)."""
        return self._put(
            "/public/api/apiKeyInfo/WebHook", params={"url": url, "secret": secret}
        )

    # ------------------------------------------------------------------ #
    # Aircraft & crew
    # ------------------------------------------------------------------ #

    def get_aircraft(self) -> list:
        """Return the account's aircraft."""
        return self._get("/public/api/aircraft")

    def get_crew(self) -> list:
        """Return the account's crew members."""
        return self._get("/public/api/crew")

    # ------------------------------------------------------------------ #
    # Contacts
    # ------------------------------------------------------------------ #

    def get_contacts(self, contact_id: Optional[str] = None, role: Optional[str] = None):
        """Return contacts, optionally filtered by id or role."""
        return self._get(
            "/public/api/contacts", params={"id": contact_id, "role": role}
        )

    def create_contact(self, contact: dict):
        """Create a contact."""
        return self._post("/public/api/contacts", json_body=contact)

    def update_contact(self, contact_id: str, contact: dict):
        """Update a contact by id."""
        return self._put(f"/public/api/contacts/{contact_id}", json_body=contact)

    def delete_contact(self, contact_id: str):
        """Delete a contact by id."""
        return self._delete(f"/public/api/contacts/{contact_id}")

    # ------------------------------------------------------------------ #
    # Airports
    # ------------------------------------------------------------------ #

    def get_airports(self) -> list:
        """Return the account's airports."""
        return self._get("/public/api/airport/Airports")

    def create_airport(self, airport: dict):
        """Create an airport."""
        return self._post("/public/api/airport", json_body=airport)

    def update_airport(self, object_id: str, airport: dict):
        """Update an airport by its objectId."""
        return self._put(f"/public/api/airport/{object_id}", json_body=airport)

    def delete_airport(self, airport_id: str):
        """Delete an airport by id."""
        return self._delete(f"/public/api/airport/{airport_id}")

    # ------------------------------------------------------------------ #
    # Custom airports
    # ------------------------------------------------------------------ #

    def get_custom_airports(self) -> list:
        """Return the account's custom airports."""
        return self._get("/public/api/customairport/Airports")

    def create_custom_airport(self, airport: dict):
        """Create a custom airport."""
        return self._post("/public/api/customairport", json_body=airport)

    def update_custom_airport(self, object_id: str, airport: dict):
        """Update a custom airport by its objectId."""
        return self._put(f"/public/api/customairport/{object_id}", json_body=airport)

    def delete_custom_airport(self, airport_id: str):
        """Delete a custom airport by id."""
        return self._delete(f"/public/api/customairport/{airport_id}")

    # ------------------------------------------------------------------ #
    # Custom airways
    # ------------------------------------------------------------------ #

    def get_custom_airways(self) -> list:
        """Return the account's custom airways."""
        return self._get("/public/api/userairways")

    def create_custom_airway(self, airway: dict):
        """Create a custom airway."""
        return self._post("/public/api/userairways", json_body=airway)

    def update_custom_airway(self, object_id: str, airway: dict):
        """Update a custom airway by its objectId."""
        return self._post(f"/public/api/userairways/{object_id}", json_body=airway)

    def delete_custom_airway(self, object_id: str):
        """Delete a custom airway by its objectId."""
        return self._delete(f"/public/api/userairways/{object_id}")

    # ------------------------------------------------------------------ #
    # Custom waypoints
    # ------------------------------------------------------------------ #

    def get_custom_waypoints(self) -> list:
        """Return the account's custom waypoints."""
        return self._get("/public/api/userwaypoints")

    def create_custom_waypoint(self, waypoint: dict):
        """Create a custom waypoint."""
        return self._post("/public/api/userwaypoints", json_body=waypoint)

    def update_custom_waypoint(self, object_id: str, waypoint: dict):
        """Update a custom waypoint by its objectId."""
        return self._post(f"/public/api/userwaypoints/{object_id}", json_body=waypoint)

    def delete_custom_waypoint(self, object_id: str):
        """Delete a custom waypoint by its objectId."""
        return self._delete(f"/public/api/userwaypoints/{object_id}")

    # ------------------------------------------------------------------ #
    # Flights
    # ------------------------------------------------------------------ #

    def get_flights(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        tags: Optional[str] = None,
        search: Optional[str] = None,
    ) -> list:
        """Return flights, optionally filtered by date range, tags, or search."""
        params = {
            "fromDate": from_date,
            "toDate": to_date,
            "tags": tags,
            "search": search,
        }
        return self._get("/public/api/Flights/flights", params=params)

    def get_modified_flights(self, since_date: Optional[str] = None) -> list:
        """Return flights modified since ``since_date``."""
        return self._get(
            "/public/api/Flights/modified", params={"sinceDate": since_date}
        )

    def get_flight(self, flight_id: str) -> dict:
        """Return a single flight by id."""
        return self._get(f"/public/api/Flights/{flight_id}")

    def create_flight(self, flight: dict):
        """Create a flight."""
        return self._post("/public/api/Flights", json_body=flight)

    def update_flight(self, flight_id: str, flight: dict, force_update: Optional[bool] = None):
        """Update a flight by id."""
        return self._post(
            f"/public/api/Flights/{flight_id}",
            json_body=flight,
            params={"forceUpdate": force_update},
        )

    def delete_flight(self, flight_id: str):
        """Delete a flight by id."""
        return self._delete(f"/public/api/Flights/{flight_id}")

    def release_flight(self, release: dict):
        """Release (dispatch) a flight."""
        return self._post("/public/api/Flights/release", json_body=release)

    def update_oooi(self, flight_id: str, oooi: dict):
        """Update OOOI (out/off/on/in) times for a flight."""
        return self._post(f"/public/api/Flights/oooi/{flight_id}", json_body=oooi)

    def get_performance(self, flight_id: str):
        """Return computed performance for a flight."""
        return self._get(f"/public/api/Flights/{flight_id}/performance")

    def calculate_performance(self, request: dict):
        """Calculate performance for an ad-hoc request (no stored flight)."""
        return self._post("/public/api/Flights/performance", json_body=request)

    def get_overflight_information(self, flight_id: str):
        """Return overflight permit information for a flight."""
        return self._get(f"/public/api/Flights/{flight_id}/overflight")

    def get_overflight_report(self, flight_id: str):
        """Return the overflight report for a flight."""
        return self._get(f"/public/api/Flights/{flight_id}/overflightreport")

    def get_wb_report(self, flight_id: str):
        """Return the weight & balance report for a flight (bytes)."""
        return self._get(f"/public/api/Flights/{flight_id}/wb")

    def get_runway_analysis(self, flight_id: str):
        """Return the runway analysis for a flight."""
        return self._get(f"/public/api/Flights/{flight_id}/rwa")

    def get_briefing(self, flight_id: str):
        """Return the briefing package for a flight (bytes)."""
        return self._get(f"/public/api/Flights/{flight_id}/briefing")

    def get_navlog(self, flight_id: str, format: Optional[str] = None):
        """Return the navlog for a flight. ``format`` may select e.g. ``pdf``."""
        return self._get(
            f"/public/api/Flights/{flight_id}/navlog", params={"format": format}
        )

    def get_icao(self, flight_id: str):
        """Return the ICAO flight plan for a flight."""
        return self._get(f"/public/api/Flights/{flight_id}/icao")

    # ------------------------------------------------------------------ #
    # Files in flight
    # ------------------------------------------------------------------ #

    def get_files_in_flight(self, flight_id: str) -> list:
        """List the files attached to a flight."""
        return self._get("/public/api/flights/files", params={"flightId": flight_id})

    def upload_file_in_flight(
        self,
        flight_id: str,
        file,
        display_name: Optional[str] = None,
        category: Optional[str] = None,
    ):
        """Upload a file to a flight (multipart)."""
        params = {
            "flightId": flight_id,
            "displayName": display_name,
            "category": category,
        }
        return self._payload(
            self._request(
                "POST",
                "/public/api/flights/files",
                params=self._clean(params),
                files={"file": file},
            )
        )

    def get_file_in_flight(self, flight_id: str, file_id: str):
        """Download a file attached to a flight (bytes)."""
        return self._get(f"/public/api/flights/files/{flight_id}/{file_id}")

    def update_file_in_flight(
        self,
        flight_id: str,
        file_id: str,
        name: str,
        category: Optional[str] = None,
    ):
        """Update metadata of a file attached to a flight."""
        params = {"name": name, "category": category}
        return self._put(
            f"/public/api/flights/files/{flight_id}/{file_id}", params=params
        )

    def delete_file_in_flight(self, flight_id: str, file_id: str):
        """Delete a file attached to a flight."""
        return self._delete(f"/public/api/flights/files/{flight_id}/{file_id}")

    # ------------------------------------------------------------------ #
    # Quotation
    # ------------------------------------------------------------------ #

    def generate_quote(self, request: dict):
        """Generate a trip quote."""
        return self._post("/public/api/quotation/quote", json_body=request)

    def compare_routes(self, request: dict):
        """Compare candidate routes."""
        return self._post("/public/api/quotation/compareroutes", json_body=request)

    # ------------------------------------------------------------------ #
    # Saved routes
    # ------------------------------------------------------------------ #

    def get_favorite_routes(self) -> list:
        """Return saved/favorite routes."""
        return self._get("/public/api/savedroutes")

    def create_saved_route(self, route: dict):
        """Create a saved route."""
        return self._post("/public/api/savedroutes", json_body=route)

    def delete_favorite_route(self, saved_route_id: str):
        """Delete a saved route by id."""
        return self._delete(f"/public/api/savedroutes/{saved_route_id}")

    # ------------------------------------------------------------------ #
    # Schedule
    # ------------------------------------------------------------------ #

    def upload_scheduled_flights(self, schedule: dict):
        """Upload a batch of scheduled flights."""
        return self._post("/public/api/scheduleflights/upload", json_body=schedule)

    def update_scheduled_flights(self, schedule: dict):
        """Update scheduled flights."""
        return self._post("/public/api/schedule/flights", json_body=schedule)
