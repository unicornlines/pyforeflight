import json
import logging
import time
from typing import Literal, Optional

import requests

from .exceptions import APIException, AuthenticationException

TRACKLOG_FORMAT = Literal["gpx", "kml", "kml-filtered", "csv"]

# ForeFlight rejects tracklog page sizes above 100 with HTTP 400.
MAX_TRACKLOG_PAGE_SIZE = 100

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
)


class Client(object):
    """
    Foreflight web API client.

    Wraps the (undocumented) endpoints behind plan.foreflight.com — the same
    HTTP API the ForeFlight Web logbook SPA uses. Handles XSRF token
    negotiation, session-cookie authentication, and request plumbing.

    The logbook backend wraps most responses in an envelope of the form
    ``{"result": <payload>, "status": "ok"}``. :meth:`_unwrap` strips that
    automatically, so callers receive the payload directly; endpoints that do
    not use the envelope (e.g. ``/map/api/procedures``) return their raw JSON.

    Instantiating the client primes a session with the XSRF token; call
    :meth:`login` before any authenticated endpoint.

    Example::

        from pyforeflight import Client

        ff = Client()
        ff.login("pilot@example.com", "password")
        for entry in ff.get_all_entries():
            print(entry["objectId"])
        for tracklog in ff.get_all_tracklogs_available():
            gpx = ff.get_tracklog(tracklog["trackUuid"], tracklog_format="gpx")
        ff.logout()
    """

    ff_baseurl = "https://plan.foreflight.com/"

    # Transient-failure retry policy. ForeFlight intermittently returns 5xx and
    # rate-limits bursts of requests; retrying with exponential backoff keeps
    # long operations (e.g. paging through hundreds of tracklogs) reliable.
    max_retries = 3
    retry_backoff = 1.0
    retry_statuses = frozenset({429, 500, 502, 503, 504})

    def __init__(self):
        self.ff_session = requests.Session()
        self.ff_session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            }
        )
        self._prime_xsrf_token()

    # ------------------------------------------------------------------ #
    # Low-level plumbing
    # ------------------------------------------------------------------ #

    def _prime_xsrf_token(self):
        """
        Fetch the landing page to obtain the ``_xsrf`` cookie and mirror it
        into the ``X-XSRFToken`` header, which Foreflight requires on writes.
        """
        r = self.ff_session.get(self.ff_baseurl)
        r.raise_for_status()
        xsrf_cookie = self.ff_session.cookies.get("_xsrf")
        if xsrf_cookie:
            self.ff_session.headers.update({"X-XSRFToken": xsrf_cookie})

    def _retry_delay(self, attempt: int, response: Optional[requests.Response] = None) -> float:
        """Backoff before the next retry, honouring ``Retry-After`` if present."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    pass
        return self.retry_backoff * (2 ** attempt)

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        Issue an HTTP request against the Foreflight base URL and raise an
        :class:`APIException` on any non-2xx response.

        Transient failures (connection errors and the status codes in
        :attr:`retry_statuses`) are retried up to :attr:`max_retries` times with
        exponential backoff.
        """
        url = self.ff_baseurl + path
        logging.debug("%s %s params=%s", method, url, kwargs.get("params"))
        for attempt in range(self.max_retries + 1):
            try:
                r = self.ff_session.request(method, url, **kwargs)
            except requests.RequestException as e:
                if attempt < self.max_retries:
                    logging.warning("%s %s connection error (%s); retry %d/%d",
                                    method, url, e, attempt + 1, self.max_retries)
                    time.sleep(self._retry_delay(attempt))
                    continue
                raise APIException(f"{method} {url} failed: {e}") from e

            if r.status_code in self.retry_statuses and attempt < self.max_retries:
                logging.warning("%s %s -> %d; retry %d/%d", method, url,
                                r.status_code, attempt + 1, self.max_retries)
                time.sleep(self._retry_delay(attempt, r))
                continue

            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                raise APIException(
                    f"{method} {url} failed ({r.status_code}): {r.text}"
                ) from e
            return r

    def _get(self, path: str, params: Optional[dict] = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _post(
        self, path: str, payload: Optional[dict] = None, params: Optional[dict] = None
    ) -> requests.Response:
        data = json.dumps(payload) if payload is not None else None
        return self._request("POST", path, data=data, params=params)

    def _put(self, path: str, payload: Optional[dict] = None) -> requests.Response:
        data = json.dumps(payload) if payload is not None else None
        return self._request("PUT", path, data=data)

    def _delete(self, path: str, params: Optional[dict] = None) -> requests.Response:
        return self._request("DELETE", path, params=params)

    @staticmethod
    def _unwrap(r: requests.Response):
        """
        Return the payload of a response. If the body is the standard logbook
        envelope, return its ``result``; otherwise return the parsed JSON, or
        the raw text if the body is not JSON.
        """
        try:
            body = r.json()
        except ValueError:
            return r.text
        if isinstance(body, dict) and "result" in body:
            return body["result"]
        return body

    def _result(self, path: str, params: Optional[dict] = None):
        """GET a logbook endpoint and return the unwrapped payload."""
        return self._unwrap(self._get(path, params=params))

    @staticmethod
    def _content(envelope: dict) -> list:
        """Extract the row list from a Spring-style paginated ``result``."""
        return envelope.get("content", [])

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #

    def login(self, username: str, password: str):
        """
        Authenticate with username and password.

        Raises :class:`AuthenticationException` if the account uses SSO (not
        supported) or if the credentials are rejected.
        """
        sso = self._post("sso/api/check/" + username)
        if sso.json().get("isSsoEnabled"):
            raise AuthenticationException(
                f"SSO is enabled for {username}; SSO login is not supported."
            )
        logging.info("SSO not enabled for user. Proceeding...")

        payload = {
            "username": username,
            "password": password,
            "next": None,
            "isFromEmail": None,
        }
        try:
            self._post("auth/api/login", payload=payload)
        except APIException as e:
            raise AuthenticationException(f"Login failed for {username}: {e}") from e
        logging.info("Login successful")

    def logout(self):
        """End the authenticated session."""
        self._get("auth/logout")
        logging.info("Logout successful")

    def forgot_password(self, username: str) -> dict:
        """Trigger a password-reset email for ``username``."""
        return self._unwrap(self._post("auth/api/forgot-password/" + username))

    # ------------------------------------------------------------------ #
    # Logbook configuration
    # ------------------------------------------------------------------ #

    def get_config(self) -> dict:
        """Return the account's logbook configuration."""
        return self._result("logbook/api/config")

    def save_config(self, config: dict) -> dict:
        """Persist the logbook configuration."""
        return self._unwrap(self._post("logbook/api/config", payload=config))

    # ------------------------------------------------------------------ #
    # Aircraft
    # ------------------------------------------------------------------ #

    def get_aircraft(self, include_flight_aircraft: bool = False) -> list:
        """
        Return the account's logbook aircraft.

        :param include_flight_aircraft: Include aircraft derived from flights,
            not just those explicitly added to the logbook.
        """
        params = {"includeFlightAircraft": str(include_flight_aircraft).lower()}
        return self._result("logbook/api/aircraft", params=params)

    def get_aircraft_by_id(
        self, aircraft_id: str, include_flight_aircraft: bool = False
    ) -> dict:
        """Return a single aircraft by its ``objectId``."""
        params = {"includeFlightAircraft": str(include_flight_aircraft).lower()}
        return self._result(f"logbook/api/aircraft/{aircraft_id}", params=params)

    def get_aircraft_stats(self, aircraft_id: str) -> dict:
        """Return aggregated flight statistics for an aircraft."""
        return self._result(f"logbook/api/aircraft/{aircraft_id}/stats")

    def save_aircraft(self, aircraft: dict) -> dict:
        """Create or update an aircraft."""
        return self._unwrap(self._post("logbook/api/aircraft", payload=aircraft))

    def delete_aircraft(self, aircraft_id: str) -> dict:
        """Delete an aircraft by its ``objectId``."""
        return self._unwrap(self._delete(f"logbook/api/aircraft/{aircraft_id}"))

    # ------------------------------------------------------------------ #
    # Logbook entries (flights)
    # ------------------------------------------------------------------ #

    def get_entries(self, page: int = 0, size: int = 50) -> dict:
        """
        Return one page of logbook entries.

        Returns the raw paginated envelope (``content``, ``totalElements``,
        ``totalPages``, ...). Use :meth:`get_all_entries` to flatten.
        """
        params = {"page": page, "size": size}
        return self._result("logbook/api/entries", params=params)

    def get_paginated_entries(
        self,
        page: int = 0,
        size: int = 50,
        search: str = "",
        start_date: str = "",
        end_date: str = "",
        sorts: str = "",
    ) -> dict:
        """
        Return a page of entries with search, date-range, and sort controls.

        :param start_date: Inclusive lower bound, ``YYYY-MM-DD`` (``startDateString``).
        :param end_date: Inclusive upper bound, ``YYYY-MM-DD`` (``endDateString``).
        :param sorts: Sort spec as used by the web UI (e.g. ``timestampStart,desc``).
        """
        params = {
            "mode": "paginated",
            "page": page,
            "size": size,
            "search": search,
            "startDateString": start_date,
            "endDateString": end_date,
            "sorts": sorts,
        }
        return self._result("logbook/api/entries", params=params)

    def get_paginated_entries_including_selected(
        self,
        selected_entry: str,
        size: int = 50,
        search: str = "",
        drafts: bool = False,
        start_date: str = "",
        end_date: str = "",
    ) -> dict:
        """
        Return the page of entries that contains ``selected_entry``.

        Mirrors the web UI's deep-link behaviour, where a specific entry must
        appear in the returned page regardless of the current sort/filter.
        """
        params = {
            "mode": "includeSelected",
            "size": size,
            "startDateString": start_date,
            "endDateString": end_date,
            "selectedEntry": selected_entry,
            "search": search,
            "drafts": str(drafts).lower(),
        }
        return self._result("logbook/api/entries", params=params)

    def get_all_entries(self, size: int = 50) -> list:
        """Page through and return every logbook entry."""
        all_entries = []
        page = 0
        while True:
            envelope = self.get_entries(page=page, size=size)
            rows = self._content(envelope)
            all_entries.extend(rows)
            if envelope.get("last", True) or not rows:
                break
            page += 1
        return all_entries

    def get_entry(self, entry_id: str) -> dict:
        """Return a single logbook entry by its ``objectId``."""
        return self._result(f"logbook/api/entries/{entry_id}")

    def get_earliest_entry(self) -> dict:
        """Return metadata about the earliest logbook entry."""
        return self._result("logbook/api/earliest")

    def create_entry(self, entry: dict) -> dict:
        """Create a new logbook entry."""
        return self._unwrap(self._post("logbook/api/entries", payload=entry))

    def save_entry(self, entry: dict) -> dict:
        """Update an existing entry. ``entry`` must contain its ``objectId``."""
        entry_id = entry["objectId"]
        return self._unwrap(self._put(f"logbook/api/entries/{entry_id}", payload=entry))

    def delete_entry(self, entry_id: str) -> dict:
        """Delete a logbook entry by its ``objectId``."""
        return self._unwrap(self._delete(f"logbook/api/entries/{entry_id}"))

    # ------------------------------------------------------------------ #
    # Entry images
    # ------------------------------------------------------------------ #

    def get_entry_images(self, entry_id: str) -> list:
        """Return image metadata attached to an entry."""
        return self._result(f"logbook/api/entryImages/{entry_id}")

    def get_entry_image_upload_urls(self, paths: list) -> list:
        """
        Request pre-signed upload URLs for the given local image paths.

        :param paths: List of file-path strings to obtain upload URLs for.
        """
        return self._unwrap(self._post("logbook/api/entries/images/urls", payload=paths))

    @staticmethod
    def upload_entry_image_file(upload_url: str, file_bytes: bytes) -> requests.Response:
        """
        PUT raw image bytes to a pre-signed URL obtained from
        :meth:`get_entry_image_upload_urls`. This targets the storage backend
        directly, not the Foreflight base URL.
        """
        r = requests.put(upload_url, data=file_bytes)
        r.raise_for_status()
        return r

    def create_entry_image_records(self, entry_id: str, images: dict) -> dict:
        """Register uploaded images against an entry once their files are stored."""
        return self._unwrap(
            self._post(f"logbook/api/entries/{entry_id}/images", payload=images)
        )

    def delete_entry_photo(self, image_id: str) -> dict:
        """Delete a single entry image by its id."""
        return self._unwrap(self._delete(f"logbook/api/entries/image/{image_id}"))

    # ------------------------------------------------------------------ #
    # Persons (pilots / crew)
    # ------------------------------------------------------------------ #

    def get_persons(self, page: int = 0, size: int = 50, search: str = "") -> dict:
        """Return one page of persons (crew/pilots) in the logbook."""
        params = {"page": page, "size": size, "search": search}
        return self._result("logbook/api/persons", params=params)

    def get_all_persons(self, size: int = 50) -> list:
        """Page through and return every person."""
        all_persons = []
        page = 0
        while True:
            envelope = self.get_persons(page=page, size=size)
            rows = self._content(envelope)
            all_persons.extend(rows)
            if envelope.get("last", True) or not rows:
                break
            page += 1
        return all_persons

    # ------------------------------------------------------------------ #
    # Custom fields
    # ------------------------------------------------------------------ #

    def get_custom_field_infos(self) -> list:
        """Return the account's custom-field definitions."""
        return self._result("logbook/api/customFieldInfos")

    def get_custom_field_entry_counts(self, custom_field_id: str) -> dict:
        """Return how many entries reference a given custom field."""
        return self._result(f"logbook/api/customFieldInfos/{custom_field_id}/entryCounts")

    def save_custom_field_info(self, custom_field: dict) -> dict:
        """Create or update a custom-field definition."""
        return self._unwrap(
            self._post("logbook/api/customFieldInfos", payload=custom_field)
        )

    def delete_custom_field_info(self, custom_field_id: str) -> dict:
        """Delete a custom-field definition."""
        return self._unwrap(
            self._delete(f"logbook/api/customFieldInfos/{custom_field_id}")
        )

    # ------------------------------------------------------------------ #
    # Reference data
    # ------------------------------------------------------------------ #

    def get_airport(self, ident: str) -> dict:
        """Return airport details for an ICAO/IATA/FAA identifier."""
        return self._result(f"logbook/api/airports/{ident}")

    def get_procedures(self, ident: str) -> list:
        """Return instrument procedures for an airport identifier (map API)."""
        return self._get("map/api/procedures/ident", params={"ident": ident}).json()[
            "procedures"
        ]

    def get_roles(self) -> list:
        """Return the list of selectable crew roles (PIC, Instructor, ...)."""
        return self._result("logbook/api/roles")

    def get_lock_status(self) -> dict:
        """Return the logbook lock status."""
        return self._result("logbook/api/lock/status")

    def get_lb4l_status(self) -> dict:
        """Return the Logbook-for-Life subscription status."""
        return self._result("logbook/api/lb4l/status")

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #

    def get_report_types(self) -> list:
        """Return the available logbook report types."""
        return self._result("logbook/api/reportTypes")

    def run_report(
        self,
        report_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        report_date: Optional[str] = None,
        tz_offset: int = 0,
        report_period_id: Optional[str] = None,
    ) -> dict:
        """
        Generate a logbook report and return its descriptor (including the URL
        to pass to :meth:`download_report`). Dates use locale ``MM/DD/YYYY``.

        :param tz_offset: Timezone offset in hours.
        """
        params = {
            "start_date": start_date,
            "end_date": end_date,
            "report_date": report_date,
            "tzOffset": tz_offset,
            "report_period_id": report_period_id,
        }
        return self._get(f"api/1/logbook/report/{report_id}", params=params).json()

    def download_report(self, report_name: str, url: str) -> bytes:
        """
        Download a generated report's file. ``url`` is the report URL returned
        by :meth:`run_report`; ``report_name`` is used for the filename.
        """
        params = {"reportName": report_name, "url": url}
        return self._get("logbook/api/downloadReport", params=params).content

    # ------------------------------------------------------------------ #
    # Import / export / reset
    # ------------------------------------------------------------------ #

    def export_csv(self) -> dict:
        """Start a CSV export job. Poll :meth:`export_status` with its ``requestId``."""
        return self._result("logbook/api/export/csv")

    def export_zip(self) -> dict:
        """Start a ZIP export job. Poll :meth:`export_status` with its ``requestId``."""
        return self._result("logbook/api/export/zip")

    def export_status(self, request_id: str) -> dict:
        """Return the status of a CSV export job."""
        return self._result(f"logbook/api/export/status/csv/{request_id}")

    def import_logbook(self, file, commit: bool = True) -> dict:
        """
        Import logbook entries from a file (multipart upload).

        :param file: File object or ``(filename, fileobj)`` tuple.
        :param commit: ``True`` to commit the import, ``False`` to validate only.
        """
        files = {"file": file}
        data = {"commit": str(commit).lower()}
        r = self._request(
            "POST",
            "logbook/api/import",
            files=files,
            data=data,
            headers={"Content-Type": None},
        )
        return self._unwrap(r)

    def get_import_rowcount(self, request_id: str = "") -> dict:
        """Return the row count of a pending import."""
        return self._result(f"logbook/api/import/rowcount/{request_id}")

    def reset_logbook(self, run_async: bool = True) -> dict:
        """
        Delete all logbook data for the account. Destructive — poll
        :meth:`reset_status` with the returned ``requestId``.
        """
        return self._unwrap(
            self._post("logbook/api/reset", payload={"async": run_async})
        )

    def reset_status(self, request_id: str) -> dict:
        """Return the status of a logbook reset job."""
        return self._result(f"logbook/api/reset/status/{request_id}")

    # ------------------------------------------------------------------ #
    # Drafts and CFI signatures (co-signing workflow)
    # ------------------------------------------------------------------ #

    def get_drafts_count(self) -> int:
        """Return the number of pending signature drafts."""
        return self._result("logbook/api/drafts/count")

    def approve_draft(self, draft_id: str) -> dict:
        """Approve a pending draft entry."""
        return self._unwrap(self._post(f"logbook/api/drafts/{draft_id}/approve"))

    def request_signature(self, entry_id: str, cfi_email: str) -> dict:
        """Request a CFI signature on an entry."""
        return self._unwrap(
            self._post(f"logbook/api/signature/{entry_id}", params={"cfiEmail": cfi_email})
        )

    def delete_signature(self, signature_id: str) -> dict:
        """Delete a signature."""
        return self._unwrap(self._delete(f"logbook/api/signature/{signature_id}"))

    def accept_signature(self, signature_detail_id: str) -> dict:
        """Accept a pending signature request (as the signing CFI)."""
        return self._unwrap(
            self._post(
                f"logbook/api/signatureDetails/{signature_detail_id}",
                params={"action": "accept"},
            )
        )

    def withdraw_signature_request(self, signature_detail_id: str) -> dict:
        """Withdraw a signature request you previously sent."""
        return self._unwrap(
            self._delete(f"logbook/api/signatureDetails/{signature_detail_id}")
        )

    def get_sign_flight_info(self, token: str) -> dict:
        """Return the flight info for a sign-flight request token."""
        return self._result(f"logbook/api/getSignFlightInfo/{token}")

    def post_sign_flight_info(self, info: dict) -> dict:
        """Submit a signed flight (CFI completes the signature)."""
        return self._unwrap(self._post("logbook/api/postSignFlightInfo", payload=info))

    def decline_sign_flight(self, info: dict) -> dict:
        """Decline a sign-flight request."""
        return self._unwrap(self._post("logbook/api/declineSignFlight", payload=info))

    def create_draft_from_signature_request(self, request: dict) -> dict:
        """Create a new draft entry from an incoming signature request."""
        return self._unwrap(
            self._post("logbook/api/createNewDraftFromSignatureReq", payload=request)
        )

    # ------------------------------------------------------------------ #
    # Tracklogs
    # ------------------------------------------------------------------ #

    def get_tracklogs_available(
        self, page: int = 0, page_size: int = MAX_TRACKLOG_PAGE_SIZE
    ) -> list:
        """
        Return one page of available tracklog metadata.

        :param page: Zero-based page index.
        :param page_size: Number of tracklogs per page. Clamped to
            :data:`MAX_TRACKLOG_PAGE_SIZE` (100); larger values are rejected by
            ForeFlight with HTTP 400.
        """
        page_size = min(page_size, MAX_TRACKLOG_PAGE_SIZE)
        params = {"pageSize": page_size, "page": page}
        return self._get("tracklogs/api/tracklogs", params=params).json()["tracklogs"]

    def get_all_tracklogs_available(self) -> list:
        """Page through and return metadata for every available tracklog.

        Pages in chunks of :data:`MAX_TRACKLOG_PAGE_SIZE` and stops on the first
        short (or empty) page, so a library with hundreds of tracklogs needs
        only a handful of requests.
        """
        all_tracklogs = []
        page = 0
        while True:
            current_page = self.get_tracklogs_available(
                page=page, page_size=MAX_TRACKLOG_PAGE_SIZE
            )
            all_tracklogs.extend(current_page)
            if len(current_page) < MAX_TRACKLOG_PAGE_SIZE:
                break
            page += 1
        return all_tracklogs

    def get_tracklog(self, tracklog_id: str, tracklog_format: TRACKLOG_FORMAT = "gpx") -> str:
        """
        Export a single tracklog in the requested format.

        :param tracklog_id: ``trackUuid`` of the tracklog to export.
        :param tracklog_format: One of ``gpx``, ``kml``, ``kml-filtered``, ``csv``.
        """
        path = f"tracklogs/export/{tracklog_id}/{tracklog_format}"
        return self._get(path).text
