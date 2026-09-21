"""The Power BI Service.

Off unless credentials are configured, the same bargain as `ai.py`: with
nothing set, `is_configured()` is False, the routes answer 503 with a plain
reason, and the UI shows the manual half only. Dashboards you paste by hand
work with none of this.

Authentication is a service principal -- client credentials, no user sign-in
-- because this has to run from a scheduled task with nobody at the keyboard.
That is also why it needs setting up by whoever administers your tenant: a
service principal has to be created in Azure AD *and* allowed in the Power BI
admin portal, and neither is something an ordinary account can do for itself.

**A service principal sees every workspace it is added to, not just yours.**
Scope it to the workspaces you actually want read, rather than granting
tenant-wide read and relying on this code to be careful.
"""

import os
from datetime import datetime
from typing import Any, Optional

import httpx

AUTHORITY = "https://login.microsoftonline.com"
API_ROOT = "https://api.powerbi.com/v1.0/myorg"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"
TIMEOUT = httpx.Timeout(30.0)

# Refresh history is only read one entry deep: the question this answers is
# "is the data behind this report currently broken", not "how has it behaved".
REFRESH_HISTORY_DEPTH = 1


class NotConfigured(RuntimeError):
    """No credentials."""


class PowerBIError(RuntimeError):
    """The call was made and came back unusable."""


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def tenant_id() -> str:
    return _env("PBI_TENANT_ID")


def client_id() -> str:
    return _env("PBI_CLIENT_ID")


def client_secret() -> str:
    return _env("PBI_CLIENT_SECRET")


def static_token() -> str:
    """A token pasted in by hand.

    An escape hatch worth having: a token copied out of the Power BI developer
    tools proves the rest of this works before anyone raises a ticket for a
    service principal. It expires in about an hour, so it is for trying the
    thing out, not for running it.
    """
    return _env("PBI_ACCESS_TOKEN")


def is_configured() -> bool:
    return bool(static_token()) or all((tenant_id(), client_id(), client_secret()))


def status() -> dict:
    # The identity this connects with is an application, not a person, so
    # anything it reads is read with the application's access. That is fine
    # for refresh state and wrong for dataset contents -- see
    # docs/powerbi-delegated-access.md.
    configured = is_configured()
    missing = [
        name
        for name in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET")
        if not _env(name)
    ]
    return {
        "configured": configured,
        "mode": "token" if static_token() else ("service_principal" if configured else None),
        "missing": [] if configured else missing,
        "reason": None
        if configured
        else (
            "Power BI isn't connected. Set PBI_TENANT_ID, PBI_CLIENT_ID and "
            "PBI_CLIENT_SECRET in backend/.env, or PBI_ACCESS_TOKEN to try it "
            "with a temporary token."
        ),
    }


def access_token() -> str:
    """A bearer token for the Power BI REST API."""
    pasted = static_token()
    if pasted:
        return pasted
    if not is_configured():
        raise NotConfigured(status()["reason"])

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                f"{AUTHORITY}/{tenant_id()}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id(),
                    "client_secret": client_secret(),
                    "scope": SCOPE,
                },
            )
    except httpx.RequestError as exc:
        raise PowerBIError(f"Couldn't reach Azure AD to sign in ({exc}).") from exc

    if response.is_error:
        # Azure's own description is far more useful than anything this layer
        # could invent -- it names the wrong secret, the wrong tenant, the
        # consent that was never granted.
        try:
            body = response.json()
            detail = body.get("error_description") or body.get("error") or response.text
        except ValueError:
            detail = response.text[:300]
        raise PowerBIError(f"Azure AD rejected the sign-in: {str(detail)[:300]}")

    token = response.json().get("access_token")
    if not token:
        raise PowerBIError("Azure AD returned no access token.")
    return token


def _get(client: httpx.Client, path: str) -> Any:
    response = client.get(path)
    if response.is_error:
        try:
            message = response.json().get("error", {}).get("message", response.reason_phrase)
        except ValueError:
            message = response.reason_phrase
        if response.status_code in (401, 403):
            message = (
                f"{message} -- the service principal may not be added to that "
                "workspace, or 'service principals can use Power BI APIs' may "
                "be off in the admin portal."
            )
        raise PowerBIError(f"Power BI {response.status_code}: {message}")
    return response.json()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def fetch_reports(workspace: Optional[str] = None) -> list[dict]:
    """Every report the credentials can see, with its dataset's refresh state.

    One call for workspaces, one per workspace for reports, one per dataset
    for refresh history. That last group is the expensive part, which is why
    only the newest entry is asked for.
    """
    token = access_token()
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict] = []

    with httpx.Client(base_url=API_ROOT, headers=headers, timeout=TIMEOUT) as client:
        groups = _get(client, "/groups").get("value", [])
        if workspace:
            wanted = workspace.lower()
            groups = [
                g
                for g in groups
                if g.get("id") == workspace or (g.get("name") or "").lower() == wanted
            ]

        for group in groups:
            gid, gname = group.get("id"), group.get("name")
            if not gid:
                continue

            datasets = {
                d.get("id"): d
                for d in _get(client, f"/groups/{gid}/datasets").get("value", [])
            }
            refresh_by_dataset: dict[str, dict] = {}

            for dataset_id, dataset in datasets.items():
                # Only a refreshable dataset has a history; asking anyway is a
                # guaranteed 400 per dataset.
                if not dataset.get("isRefreshable"):
                    continue
                try:
                    history = _get(
                        client,
                        f"/groups/{gid}/datasets/{dataset_id}/refreshes"
                        f"?$top={REFRESH_HISTORY_DEPTH}",
                    ).get("value", [])
                except PowerBIError:
                    # A dataset nobody can read should not cost the whole sync.
                    continue
                if history:
                    refresh_by_dataset[dataset_id] = history[0]

            for report in _get(client, f"/groups/{gid}/reports").get("value", []):
                dataset_id = report.get("datasetId")
                refresh = refresh_by_dataset.get(dataset_id, {})
                out.append(
                    {
                        "external_id": report.get("id"),
                        "name": report.get("name"),
                        "url": report.get("webUrl"),
                        "workspace_id": gid,
                        "workspace_name": gname,
                        "dataset_id": dataset_id,
                        "dataset_name": (datasets.get(dataset_id) or {}).get("name"),
                        "last_refresh_at": _parse_time(
                            refresh.get("endTime") or refresh.get("startTime")
                        ),
                        "refresh_status": refresh.get("status"),
                        "refresh_error": (refresh.get("serviceExceptionJson") or None),
                    }
                )
    return out
