from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import accounts


def _build_accounts_client() -> TestClient:
    app = FastAPI()
    app.include_router(accounts.router)
    return TestClient(app)


def test_account_crud_uses_configured_state_dir(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("ACCOUNT_STATE_DIR", str(state_dir))
    client = _build_accounts_client()

    create_response = client.post(
        "/api/accounts",
        json={"name": "主账号", "content": '{"cookies": [], "origins": []}'},
    )
    assert create_response.status_code == 200

    list_response = client.get("/api/accounts")
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["name"] == "主账号"
    assert payload[0]["path"].endswith(".json")
    assert "主账号.json" not in payload[0]["path"]

    detail_response = client.get("/api/accounts/%E4%B8%BB%E8%B4%A6%E5%8F%B7")
    assert detail_response.status_code == 200
    content = detail_response.json()["content"]
    if isinstance(content, str):
        assert '"cookies"' in content
    else:
        assert "cookies" in content

    update_response = client.put(
        "/api/accounts/%E4%B8%BB%E8%B4%A6%E5%8F%B7",
        json={"content": '{"cookies": [{"name": "tracknick"}], "origins": []}'},
    )
    assert update_response.status_code == 200

    delete_response = client.delete("/api/accounts/%E4%B8%BB%E8%B4%A6%E5%8F%B7")
    assert delete_response.status_code == 200
    assert state_dir.exists()
    assert list(path for path in state_dir.glob("*.json") if path.name != ".accounts.json") == []


class _FakeBrowserLoginService:
    def __init__(self):
        self.started_payloads = []

    async def start_job(self, account_name: str, *, set_as_default: bool = True):
        self.started_payloads.append((account_name, set_as_default))
        return {
            "id": "job-1",
            "account_name": account_name,
            "status": "awaiting_scan",
            "message": "浏览器已打开",
            "created_at": "2026-04-07T00:00:00Z",
            "updated_at": "2026-04-07T00:00:00Z",
            "finished_at": None,
            "error": None,
            "account_path": "state/acc_main.json",
            "set_as_default": set_as_default,
            "default_state_path": "xianyu_state.json" if set_as_default else None,
            "browser_opened": True,
        }

    async def get_job(self, job_id: str):
        if job_id != "job-1":
            raise KeyError(job_id)
        return {
            "id": "job-1",
            "account_name": "acc_main",
            "status": "completed",
            "message": "登录状态已保存。",
            "created_at": "2026-04-07T00:00:00Z",
            "updated_at": "2026-04-07T00:00:05Z",
            "finished_at": "2026-04-07T00:00:05Z",
            "error": None,
            "account_path": "state/acc_main.json",
            "set_as_default": True,
            "default_state_path": "xianyu_state.json",
            "browser_opened": True,
        }


def test_browser_login_routes(monkeypatch):
    fake_service = _FakeBrowserLoginService()
    monkeypatch.setattr(accounts, "browser_login_service", fake_service)
    client = _build_accounts_client()

    create_response = client.post(
        "/api/accounts/browser-login",
        json={"name": "主账号", "set_as_default": True},
    )
    assert create_response.status_code == 200
    assert fake_service.started_payloads == [("主账号", True)]
    assert create_response.json()["status"] == "awaiting_scan"

    status_response = client.get("/api/accounts/browser-login/job-1")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"

    missing_response = client.get("/api/accounts/browser-login/job-missing")
    assert missing_response.status_code == 404
