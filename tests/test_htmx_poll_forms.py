from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


def _read(*parts: str) -> str:
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


def test_workspace_poll_does_not_wrap_vlan_select():
    workspace = _read("partials", "workspace.html")
    pane = _read("partials", "port_pane.html")
    assert 'id="switch-workspace"' in workspace
    workspace_open = workspace.split('id="switch-workspace"')[1].split(">")[0]
    assert "every 2s" not in workspace_open
    assert "hx-trigger" not in workspace_open
    assert 'name="vlan_id"' in pane
    assert 'id="pane-actions"' in pane
    assert 'id="pane-status"' in pane
    status_block = pane.split('id="pane-status"')[1].split('id="pane-actions"')[0]
    actions_block = pane.split('id="pane-actions"')[1]
    assert "every 2s" in status_block
    assert 'name="vlan_id"' not in status_block
    assert 'name="vlan_id"' in actions_block
    assert "every 2s" not in actions_block


def test_faceplate_poll_is_isolated_from_forms():
    workspace = _read("partials", "workspace.html")
    faceplate = _read("partials", "faceplate.html")
    assert 'id="faceplate-live"' in workspace
    live = workspace.split('id="faceplate-live"')[1].split("led-legend")[0]
    assert "every 2s" in live
    assert 'name="vlan_id"' not in live
    assert 'name="vlan_id"' not in faceplate


def test_selected_port_page_keeps_select_outside_poll_target(client):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/switches/1?port=1")
    assert page.status_code == 200
    html = page.text
    assert 'name="vlan_id"' in html
    assert 'id="pane-actions"' in html
    assert 'id="pane-status"' in html
    assert 'hx-get="/partials/switches/1/pane-status?port=1"' in html
    status = html.split('id="pane-status"')[1].split('id="pane-actions"')[0]
    actions = html.split('id="pane-actions"')[1].split("id=\"pane-admin\"")[0]
    assert "every 2s" in status
    assert 'name="vlan_id"' not in status
    assert 'name="vlan_id"' in actions
    assert "every 2s" not in actions


def test_pane_status_partial_has_no_select(client):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    response = client.get("/partials/switches/1/pane-status?port=1")
    assert response.status_code == 200
    assert 'name="vlan_id"' not in response.text
    assert "Connected for" in response.text or "Not connected" in response.text
