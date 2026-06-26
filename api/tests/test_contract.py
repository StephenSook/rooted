"""Contract test: the SBR API's JSON/GET surface conforms to its own OpenAPI schema (schemathesis).

Schemathesis generates requests for every GET/JSON operation from /openapi.json and validates that
each response conforms to the declared schema, status code, content type, and headers, and that no
input triggers a server error. This proves the SBR API is self-consistent with the contract the
typed front-end client and the MCP server are generated against. It runs in-process against the ASGI
app (no network, no credentials), derandomized so results are stable across machines and CI runs.

The two multipart file-upload operations (POST /ingest, POST /matches/byContent) are excluded: their
real input is binary image content, which OpenAPI's binary type cannot model, so schemathesis can
only exercise framework-level body-parsing errors rather than the actual contract. Their response
schemas (SoftBindingQueryResult and the ingest ack) are the same models validated on the GET surface
and exercised directly in the unit tests.
"""

from __future__ import annotations

import schemathesis
from hypothesis import settings

from rooted_api.main import app

schema = schemathesis.openapi.from_asgi("/openapi.json", app).exclude(method="POST")


@schema.parametrize()
@settings(max_examples=25, deadline=None, derandomize=True)
def test_sbr_api_conforms_to_its_openapi(case: schemathesis.Case) -> None:
    case.call_and_validate()
