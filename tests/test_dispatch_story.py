"""下发资格、源数据更新与补正撤回的行为测试。"""


def seed(client):
    response = client.post("/api/dispatch/demo")
    assert response.status_code == 200
    return response.get_json()


def ids(board):
    return {g["group_id"]: g for g in board["groups"]}


def test_source_update_only_stops_affected_group(client):
    board = seed(client)
    assert board["summary"]["dispatchable"] == 3

    response = client.post("/api/source-updates", json={
        "update_id": "SRC-1", "group_id": "M1|2026-11-01", "reason": "票据金额更新",
    })
    assert response.status_code == 200
    board = client.get("/api/dispatch/board").get_json()
    groups = ids(board)
    assert groups["M1|2026-11-01"]["can_dispatch"] is False
    assert groups["M2|2026-11-02"]["can_dispatch"] is True
    assert groups["M3|2026-11-03"]["can_dispatch"] is True
    assert board["dispatchable_group_ids"] == ["M2|2026-11-02", "M3|2026-11-03"]


def test_old_credential_is_frozen_and_archived(client):
    before = seed(client)
    old = before["certificates"][0]
    client.post("/api/source-updates", json={"update_id": "SRC-2", "group_id": old["group_id"]})

    after = client.get("/api/dispatch/board").get_json()
    group = ids(after)[old["group_id"]]
    assert group["original_credential"]["credential_sha256"] == old["credential_sha256"]
    assert group["credential"]["credential_sha256"] == old["credential_sha256"]
    assert any(c["credential_sha256"] == old["credential_sha256"] for c in after["certificates"])


def test_member_change_holds_both_moved_groups(client):
    seed(client)
    payload = {
        "correction_id": "COR-MOVE",
        "claim_id": "K3",
        "new_member_id": "M9",
        "new_service_date": "2026-11-09",
    }
    first = client.post("/api/corrections", json=payload)
    assert first.status_code == 200
    data = first.get_json()
    assert data["from_group_id"] == "M2|2026-11-02"
    assert data["to_group_id"] == "M9|2026-11-09"

    groups = ids(client.get("/api/dispatch/board").get_json())
    assert groups["M2|2026-11-02"]["can_dispatch"] is False
    assert groups["M9|2026-11-09"]["can_dispatch"] is False
    assert groups["M1|2026-11-01"]["can_dispatch"] is True


def test_withdraw_restores_original_and_resumes_groups_without_pending_corrections(client):
    seed(client)
    payload = {"correction_id": "COR-WD", "claim_id": "K3",
               "new_member_id": "M9", "new_service_date": "2026-11-09"}
    client.post("/api/corrections", json=payload)
    response = client.post("/api/corrections/COR-WD/withdraw")
    assert response.status_code == 200
    assert response.get_json()["status"] == "WITHDRAWN"

    board = client.get("/api/dispatch/board").get_json()
    groups = ids(board)
    assert "M9|2026-11-09" not in groups
    restored = groups["M2|2026-11-02"]
    assert restored["can_dispatch"] is True
    assert restored["pending_correction_ids"] == []
    assert restored["original_issuance"]["claim_ids"] == ["K3"]
    assert restored["issuance"]["claim_ids"] == ["K3"]


def test_two_pending_corrections_require_both_withdrawn(client):
    seed(client)
    client.post("/api/corrections", json={"correction_id": "C1", "group_id": "M1|2026-11-01"})
    client.post("/api/corrections", json={"correction_id": "C2", "group_id": "M1|2026-11-01"})
    assert ids(client.get("/api/dispatch/board").get_json())["M1|2026-11-01"]["can_dispatch"] is False
    client.post("/api/corrections/C1/withdraw")
    group = ids(client.get("/api/dispatch/board").get_json())["M1|2026-11-01"]
    assert group["can_dispatch"] is False
    client.post("/api/corrections/C2/withdraw")
    group = ids(client.get("/api/dispatch/board").get_json())["M1|2026-11-01"]
    assert group["can_dispatch"] is True


def test_same_correction_retry_and_withdrawn_replay_do_not_reapply(client):
    seed(client)
    payload = {"correction_id": "COR-IDEM", "claim_id": "K3",
               "new_member_id": "M9", "new_service_date": "2026-11-09"}
    first = client.post("/api/corrections", json=payload).get_json()
    retry = client.post("/api/corrections", json=payload).get_json()
    assert first["correction_id"] == retry["correction_id"]
    assert retry["idempotent_hit"] is True
    assert len(client.get("/api/dispatch/board").get_json()["events"]) >= 1

    client.post("/api/corrections/COR-IDEM/withdraw")
    replayed = client.post("/api/corrections", json=payload).get_json()
    assert replayed["status"] == "WITHDRAWN"
    assert replayed["idempotent_hit"] is True
    board = client.get("/api/dispatch/board").get_json()
    assert board["dispatchable_group_ids"] == [
        "M1|2026-11-01", "M2|2026-11-02", "M3|2026-11-03"]


def test_dispatch_endpoint_is_idempotent_when_eligible(client):
    seed(client)
    body = {"dispatch_id": "D-1"}
    first = client.post("/api/groups/M2|2026-11-02/dispatch", json=body)
    second = client.post("/api/groups/M2|2026-11-02/dispatch", json=body)
    assert first.status_code == second.status_code == 200
    group = second.get_json()
    assert group["idempotent_hit"] is True
    assert group["dispatch_count"] == 1


def test_approved_run_gets_dispatch_board(client):
    run = client.post("/api/runs", json={"profile": "clean"}).get_json()
    client.post(f"/api/runs/{run['run_id']}/review",
                json={"decision": "APPROVED", "comment": "ok", "expected_revision": 0})
    response = client.get(f"/api/runs/{run['run_id']}/dispatch")
    assert response.status_code == 200
    board = response.get_json()
    assert {g["group_id"] for g in board["groups"]} == {
        "M1|2026-11-01", "M2|2026-11-02", "M3|2026-11-03"}
