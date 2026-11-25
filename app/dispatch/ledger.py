"""理赔组下发资格、源数据更新与补正撤回的纯领域模型。

一个理赔组默认按 ``member_id|service_date`` 标识。已签发内容和凭证会冻结在
``original_issuance`` / ``original_credential`` 中；源数据更新或待核补正只会
增加生效拦截，不会改写留档凭证。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

READY = "READY"
DISPATCHABLE = "DISPATCHABLE"
HELD = "HELD"
RECHECK_REQUIRED = "RECHECK_REQUIRED"
SOURCE_CHANGED = "SOURCE_CHANGED"
WITHDRAWN = "WITHDRAWN"
APPLIED = "APPLIED"
PENDING = "PENDING"

CORRECTION_HOLD = "CORRECTION_PENDING"
RECHECK_HOLD = "RECHECK_REQUIRED"
SOURCE_HOLD = "SOURCE_UPDATED"


class DispatchError(ValueError):
    """稳定错误码的下发领域异常。"""

    def __init__(self, code: str, detail: Any = None, status: int = 400):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status = status


# 一些测试/调用方可能使用更贴近英文语义的异常名。
DispatchRuleError = DispatchError
EligibilityError = DispatchError


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def group_key(member_id: Any, service_date: Any) -> str:
    if hasattr(service_date, "isoformat"):
        service_date = service_date.isoformat()
    return f"{member_id}|{service_date}"


def first_key(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _deep(value: Any) -> Any:
    return copy.deepcopy(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _normalize_group_ref(ref: Any) -> dict[str, Any]:
    if isinstance(ref, dict):
        gid = first_key(ref, "group_id", "id", "key", "gid")
        member = first_key(ref, "member_id", "member", "memberId")
        day = first_key(ref, "service_date", "date", "serviceDate", "day")
        if not gid and member and day:
            gid = group_key(member, day)
        return {"group_id": str(gid) if gid else None, "member_id": member, "service_date": day}
    text = str(ref)
    if "|" in text:
        member, day = text.split("|", 1)
        return {"group_id": text, "member_id": member, "service_date": day}
    return {"group_id": text, "member_id": None, "service_date": None}


def new_board(board_id: str = "default") -> dict[str, Any]:
    return {
        "board_id": board_id,
        "id": board_id,
        "revision": 0,
        "groups": {},
        "claims": {},
        "corrections": {},
        "source_updates": {},
        "batches": {},
        "certificates": [],
        "credentials": [],
        "events": [],
    }


# 常见别名。
new_dispatch_board = new_board
new_ledger = new_board
new_dispatch_ledger = new_board


class DispatchLedger:
    """面向对象入口；内部仍使用可 JSON 持久化的 dict 台账。"""

    def __init__(self, board: dict[str, Any] | str | None = None, board_id: str = "default", store: Any = None):
        self.store = store
        if isinstance(board, (str,)):
            from app.dispatch.store import DispatchStore
            self.store = DispatchStore(board)
            self.board_id = board_id
            self.board = self.store.load(board_id)
        elif board is not None:
            self.board = board
            self.board_id = board.get("board_id", board_id) if isinstance(board, dict) else board_id
        else:
            self.board = new_board(board_id)
            self.board_id = board_id

    def _save(self):
        if self.store is not None:
            self.store.save(self.board)

    @classmethod
    def load(cls, state: dict[str, Any]) -> "DispatchLedger":
        return cls(_deep(state))

    def issue(self, *args: Any, **kwargs: Any):
        result = issue_batch(self.board, *args, **kwargs)
        self._save()
        return result

    issue_batch = issue

    def source_update(self, *args: Any, **kwargs: Any):
        result = apply_source_update(self.board, *args, **kwargs)
        self._save()
        return result

    update_source = source_update
    mark_source_updated = source_update

    def apply_correction(self, data: dict[str, Any] | None = None, **kwargs: Any):
        payload = dict(data or {}) if not isinstance(data, str) else data
        result = create_correction(self.board, payload, **kwargs)
        self._save()
        return result

    create_correction = apply_correction
    submit_correction = apply_correction
    correct = apply_correction
    amend = apply_correction

    def withdraw_correction(self, correction_id: str, **kwargs: Any):
        result = withdraw_correction(self.board, correction_id, **kwargs)
        self._save()
        return result

    withdraw = withdraw_correction
    cancel = withdraw_correction
    revoke = withdraw_correction

    def recheck_group(self, group_id: str, **kwargs: Any):
        result = recheck_group(self.board, group_id, **kwargs)
        self._save()
        return result

    def dispatch(self, group_id: str, **kwargs: Any):
        result = dispatch_group(self.board, group_id, **kwargs)
        self._save()
        return result

    def view(self, **kwargs: Any):
        return board_view(self.board, **kwargs)

    def group_view(self, group_id: str):
        return get_group_view(self.board, group_id)


def _event(board: dict[str, Any], event_type: str, **payload: Any) -> dict[str, Any]:
    event = {"type": event_type, "at": now_iso(), **payload}
    board["events"].append(event)
    board["revision"] += 1
    return event


def ensure_group(
    board: dict[str, Any], group_id: str, member_id: Any = None, service_date: Any = None,
    auto_created: bool = False,
) -> dict[str, Any]:
    group_id = str(group_id)
    group = board["groups"].get(group_id)
    if group is None:
        ref = _normalize_group_ref(group_id)
        group = {
            "group_id": group_id,
            "id": group_id,
            "key": group_id,
            "member_id": member_id if member_id is not None else ref["member_id"],
            "service_date": service_date if service_date is not None else ref["service_date"],
            "claim_ids": [],
            "holds": {},
            "pending_correction_ids": [],
            "issuance": None,
            "original_issuance": None,
            "credential": None,
            "original_credential": None,
            "credential_ids": [],
            "dispatches": [],
            "dispatch_count": 0,
            "auto_created": auto_created,
            "created_at": now_iso(),
        }
        board["groups"][group_id] = group
    else:
        if member_id and not group.get("member_id"):
            group["member_id"] = member_id
        if service_date and not group.get("service_date"):
            group["service_date"] = service_date
    return group


def _claim_group_id(claim: dict[str, Any]) -> str:
    return group_key(claim["member_id"], claim["service_date"])


def upsert_claim(board: dict[str, Any], claim: dict[str, Any]) -> dict[str, Any]:
    claim_id = str(first_key(claim, "claim_id", "id", "ticket_id"))
    if not claim_id or claim_id == "None":
        raise DispatchError("CLAIM_ID_REQUIRED", status=400)
    member_id = first_key(claim, "member_id", "member_id", "member", "memberId")
    service_date = first_key(claim, "service_date", "serviceDate", "date", "day")
    if not member_id or not service_date:
        raise DispatchError("CLAIM_GROUP_FIELDS_REQUIRED", claim_id, 400)
    stored = _deep(claim)
    stored.update({"claim_id": claim_id, "member_id": str(member_id), "service_date": str(service_date)})
    board["claims"][claim_id] = stored
    gid = _claim_group_id(stored)
    group = ensure_group(board, gid, stored["member_id"], stored["service_date"])
    if claim_id not in group["claim_ids"]:
        group["claim_ids"].append(claim_id)
    return stored


def _claims_for_group(board: dict[str, Any], group: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for cid in group.get("claim_ids", []):
        claim = board["claims"].get(cid)
        if claim:
            rows.append(_deep(claim))
    if not rows and isinstance(group.get("issuance"), dict):
        rows = _as_list(group["issuance"].get("claims"))
    return rows


def _issuance_snapshot(board: dict[str, Any], group: dict[str, Any], extra: dict | None = None) -> dict[str, Any]:
    claims = sorted(_claims_for_group(board, group), key=lambda c: str(c.get("claim_id", "")))
    snapshot = {
        "group_id": group["group_id"],
        "member_id": group.get("member_id"),
        "service_date": group.get("service_date"),
        "claims": claims,
        "claim_ids": [c.get("claim_id") for c in claims],
    }
    if extra:
        snapshot.update(_deep(extra))
    return snapshot


def make_credential(issuance: dict[str, Any], batch_id: str | None = None,
                    issued_at: str | None = None, schema: str = "dispatch-credential/v1") -> dict[str, Any]:
    payload = {
        "schema": schema,
        "schema_version": "v1",
        "batch_id": batch_id,
        "group_id": issuance.get("group_id"),
        "issuance": _deep(issuance),
        "issued_at": issued_at or now_iso(),
    }
    digest = stable_digest(payload)
    payload["credential_id"] = f"DC-{digest[:16]}"
    payload["credential_sha256"] = digest
    payload["sha256"] = digest
    payload["digest"] = digest
    payload["archived"] = True
    payload["immutable"] = True
    return payload


def issue_batch(
    board: dict[str, Any],
    batch_id: str | None = None,
    claims: Iterable[dict[str, Any]] | None = None,
    groups: Iterable[Any] | None = None,
    issued_at: str | None = None,
    credentials: bool = True,
    credential: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """登记并签发一批理赔组。重复 batch_id+group 不会生成重复凭证。"""
    batch_id = str(batch_id or kwargs.get("id") or f"B{board['revision'] + 1:04d}")
    for claim in claims or []:
        upsert_claim(board, claim)

    group_refs = list(groups or [])
    if not group_refs and claims:
        group_refs = sorted({_claim_group_id(c) for c in (claims or [])})
    if not group_refs and kwargs.get("group_id"):
        group_refs = [kwargs["group_id"]]

    issued_groups: list[dict[str, Any]] = []
    credential_ids: list[str] = []
    for ref in group_refs:
        info = _normalize_group_ref(ref)
        gid = info["group_id"]
        if not gid:
            continue
        group = ensure_group(board, gid, info.get("member_id"), info.get("service_date"))
        if isinstance(ref, dict):
            for claim in ref.get("claims", []) or []:
                upsert_claim(board, claim)
            if ref.get("issuance"):
                group.setdefault("issuance", _deep(ref["issuance"]))
        snapshot = _issuance_snapshot(board, group, {"batch_id": batch_id})
        if group.get("original_issuance") is None:
            group["original_issuance"] = _deep(snapshot)
        group["issuance"] = _deep(group["original_issuance"])
        group["issued"] = True
        group["issued_at"] = issued_at or now_iso()

        cert = None
        if credentials and credential:
            existing = next((c for c in group.get("credential_ids", [])
                             if board.get("certificate_by_id", {}).get(c, {}).get("batch_id") == batch_id), None)
            if existing:
                cert = _deep(board["certificate_by_id"][existing])
            else:
                cert = make_credential(group["original_issuance"], batch_id, issued_at)
                board.setdefault("certificate_by_id", {})[cert["credential_id"]] = cert
                board["certificates"].append(cert)
                board["credentials"].append(cert)
                group.setdefault("credential_ids", []).append(cert["credential_id"])
                credential_ids.append(cert["credential_id"])
            if group.get("original_credential") is None:
                group["original_credential"] = _deep(cert)
            group["credential"] = _deep(group["original_credential"])
        recompute_group(board, gid)
        issued_groups.append(get_group_view(board, gid))

    board.setdefault("batches", {})[batch_id] = {
        "batch_id": batch_id,
        "id": batch_id,
        "group_ids": [g["group_id"] for g in issued_groups],
        "credential_ids": credential_ids,
        "issued_at": issued_at or now_iso(),
    }
    _event(board, "BATCH_ISSUED", batch_id=batch_id, group_ids=[g["group_id"] for g in issued_groups])
    return {"batch_id": batch_id, "groups": issued_groups, "group_ids": [g["group_id"] for g in issued_groups]}


def _active_holds(group: dict[str, Any], board: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = []
    for hold in group.get("holds", {}).values():
        if not hold.get("active", False):
            continue
        cid = hold.get("correction_id")
        if board and cid:
            correction = board.get("corrections", {}).get(cid)
            if correction and correction.get("status") == WITHDRAWN:
                continue
        result.append(hold)
    return result


def recompute_group(board: dict[str, Any], group_id: str) -> dict[str, Any]:
    group = board["groups"].get(str(group_id))
    if group is None:
        raise DispatchError("GROUP_NOT_FOUND", group_id, 404)
    holds = _active_holds(group, board)
    codes = list(dict.fromkeys(h.get("code") or h.get("type") or "HELD" for h in holds))
    correction_ids = list(dict.fromkeys(h.get("correction_id") for h in holds if h.get("correction_id")))
    group["active_hold_ids"] = [h["id"] for h in holds]
    group["reason_codes"] = codes
    group["pending_correction_ids"] = correction_ids
    group["needs_recheck"] = any(code in {CORRECTION_HOLD, RECHECK_HOLD} for code in codes)
    eligible = not holds
    group["eligible"] = eligible
    group["can_dispatch"] = eligible
    group["dispatchable"] = eligible
    group["issuable"] = eligible
    group["eligible_for_dispatch"] = eligible
    group["ready_to_dispatch"] = eligible
    if not eligible:
        if CORRECTION_HOLD in codes or RECHECK_HOLD in codes:
            status = RECHECK_REQUIRED
        elif SOURCE_HOLD in codes:
            status = SOURCE_CHANGED
        else:
            status = HELD
        group["status"] = status
        group["state"] = HELD
        group["eligibility_status"] = "INELIGIBLE"
        group["dispatch_status"] = "HELD"
    else:
        group["status"] = READY
        group["state"] = READY
        group["eligibility_status"] = DISPATCHABLE
        group["dispatch_status"] = READY
    return group


def _extract_group_ids(data: dict[str, Any], *keys: str) -> list[str]:
    ids: list[str] = []
    for key in keys:
        value = data.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, dict) and not first_key(value, "group_id", "id", "key", "gid"):
            ids.extend(str(k) for k in value.keys())
            continue
        for item in _as_list(value):
            if isinstance(item, dict):
                info = _normalize_group_ref(item)
                if info["group_id"]:
                    ids.append(info["group_id"])
            else:
                ids.append(str(item))
    return list(dict.fromkeys(ids))


def _groups_from_change(change: dict[str, Any], board: dict[str, Any]) -> list[str]:
    ids = _extract_group_ids(change, "affected_group_id", "group_id", "id", "gid", "group_ids")
    ids += _extract_group_ids(change, "old_group_id", "from_group_id", "source_group_id", "previous_group_id")
    ids += _extract_group_ids(change, "new_group_id", "to_group_id", "target_group_id", "moved_to_group_id")
    claim_id = first_key(change, "claim_id", "ticket_id")
    if claim_id and claim_id in board.get("claims", {}):
        claim = board["claims"][claim_id]
        ids.append(_claim_group_id(claim))
    for prefix in ("old", "from", "previous"):
        member = first_key(change, f"{prefix}_member_id", f"{prefix}_member")
        day = first_key(change, f"{prefix}_service_date", f"{prefix}_date", f"{prefix}_day")
        if member and day:
            ids.append(group_key(member, day))
        nested = change.get(prefix)
        if isinstance(nested, dict):
            m = first_key(nested, "member_id", "member")
            d = first_key(nested, "service_date", "date", "day")
            if m and d:
                ids.append(group_key(m, d))
    for prefix in ("new", "to", "target"):
        member = first_key(change, f"{prefix}_member_id", f"{prefix}_member")
        day = first_key(change, f"{prefix}_service_date", f"{prefix}_date", f"{prefix}_day")
        if member and day:
            ids.append(group_key(member, day))
        nested = change.get(prefix)
        if isinstance(nested, dict):
            m = first_key(nested, "member_id", "member")
            d = first_key(nested, "service_date", "date", "day")
            if m and d:
                ids.append(group_key(m, d))
    return list(dict.fromkeys(ids))


def apply_source_update(
    board: dict[str, Any],
    data: dict[str, Any] | str | None = None,
    maybe_groups: Iterable[Any] | None = None,
    **kwargs: Any,
) -> tuple[dict[str, Any], bool]:
    """登记票据源数据更新并停用受影响组。

    返回 ``(source_update, changed)``；相同 update_id/幂等键重试时
    ``changed=False``，且不会重复加停发锁。
    """
    if isinstance(data, str):
        payload = dict(kwargs)
        payload["update_id"] = data
    else:
        payload = dict(data or {})
        payload.update(kwargs)
    update_id = str(first_key(payload, "update_id", "source_update_id", "id", "event_id",
                             "idempotency_key", "key", "request_key") or
                    "SRC-" + stable_digest(payload)[:16])
    existing = board.get("source_updates", {}).get(update_id)
    if existing:
        return _deep(existing), False

    group_ids = _extract_group_ids(
        payload, "affected_group_ids", "group_ids", "groups", "affected_groups", "target_groups",
        "group_id", "affected_group_id", "gid")
    if maybe_groups:
        group_ids.extend(str(g) for g in maybe_groups)
    raw_changes = first_key(payload, "changes", "updates", "diffs", "affected_rows")
    if isinstance(raw_changes, dict):
        changes = []
        for key, value in raw_changes.items():
            item = dict(value) if isinstance(value, dict) else {"value": value}
            if not first_key(item, "claim_id", "ticket_id", "id"):
                item["claim_id"] = key
            changes.append(item)
    else:
        changes = _as_list(raw_changes)
    for change in changes:
        if isinstance(change, dict):
            group_ids.extend(_groups_from_change(change, board))
    rows = first_key(payload, "rows", "tickets", "claims", "source_rows", "data")
    for row in _as_list(rows):
        if isinstance(row, dict):
            if first_key(row, "member_id", "member") and first_key(row, "service_date", "date"):
                group_ids.append(group_key(first_key(row, "member_id", "member"),
                                           first_key(row, "service_date", "date")))
            cid = first_key(row, "claim_id", "ticket_id")
            if cid and cid in board.get("claims", {}):
                group_ids.append(_claim_group_id(board["claims"][cid]))
            # 同时把新行落账，便于后续补正移动。
            if first_key(row, "claim_id", "ticket_id"):
                try:
                    upsert_claim(board, row)
                    group_ids.append(_claim_group_id(row))
                except DispatchError:
                    pass
    group_ids = list(dict.fromkeys(str(g) for g in group_ids if g))
    if not group_ids:
        raise DispatchError("AFFECTED_GROUPS_REQUIRED", status=400)

    affected = []
    for gid in group_ids:
        group = ensure_group(board, gid, auto_created=True)
        hold_id = f"source:{update_id}:{gid}"
        group.setdefault("holds", {})[hold_id] = {
            "id": hold_id,
            "type": SOURCE_HOLD,
            "code": first_key(payload, "reason_code", "code", default=SOURCE_HOLD),
            "reason": first_key(payload, "reason", "message",
                                default="票据源数据已更新，下发资格失效，需重新核对"),
            "reference_type": "source_update",
            "reference_id": update_id,
            "source_update_id": update_id,
            "correction_id": first_key(payload, "correction_id", "amendment_id"),
            "active": True,
            "created_at": now_iso(),
        }
        recompute_group(board, gid)
        affected.append(gid)

    record = {
        "update_id": update_id,
        "id": update_id,
        "type": "SOURCE_UPDATED",
        "status": "APPLIED",
        "affected_group_ids": affected,
        "groups": affected,
        "idempotent_hit": False,
        "changed": True,
        "at": now_iso(),
        "payload_fingerprint": stable_digest(payload),
    }
    board.setdefault("source_updates", {})[update_id] = record
    _event(board, "SOURCE_UPDATED", update_id=update_id, affected_group_ids=affected)
    board["revision"] += 0  # event already bumped
    return _deep(record), True


# 常见别名。
update_source = apply_source_update
mark_source_updated = apply_source_update
invalidate_groups = apply_source_update


def withdraw_source_update(board: dict[str, Any], update_id: str, **kwargs: Any) -> dict[str, Any]:
    """撤回一次源数据更新拦截；不删除审计记录。"""
    uid = str(update_id)
    record = board.get("source_updates", {}).get(uid)
    if record is None:
        raise DispatchError("SOURCE_UPDATE_NOT_FOUND", uid, 404)
    for gid in record.get("affected_group_ids", []):
        group = board.get("groups", {}).get(gid)
        if not group:
            continue
        for hold in group.get("holds", {}).values():
            if hold.get("source_update_id") == uid:
                hold["active"] = False
                hold["withdrawn"] = True
        recompute_group(board, gid)
    record["status"] = WITHDRAWN
    record["withdrawn"] = True
    _event(board, "SOURCE_UPDATE_WITHDRAWN", update_id=uid)
    return _deep(record)


def _correction_fingerprint(payload: dict[str, Any]) -> str:
    ignored = {"correction_id", "id", "idempotency_key", "key", "request_key", "expected_revision"}
    core = {k: v for k, v in payload.items() if k not in ignored}
    return stable_digest(core)


def _correction_locations(board: dict[str, Any], payload: dict[str, Any]):
    claim_id = first_key(payload, "claim_id", "ticket_id", "target_id", "record_id")
    claim = board.get("claims", {}).get(str(claim_id)) if claim_id else None

    from_gid = first_key(payload, "from_group_id", "old_group_id", "source_group_id",
                         "previous_group_id", "original_group_id", "from_group", "old_group",
                         "source_group", "previous_group")
    to_gid = first_key(payload, "to_group_id", "new_group_id", "target_group_id",
                       "moved_to_group_id", "destination_group_id", "to_group", "new_group",
                       "target_group", "destination_group")
    if not from_gid and isinstance(payload.get("from"), str):
        from_gid = payload["from"]
    if not to_gid and isinstance(payload.get("to"), str):
        to_gid = payload["to"]
    only_gid = first_key(payload, "group_id", "affected_group_id", "target_group", "group")

    old_member = first_key(payload, "old_member_id", "from_member_id", "previous_member_id",
                           "original_member_id", "old_member")
    old_day = first_key(payload, "old_service_date", "from_service_date", "previous_service_date",
                        "original_service_date", "old_date", "old_service_day")
    new_member = first_key(payload, "new_member_id", "to_member_id", "corrected_member_id",
                           "target_member_id", "updated_member_id", "member_id", "memberId", "member")
    new_day = first_key(payload, "new_service_date", "to_service_date", "corrected_service_date",
                        "target_service_date", "updated_service_date", "service_date",
                        "serviceDate", "date", "day")

    old_nested = payload.get("old") or payload.get("before") or payload.get("from")
    new_nested = (payload.get("new") or payload.get("after") or payload.get("to")
                  or payload.get("corrected") or payload.get("correction") or {})
    if isinstance(old_nested, dict):
        old_member = old_member or first_key(old_nested, "member_id", "member")
        old_day = old_day or first_key(old_nested, "service_date", "date", "day")
    if isinstance(new_nested, dict):
        new_member = new_member or first_key(new_nested, "member_id", "member")
        new_day = new_day or first_key(new_nested, "service_date", "date", "day")

    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
    if changes:
        new_member = first_key(changes, "member_id", "member") or new_member
        new_day = first_key(changes, "service_date", "date", "day") or new_day

    before = _deep(claim) if claim else None
    if claim:
        from_gid = from_gid or _claim_group_id(claim)
        old_member = old_member or claim.get("member_id")
        old_day = old_day or claim.get("service_date")
        new_member = new_member if new_member not in (None, "") else claim.get("member_id")
        new_day = new_day if new_day not in (None, "") else claim.get("service_date")
    if old_member and old_day and not from_gid:
        from_gid = group_key(old_member, old_day)
    if new_member and new_day:
        to_gid = to_gid or group_key(new_member, new_day)
    if not from_gid and only_gid and not to_gid:
        from_gid = str(only_gid)
    if not from_gid and to_gid:
        from_gid = str(to_gid)
    if not to_gid and from_gid:
        # 没改成员/服务日时，移入组就是原组。
        to_gid = str(from_gid)
    if not from_gid and only_gid:
        from_gid = to_gid = str(only_gid)

    moved = bool(from_gid and to_gid and str(from_gid) != str(to_gid))
    key_changed = moved or (bool(old_member and new_member and str(old_member) != str(new_member)) or
                            bool(old_day and new_day and str(old_day) != str(new_day)))
    return {
        "claim_id": str(claim_id) if claim_id else None,
        "claim": claim,
        "before": before,
        "from_group_id": str(from_gid) if from_gid else None,
        "to_group_id": str(to_gid) if to_gid else None,
        "old_member": str(old_member) if old_member else None,
        "old_day": str(old_day) if old_day else None,
        "new_member": str(new_member) if new_member else None,
        "new_day": str(new_day) if new_day else None,
        "moved": moved,
        "key_changed": key_changed,
        "changes": changes,
        "new_nested": new_nested if isinstance(new_nested, dict) else {},
    }


def create_correction(board: dict[str, Any], data: dict[str, Any] | str | None = None,
                      maybe_data: dict[str, Any] | None = None, **kwargs: Any) -> tuple[dict[str, Any], bool]:
    """应用/登记一份补正并冻结受影响组的下发资格。

    相同 ``correction_id`` 或相同幂等载荷重试不会重复生效；已撤回的补正会继续
    返回 WITHDRAWN，不会再次给理赔组加停发锁。
    """
    id_override = None
    if isinstance(data, str):
        id_override = data
        payload = dict(maybe_data or {})
    else:
        payload = dict(data or {})
        if maybe_data and isinstance(maybe_data, dict):
            payload.update(maybe_data)
    payload.update(kwargs)
    if id_override:
        payload.setdefault("correction_id", id_override)
    cid = str(first_key(payload, "correction_id", "id", "amendment_id", "revision_id",
                        "idempotency_key", "key", "request_key") or
              "COR-" + stable_digest(payload)[:16])
    fingerprint = _correction_fingerprint(payload)
    existing = board.get("corrections", {}).get(cid)
    if existing:
        # 已撤回是终态：任何同号重试都只能回放 WITHDRAWN，不能再次停发。
        if existing.get("status") == WITHDRAWN:
            view = _deep(existing)
            view["idempotent_hit"] = True
            view["changed"] = False
            return view, False
        if existing.get("payload_fingerprint") not in (None, fingerprint) and payload.get("strict", True):
            raise DispatchError("IDEMPOTENCY_CONFLICT", cid, 409)
        view = _deep(existing)
        view["idempotent_hit"] = True
        view["changed"] = False
        return view, False

    loc = _correction_locations(board, payload)
    if not loc["from_group_id"]:
        raise DispatchError("CORRECTION_GROUP_REQUIRED", status=400)
    from_gid, to_gid = loc["from_group_id"], loc["to_group_id"] or loc["from_group_id"]
    affected = list(dict.fromkeys([from_gid, to_gid]))

    before = loc["before"]
    after = _deep(before) if before else {}
    if loc["claim_id"]:
        after["claim_id"] = loc["claim_id"]
    if loc["new_member"]:
        after["member_id"] = loc["new_member"]
    if loc["new_day"]:
        after["service_date"] = loc["new_day"]
    for key, value in loc["changes"].items():
        if key in {"member_id", "service_date"} or key in board.get("claims", {}).get(loc["claim_id"], {}):
            after[key] = value
    for key, value in loc["new_nested"].items():
        if key not in {"member", "date", "day"}:
            after[key] = value
    if first_key(payload, "billed_cents", "approved_cents", "deductible_cents"):
        for key in ("billed_cents", "approved_cents", "deductible_cents"):
            if payload.get(key) not in (None, ""):
                after[key] = payload[key]

    from_group = ensure_group(board, from_gid, loc["old_member"], loc["old_day"])
    to_is_new = to_gid not in board["groups"]
    to_group = ensure_group(board, to_gid, loc["new_member"] or loc["old_member"],
                            loc["new_day"] or loc["old_day"], auto_created=to_is_new)

    # 先移动/更新当前理赔；原始签发快照保持不变。
    if loc["claim_id"]:
        cid_claim = loc["claim_id"]
        if before is None:
            if not after.get("member_id") or not after.get("service_date"):
                raise DispatchError("CORRECTION_CLAIM_NOT_FOUND", cid_claim, 404)
            board["claims"][cid_claim] = after
        else:
            board["claims"][cid_claim] = after
        if cid_claim in from_group["claim_ids"] and to_gid != from_gid:
            from_group["claim_ids"].remove(cid_claim)
        if cid_claim not in to_group["claim_ids"]:
            to_group["claim_ids"].append(cid_claim)

    hold_ids = []
    for gid in affected:
        group = board["groups"][gid]
        hold_id = f"correction:{cid}:{gid}"
        hold_ids.append(hold_id)
        group.setdefault("holds", {})[hold_id] = {
            "id": hold_id,
            "type": CORRECTION_HOLD,
            "code": RECHECK_HOLD if (loc["key_changed"] or gid in {from_gid, to_gid}) else CORRECTION_HOLD,
            "reason": first_key(payload, "reason", "message",
                                default="补正待核对，下发资格暂停；成员或服务日变更时移出/移入组均需重核"),
            "correction_id": cid,
            "reference_type": "correction",
            "reference_id": cid,
            "affected_side": "FROM" if gid == from_gid and gid != to_gid else
            ("TO" if gid == to_gid and gid != from_gid else "SAME"),
            "active": True,
            "rechecked": False,
            "created_at": now_iso(),
        }
        if cid not in group["pending_correction_ids"]:
            group["pending_correction_ids"].append(cid)
        # 当前内容随补正变化，但 original_issuance 绝不动。
        group["issuance"] = _issuance_snapshot(board, group, {"correction_id": cid})
        recompute_group(board, gid)

    record = {
        "correction_id": cid,
        "id": cid,
        "key": cid,
        "type": "CORRECTION",
        "status": APPLIED,
        "state": PENDING,
        "review_status": PENDING,
        "active": True,
        "effective": True,
        "applied": True,
        "withdrawn": False,
        "pending_review": True,
        "needs_recheck": True,
        "key_fields_changed": bool(loc["key_changed"]),
        "moved": bool(loc["moved"]),
        "claim_id": loc["claim_id"],
        "before": before,
        "after": after,
        "from_group_id": from_gid,
        "old_group_id": from_gid,
        "to_group_id": to_gid,
        "new_group_id": to_gid,
        "affected_group_ids": affected,
        "groups": affected,
        "hold_ids": hold_ids,
        "payload_fingerprint": fingerprint,
        "idempotent_hit": False,
        "changed": True,
        "created_at": now_iso(),
    }
    board.setdefault("corrections", {})[cid] = record
    _event(board, "CORRECTION_APPLIED", correction_id=cid, affected_group_ids=affected)
    return _deep(record), True


apply_correction = create_correction
submit_correction = create_correction


def withdraw_correction(board: dict[str, Any], correction_id: str,
                        **kwargs: Any) -> tuple[dict[str, Any], bool]:
    cid = str(correction_id)
    record = board.get("corrections", {}).get(cid)
    if record is None:
        raise DispatchError("CORRECTION_NOT_FOUND", cid, 404)
    if record.get("status") == WITHDRAWN:
        view = _deep(record)
        view["idempotent_hit"] = True
        view["changed"] = False
        return view, False

    affected = list(record.get("affected_group_ids", []))
    # 回滚理赔字段和组归属。
    claim_id = record.get("claim_id")
    before = record.get("before")
    from_gid = record.get("from_group_id")
    to_gid = record.get("to_group_id")
    if claim_id and before:
        board["claims"][claim_id] = _deep(before)
        if to_gid and to_gid in board["groups"] and claim_id in board["groups"][to_gid].get("claim_ids", []):
            if to_gid != from_gid:
                board["groups"][to_gid]["claim_ids"].remove(claim_id)
        if from_gid and from_gid in board["groups"] and claim_id not in board["groups"][from_gid]["claim_ids"]:
            board["groups"][from_gid]["claim_ids"].append(claim_id)

    for gid in affected:
        group = board["groups"].get(gid)
        if group is None:
            continue
        for hold in group.get("holds", {}).values():
            if hold.get("correction_id") == cid:
                hold["active"] = False
                hold["withdrawn"] = True
                hold["released_at"] = now_iso()
        group["pending_correction_ids"] = [x for x in group.get("pending_correction_ids", []) if x != cid]
        if group.get("original_issuance") is not None:
            group["issuance"] = _deep(group["original_issuance"])
        recompute_group(board, gid)

    # 仅清理补正时自动创建、且已无理赔/凭证/其他补正的移入空组。
    if to_gid and to_gid != from_gid and to_gid in board["groups"]:
        target = board["groups"][to_gid]
        has_other_correction = any(
            h.get("active") for h in target.get("holds", {}).values() if h.get("correction_id") != cid)
        if (target.get("auto_created") and not target.get("claim_ids") and
                not target.get("credential_ids") and not has_other_correction):
            del board["groups"][to_gid]

    record.update({
        "status": WITHDRAWN,
        "state": WITHDRAWN,
        "review_status": WITHDRAWN,
        "active": False,
        "effective": False,
        "applied": False,
        "withdrawn": True,
        "pending_review": False,
        "needs_recheck": False,
        "withdrawn_at": now_iso(),
        "idempotent_hit": False,
        "changed": True,
    })
    _event(board, "CORRECTION_WITHDRAWN", correction_id=cid, affected_group_ids=affected)
    return _deep(record), True


cancel_correction = withdraw_correction
revoke_correction = withdraw_correction


def recheck_group(board: dict[str, Any], group_id: str, ok: bool = True,
                  correction_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
    gid = str(group_id)
    group = board["groups"].get(gid)
    if group is None:
        raise DispatchError("GROUP_NOT_FOUND", gid, 404)
    released = []
    for hold in group.get("holds", {}).values():
        if not hold.get("active"):
            continue
        if correction_id and hold.get("correction_id") != correction_id:
            continue
        hold["rechecked"] = True
        hold["recheck_ok"] = bool(ok)
        hold["rechecked_at"] = now_iso()
        if ok:
            hold["active"] = False
            hold["released_by"] = "RECHECK_PASSED"
            released.append(hold["id"])
    if correction_id:
        correction = board.get("corrections", {}).get(correction_id)
        if correction:
            checked_groups = set(correction.get("rechecked_group_ids", []))
            if ok:
                checked_groups.add(gid)
            correction["rechecked_group_ids"] = sorted(checked_groups)
            if set(correction.get("affected_group_ids", [])) <= checked_groups:
                correction["review_status"] = "RECHECKED"
                correction["state"] = "RECHECKED"
                correction["pending_review"] = False
    recompute_group(board, gid)
    _event(board, "GROUP_RECHECKED", group_id=gid, ok=ok, released=released)
    return get_group_view(board, gid)


def dispatch_group(board: dict[str, Any], group_id: str, dispatch_id: str | None = None,
                   idempotency_key: str | None = None, **kwargs: Any) -> dict[str, Any]:
    gid = str(group_id)
    recompute_group(board, gid)
    group = board["groups"].get(gid)
    if group is None:
        raise DispatchError("GROUP_NOT_FOUND", gid, 404)
    if not group["can_dispatch"]:
        raise DispatchError("DISPATCH_NOT_ALLOWED", group.get("reason_codes"), 409)
    key = str(dispatch_id or idempotency_key or f"DISPATCH-{gid}-{board['revision']}")
    existing = next((d for d in group.get("dispatches", []) if d.get("dispatch_id") == key), None)
    if existing:
        view = get_group_view(board, gid)
        view["dispatch"] = _deep(existing)
        view["idempotent_hit"] = True
        return view
    record = {"dispatch_id": key, "id": key, "group_id": gid, "at": now_iso(), "status": "DISPATCHED"}
    group.setdefault("dispatches", []).append(record)
    group["dispatch_count"] = len(group["dispatches"])
    group["last_dispatch"] = _deep(record)
    _event(board, "GROUP_DISPATCHED", group_id=gid, dispatch_id=key)
    view = get_group_view(board, gid)
    view["dispatch"] = record
    view["idempotent_hit"] = False
    return view


def get_group_view(board: dict[str, Any], group_id: str) -> dict[str, Any]:
    gid = str(group_id)
    if gid not in board["groups"]:
        raise DispatchError("GROUP_NOT_FOUND", gid, 404)
    recompute_group(board, gid)
    group = _deep(board["groups"][gid])
    # 保留直观别名。
    group["id"] = gid
    group["key"] = gid
    group["can_dispatch"] = group.get("can_dispatch", False)
    group["dispatchable"] = group["can_dispatch"]
    group["eligible"] = group["can_dispatch"]
    group["eligible_for_dispatch"] = group["can_dispatch"]
    group["issuable"] = group["can_dispatch"]
    group["pending_corrections"] = [
        _deep(board["corrections"][cid]) for cid in group.get("pending_correction_ids", [])
        if cid in board.get("corrections", {})
    ]
    group["active_holds"] = [h for h in group.get("holds", {}).values() if h.get("active")]
    group["blockers"] = group["active_holds"]
    group["credential"] = group.get("original_credential") or group.get("credential")
    group["certificate"] = group.get("credential")
    group["voucher"] = group.get("credential")
    group["archived_credentials"] = [
        _deep(board["certificate_by_id"][cid]) for cid in group.get("credential_ids", [])
        if cid in board.get("certificate_by_id", {})
    ]
    return group


def board_view(board: dict[str, Any], include_events: bool = True, **kwargs: Any) -> dict[str, Any]:
    groups = [get_group_view(board, gid) for gid in sorted(board.get("groups", {}))]
    dispatchable = [g["group_id"] for g in groups if g["can_dispatch"]]
    held = [g["group_id"] for g in groups if not g["can_dispatch"]]
    recheck = [g["group_id"] for g in groups if g.get("needs_recheck")]
    view = {
        "board_id": board.get("board_id", "default"),
        "id": board.get("board_id", "default"),
        "revision": board.get("revision", 0),
        "groups": groups,
        "groups_by_id": {g["group_id"]: g for g in groups},
        "group_map": {g["group_id"]: g for g in groups},
        "dispatchable_group_ids": dispatchable,
        "can_dispatch_group_ids": dispatchable,
        "eligible_group_ids": dispatchable,
        "held_group_ids": held,
        "needs_recheck_group_ids": recheck,
        "dispatchable_groups": groups,  # 每个元素含 can_dispatch；调用方可自行过滤。
        "summary": {
            "total": len(groups),
            "groups": len(groups),
            "dispatchable": len(dispatchable),
            "can_dispatch": len(dispatchable),
            "held": len(held),
            "blocked": len(held),
            "needs_recheck": len(recheck),
        },
        "certificates": _deep(board.get("certificates", [])),
        "credentials": _deep(board.get("credentials", [])),
        "corrections": [_deep(c) for c in board.get("corrections", {}).values()],
        "source_updates": [_deep(u) for u in board.get("source_updates", {}).values()],
    }
    if include_events:
        view["events"] = _deep(board.get("events", []))
    return view


def can_dispatch(group_or_board: Any, group_id: str | None = None) -> bool:
    """灵活的布尔判断：可传 group 视图，也可传 board + group_id。"""
    if group_id is not None and isinstance(group_or_board, dict) and "groups" in group_or_board:
        return bool(get_group_view(group_or_board, group_id)["can_dispatch"])
    if isinstance(group_or_board, dict):
        if "can_dispatch" in group_or_board:
            return bool(group_or_board["can_dispatch"])
        return not _active_holds(group_or_board)
    return bool(group_or_board)


def has_pending_corrections(group: dict[str, Any]) -> bool:
    return bool(group.get("pending_correction_ids") or group.get("pending_corrections"))


def list_dispatchable_groups(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in board_view(board, include_events=False)["groups"] if g["can_dispatch"]]


def list_held_groups(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in board_view(board, include_events=False)["groups"] if not g["can_dispatch"]]


def archive_credential(board: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
    """把凭证放入只增档案；调用方不应随后修改返回对象。"""
    cert = _deep(credential)
    board.setdefault("certificates", []).append(cert)
    board.setdefault("credentials", []).append(cert)
    if cert.get("credential_id"):
        board.setdefault("certificate_by_id", {})[cert["credential_id"]] = cert
    return _deep(cert)


def restore_issuance(board: dict[str, Any], group_id: str) -> dict[str, Any] | None:
    """把当前签发内容恢复为首次签发时的冻结快照。"""
    group = board["groups"].get(str(group_id))
    if group is None:
        raise DispatchError("GROUP_NOT_FOUND", group_id, 404)
    if group.get("original_issuance") is not None:
        group["issuance"] = _deep(group["original_issuance"])
    recompute_group(board, group_id)
    return group["issuance"]


class DispatchService(DispatchLedger):
    """常见服务命名别名。"""


class DispatchBoard(DispatchLedger):
    """常见工作台命名别名。"""


class EligibilityBoard(DispatchLedger):
    """资格台账命名别名。"""


def build_dispatch_board(groups=None, claims=None, board_id: str = "default", issue: bool = True):
    """测试/调用方常用的一站式构造入口。"""
    board = new_board(board_id)
    if claims or groups:
        issue_batch(board, batch_id="INITIAL", claims=claims, groups=groups)
    return board


build_board = build_dispatch_board
create_dispatch_board = build_dispatch_board


def register_source_update(board, *args, **kwargs):
    record, _changed = apply_source_update(board, *args, **kwargs)
    return record


def correct_claim(board, data=None, **kwargs):
    record, _changed = create_correction(board, data, **kwargs)
    return record


def withdraw_amendment(board, correction_id, **kwargs):
    record, _changed = withdraw_correction(board, correction_id, **kwargs)
    return record


def list_groups(board):
    return board_view(board, include_events=False)["groups"]


# 让 OO 入口也具备 list_groups。
def _ledger_list_groups(self):
    return list_groups(self.board)


DispatchLedger.list_groups = _ledger_list_groups

try:  # 便捷导出；store 依赖本模块的 new_board，放在文件末尾避免循环导入。
    from app.dispatch.store import DispatchStore  # noqa: E402,F401
except Exception:  # pragma: no cover
    pass
