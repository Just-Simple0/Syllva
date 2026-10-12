"""P-E E3-E5: bounded Canvas file download; the token never leaves the Canvas origin."""
from __future__ import annotations

import pytest

from uls.settings.canvas_checks import CanvasCheckError, CanvasResponse, open_canvas_connection

pytestmark = pytest.mark.unit

ORIGIN = "https://canvas.example.edu"
STORAGE = "files.storage.example.com"
URL = f"{ORIGIN}/files/31/download?download_frd=1&verifier=secret-v"
DATA = b"%PDF-1.4 abc"


class World:
    def __init__(self, routes: dict[tuple[str, str], CanvasResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.connections: list[str] = []

    def resolver(self, host: str, port: int):
        return ["93.184.216.34"]

    def connector(self, host: str, port: int, address: str, timeout: float):
        self.connections.append(host)
        world = self

        class Transport:
            def fetch(self, target: str, headers, timeout: float, max_bytes: int) -> CanvasResponse:
                world.calls.append((host, target, dict(headers)))
                response = world.routes[(host, target)]
                if len(response.body) > max_bytes:
                    raise CanvasCheckError("SIZE_MISMATCH")
                return response

            def request(self, method, target, headers, timeout):
                world.calls.append((host, target, dict(headers)))
                return world.routes[(host, target)]

            def close(self) -> None:
                return None

        return Transport()

    def download(self, url: str = URL, *, size: int = len(DATA), hosts=(STORAGE,)) -> bytes:
        with open_canvas_connection(ORIGIN, resolver=self.resolver, connector=self.connector) as connection:
            return connection.download(url, "tok", course_id=67535, file_id="31", expected_size=size,
                                       deadline=_far(), file_hosts=hosts, resolver=self.resolver,
                                       connector=self.connector)


def _far() -> float:
    import time
    return time.monotonic() + 30


def _ok(body: bytes = DATA) -> CanvasResponse:
    return CanvasResponse(200, {}, body)


def _redirect(location: str) -> CanvasResponse:
    return CanvasResponse(302, {"Location": location}, b"")


PATH = "/files/31/download?download_frd=1&verifier=secret-v"


def test_a_same_origin_download_returns_the_exact_bytes_with_the_token() -> None:
    world = World({("canvas.example.edu", PATH): _ok()})
    assert world.download() == DATA
    [(host, _target, headers)] = world.calls
    assert host == "canvas.example.edu" and headers["Authorization"] == "Bearer tok"


def test_a_redirect_to_an_allowlisted_storage_host_is_followed_without_the_token() -> None:
    world = World({
        ("canvas.example.edu", PATH): _redirect(f"https://{STORAGE}/blob/31?sig=abc"),
        (STORAGE, "/blob/31?sig=abc"): _ok(),
    })
    assert world.download() == DATA
    assert world.connections == ["canvas.example.edu", STORAGE]
    storage_headers = [h for host, _, h in world.calls if host == STORAGE]
    assert storage_headers and all("Authorization" not in h for h in storage_headers)


def test_a_redirect_to_another_host_is_refused_before_any_request() -> None:
    world = World({("canvas.example.edu", PATH): _redirect("https://evil.example.net/blob?sig=abc")})
    with pytest.raises(CanvasCheckError) as error:
        world.download()
    assert error.value.code == "DESTINATION_NOT_ALLOWED" and world.connections == ["canvas.example.edu"]
    assert "sig" not in repr(error.value) and "evil" not in str(error.value)


def test_storage_redirects_may_not_leave_the_storage_host() -> None:
    world = World({
        ("canvas.example.edu", PATH): _redirect(f"https://{STORAGE}/a"),
        (STORAGE, "/a"): _redirect(f"https://other.{STORAGE}/b"),
    })
    with pytest.raises(CanvasCheckError) as error:
        world.download(hosts=(STORAGE, f"other.{STORAGE}"))
    assert error.value.code == "DESTINATION_NOT_ALLOWED"


def test_the_redirect_chain_is_bounded() -> None:
    loop = "/files/31/download?download_frd=1"
    world = World({
        ("canvas.example.edu", PATH): _redirect(loop),
        ("canvas.example.edu", loop): _redirect(loop),
    })
    with pytest.raises(CanvasCheckError) as error:
        world.download()
    assert error.value.code == "PROVIDER_UNAVAILABLE" and len(world.calls) == 3


@pytest.mark.parametrize("url", [
    f"{ORIGIN}/files/32/download",                       # another file
    f"{ORIGIN}/api/v1/users/self",                       # not a download path
    f"{ORIGIN}/files/31/download?download_frd=1&x=1",    # unexpected query key
    f"{ORIGIN}/courses/1/files/31/download",             # another course
])
def test_only_this_files_download_path_is_requested(url: str) -> None:
    world = World({})
    with pytest.raises(CanvasCheckError) as error:
        world.download(url)
    assert error.value.code == "CHECK_NOT_READ_ONLY" and world.calls == []


def test_a_download_url_on_another_origin_is_refused() -> None:
    world = World({})
    with pytest.raises(CanvasCheckError) as error:
        world.download(f"https://{STORAGE}/files/31/download")
    assert error.value.code == "DESTINATION_NOT_ALLOWED" and world.calls == []


@pytest.mark.parametrize("body", [DATA[:-1], DATA + b"x"])
def test_anything_but_the_declared_size_is_rejected(body: bytes) -> None:
    world = World({("canvas.example.edu", PATH): _ok(body)})
    with pytest.raises(CanvasCheckError) as error:
        world.download()
    assert error.value.code == "SIZE_MISMATCH"


def test_the_file_object_is_read_course_scoped() -> None:
    world = World({("canvas.example.edu", "/api/v1/courses/67535/files/31"):
                   CanvasResponse(200, {}, b'{"id": 31}')})
    with open_canvas_connection(ORIGIN, resolver=world.resolver, connector=world.connector) as connection:
        assert connection.file_metadata(67535, "31", "tok", deadline=_far()) == {"id": 31}
        with pytest.raises(CanvasCheckError):
            connection.file_metadata(67535, "../1", "tok", deadline=_far())
    assert [t for _, t, _ in world.calls] == ["/api/v1/courses/67535/files/31"]



def test_the_redirect_budget_is_shared_between_canvas_and_storage_hops() -> None:
    loop = "/files/31/download?download_frd=1"
    world = World({
        ("canvas.example.edu", PATH): _redirect(loop),
        ("canvas.example.edu", loop): _redirect(f"https://{STORAGE}/a"),
        (STORAGE, "/a"): _redirect(f"https://{STORAGE}/b"),
        (STORAGE, "/b"): _ok(),
    })
    with pytest.raises(CanvasCheckError):
        world.download()
    assert [h for h, _, _ in world.calls] == ["canvas.example.edu", "canvas.example.edu", STORAGE]
