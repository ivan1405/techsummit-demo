from app import mcp_tools


def test_mcp_requests_carry_the_role_and_only_analysts_send_the_token(monkeypatch):
    monkeypatch.setattr(mcp_tools, "MCP_ANALYST_TOKEN", "s3cret")
    assert mcp_tools._http_client({"a": "b"}).headers["x-data-role"] == "reader"
    assert "x-data-token" not in mcp_tools._http_client().headers
    token = mcp_tools.role.set("analyst")
    try:
        headers = mcp_tools._http_client().headers
    finally:
        mcp_tools.role.reset(token)
    assert headers["x-data-role"] == "analyst" and headers["x-data-token"] == "s3cret"
