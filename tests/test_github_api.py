from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import github_api


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.raw = io.BytesIO(json.dumps(payload).encode())

    def read(self) -> bytes:
        return self.raw.read()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args) -> None:
        return None


class GraphqlTests(unittest.TestCase):
    def http_error(self, code: int) -> urllib.error.HTTPError:
        err = urllib.error.HTTPError(github_api.API, code, "err", {}, io.BytesIO())
        self.addCleanup(err.close)
        return err

    def test_returns_data_and_sends_auth_header(self) -> None:
        seen = {}

        def fake_open(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            seen["body"] = json.loads(req.data.decode())
            seen["timeout"] = timeout
            return FakeResponse({"data": {"ok": True}})

        with mock.patch("urllib.request.urlopen", side_effect=fake_open):
            result = github_api.graphql("query { viewer { login } }", {"a": 1}, "secret")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(seen["auth"], "bearer secret")
        self.assertEqual(seen["body"]["variables"], {"a": 1})
        self.assertEqual(seen["timeout"], 30)

    def test_retries_transient_5xx_then_succeeds(self) -> None:
        calls = [self.http_error(502), self.http_error(503), FakeResponse({"data": {"n": 3}})]
        with mock.patch("urllib.request.urlopen", side_effect=calls), \
             mock.patch("time.sleep") as sleep_mock:
            result = github_api.graphql("q", {}, "tok")

        self.assertEqual(result, {"n": 3})
        self.assertEqual(sleep_mock.call_args_list, [mock.call(5), mock.call(10)])

    def test_raises_after_exhausting_retries(self) -> None:
        calls = [self.http_error(502), self.http_error(503), self.http_error(504)]
        with mock.patch("urllib.request.urlopen", side_effect=calls), \
             mock.patch("time.sleep") as sleep_mock:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                github_api.graphql("q", {}, "tok")
        self.assertEqual(ctx.exception.code, 504)
        self.assertEqual(sleep_mock.call_args_list, [mock.call(5), mock.call(10)])

    def test_retries_network_errors_then_succeeds(self) -> None:
        calls = [urllib.error.URLError("connection reset"), TimeoutError("timed out"),
                 FakeResponse({"data": {"n": 3}})]
        with mock.patch("urllib.request.urlopen", side_effect=calls), \
             mock.patch("time.sleep") as sleep_mock:
            result = github_api.graphql("q", {}, "tok")

        self.assertEqual(result, {"n": 3})
        self.assertEqual(sleep_mock.call_args_list, [mock.call(5), mock.call(10)])

    def test_raises_network_error_after_exhausting_retries(self) -> None:
        calls = [urllib.error.URLError("down")] * 3
        with mock.patch("urllib.request.urlopen", side_effect=calls), \
             mock.patch("time.sleep"):
            with self.assertRaises(urllib.error.URLError):
                github_api.graphql("q", {}, "tok")

    def test_does_not_retry_client_errors(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=self.http_error(403)), \
             mock.patch("time.sleep") as sleep_mock:
            with self.assertRaises(urllib.error.HTTPError):
                github_api.graphql("q", {}, "tok")
        sleep_mock.assert_not_called()

    def test_raises_on_graphql_errors(self) -> None:
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse({"errors": [{"message": "bad"}]})):
            with self.assertRaises(RuntimeError):
                github_api.graphql("q", {}, "tok")

    def test_allows_not_found_errors_when_opted_in(self) -> None:
        payload = {
            "data": {"repository": None},
            "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "Could not resolve"}],
        }
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            result = github_api.graphql("q", {}, "tok", allow_not_found=True)
        self.assertEqual(result, {"repository": None})

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError):
                github_api.graphql("q", {}, "tok")

    def test_raises_on_mixed_errors_or_null_data_even_with_allow_not_found(self) -> None:
        mixed = {
            "data": {"repository": None},
            "errors": [{"type": "NOT_FOUND"}, {"type": "RATE_LIMITED"}],
        }
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(mixed)):
            with self.assertRaises(RuntimeError):
                github_api.graphql("q", {}, "tok", allow_not_found=True)

        null_data = {
            "data": None,
            "errors": [{"type": "NOT_FOUND"}],
        }
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(null_data)):
            with self.assertRaises(RuntimeError):
                github_api.graphql("q", {}, "tok", allow_not_found=True)


if __name__ == "__main__":
    unittest.main()
