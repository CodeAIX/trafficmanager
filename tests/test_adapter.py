import httpx
import pytest
from backend.app.adapters.threexui import AdapterError, ModernThreeXUIAdapter, detect_adapter, normalize_base_url
from tests.mock_3xui import create_mock


def test_base_path_and_scheme_validation():
    assert normalize_base_url("https://host:2053/abc/") == "https://host:2053/abc"
    with pytest.raises(ValueError):
        normalize_base_url("file:///tmp/x")


@pytest.mark.asyncio
async def test_modern_detection_bearer_and_large_counter():
    transport = httpx.ASGITransport(app=create_mock())
    adapter, caps, sha = await detect_adapter("http://mock/base", "test-token", transport=transport)
    assert isinstance(adapter, ModernThreeXUIAdapter)
    assert caps["modern_clients_api"] and sha
    client = await adapter.get_client("user@example.com")
    assert client["totalGB"] == 2**40
    await adapter.close()


@pytest.mark.asyncio
async def test_auth_error_not_retried():
    transport = httpx.ASGITransport(app=create_mock())
    adapter = ModernThreeXUIAdapter("http://mock/base", "bad", transport=transport)
    with pytest.raises(AdapterError) as error:
        await adapter.get_client("user@example.com")
    assert error.value.code == "AUTH_FAILED"
    await adapter.close()


@pytest.mark.asyncio
async def test_read_modify_write_preserves_secret_and_verify_reset():
    transport = httpx.ASGITransport(app=create_mock())
    adapter = ModernThreeXUIAdapter("http://mock/base", "test-token", transport=transport)
    updated = await adapter.update_client_quota("user@example.com", 2**42)
    assert updated["id"] == "secret-not-persisted"
    await adapter.reset_client_traffic("user@example.com")
    assert (await adapter.verify_client("user@example.com", 2**42))["verified"]
    await adapter.close()

