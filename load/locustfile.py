"""Demo-safety load smoke for the Rooted SBR read endpoints. Run before a demo or deploy:

    uv run uvicorn rooted_api.main:app --port 8000        # one shell
    uvx locust -f load/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 15s

It hits only safe GET endpoints (no DB writes, no generation): liveness, supportedAlgorithms,
byBinding (the empty-match read path), a 404 manifest, and the signed checkpoint. Watch the p95
latency and failure rate so the live demo will not fall over under concurrent judges. locust is run
via uvx so it is not a project dependency.
"""

from locust import HttpUser, between, task


class SbrReader(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(3)
    def health(self) -> None:
        self.client.get("/health")

    @task(3)
    def supported_algorithms(self) -> None:
        self.client.get("/services/supportedAlgorithms")

    @task(5)
    def matches_by_binding(self) -> None:
        # The empty-match read path: a binding that resolves to no manifest exercises the resolver.
        self.client.get(
            "/matches/byBinding",
            params={"alg": "com.adobe.trustmark.P", "value": "RTNONE"},
        )

    @task(2)
    def checkpoint(self) -> None:
        self.client.get("/transparency/checkpoint")

    @task(1)
    def manifest_not_found(self) -> None:
        # 404 is the correct answer for an unknown manifest, so do not count it as a failure.
        with self.client.get("/manifests/urn:c2pa:none", catch_response=True) as r:
            if r.status_code == 404:
                r.success()
