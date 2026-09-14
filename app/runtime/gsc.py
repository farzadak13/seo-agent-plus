"""Access-token GSC transport. OAuth token refresh belongs to credential onboarding."""
import json as json_module
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from app.gsc.client import GSCClient, GSCClientError
from app.gsc.config import GSCClientConfig


class JSONResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body

    def json(self):
        return json_module.loads(self.body)


def google_request(method, path, *, headers, json, timeout):
    request = Request("https://www.googleapis.com" + path,
                      data=json_module.dumps(json).encode(), headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return JSONResponse(response.status, response.read())
    except HTTPError as exc:
        exc.close()
        return JSONResponse(exc.code, b"{}")
    except Exception:
        raise GSCClientError("GSC network request failed.", retryable=True) from None


class LiveGSCGateway:
    def __init__(self, site, *, timeout=30, request_fn=google_request):
        self.site = site
        self.timeout = timeout
        self.request_fn = request_fn

    def fetch(self, **kwargs):
        return self._fetch(dimensions=("page", "query", "date"), **kwargs)

    def fetch_url_metrics(self, **kwargs):
        return self._fetch(dimensions=("page", "date"), **kwargs)

    def _fetch(self, *, dimensions, site_id, property_url, credential, start_date, end_date):
        if self.site.gsc.auth_mode != "access_token":
            raise ValueError("This gateway requires access_token auth; configure a token provider for other modes.")
        client = GSCClient(GSCClientConfig(oauth_access_token=credential,
            timeout_seconds=self.timeout, page_size=self.site.gsc.row_limit), request_fn=self.request_fn)
        response = client.query(site_id=site_id, site_url=property_url, start_date=start_date, end_date=end_date, dimensions=dimensions)
        # GSC metrics are JSON numbers; preserve only exact non-negative counts.
        rows = []
        for original in response.raw_payload.get("rows", []):
            row = dict(original)
            for name in ("clicks", "impressions"):
                value = row.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or int(value) != value:
                    raise ValueError("Invalid GSC count.")
                row[name] = int(value)
            if len(row.get("keys", [])) != len(dimensions):
                raise ValueError("GSC response dimensions do not match request.")
            rows.append(row)
        return response.model_copy(update={"raw_payload": {**response.raw_payload, "rows": rows}})
