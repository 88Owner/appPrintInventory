from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .config import AppConfig, AuthStrategy


class SapoApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReceiveInventoryItem:
    sku: str
    name: str
    quantity: int


@dataclass(frozen=True)
class ReceiveInventorySummary:
    """Một dòng trong danh sách GET /admin/receive_inventories.json"""

    id: int
    code: str
    receipt_status: str
    created_on: str


def _extract_receive_inventory_id(payload: Any) -> int | None:
    if isinstance(payload, dict):
        ri = payload.get("receive_inventory")
        if isinstance(ri, dict):
            v = ri.get("id")
            try:
                return int(v)
            except Exception:
                return None
        v = payload.get("id")
        try:
            return int(v)
        except Exception:
            return None
    return None


def _extract_receive_inventory_code(payload: Any) -> str | None:
    if isinstance(payload, dict):
        ri = payload.get("receive_inventory")
        if isinstance(ri, dict):
            v = ri.get("code")
            if isinstance(v, str) and v.strip():
                return v.strip()
        v = payload.get("code")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _as_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _coerce_item(obj: dict[str, Any]) -> ReceiveInventoryItem | None:
    variant = obj.get("variant") if isinstance(obj.get("variant"), dict) else {}
    product = obj.get("product") if isinstance(obj.get("product"), dict) else {}

    sku = (
        (obj.get("sku") or obj.get("SKU") or variant.get("sku") or product.get("sku") or "")
    )
    sku = str(sku).strip()

    name = (
        obj.get("name")
        or obj.get("product_name")
        or obj.get("title")
        or obj.get("variant_title")
        or variant.get("title")
        or product.get("name")
        or product.get("title")
    )
    name = str(name).strip()

    # Some payloads split title across product + variant
    if product and isinstance(product.get("name"), str) and isinstance(variant.get("title"), str):
        pn = str(product.get("name")).strip()
        vt = str(variant.get("title")).strip()
        if pn and vt and vt.lower() not in pn.lower():
            name = f"{pn} {vt}".strip()

    qty_raw = (
        obj.get("quantity")
        or obj.get("qty")
        or obj.get("line_quantity")
        or obj.get("received_quantity")
        or obj.get("accept_quantity")
        or obj.get("amount")
    )
    quantity = _as_int(qty_raw) or 0

    if not sku and not name:
        return None
    return ReceiveInventoryItem(sku=sku, name=name, quantity=quantity)


def _extract_items(payload: Any) -> list[ReceiveInventoryItem]:
    # Heuristic parsing because exact shape may differ by Sapo version.
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in (
            "items",
            "line_items",
            "products",
            "receive_inventory_items",
            "receive_inventory_lines",
            "receive_inventory_line_items",
            "receive_inventory_details",
            "receive_lines",
            "details",
        ):
            if key in payload:
                candidates.append(payload[key])
        # sometimes nested
        for key in ("receive_inventory", "data"):
            if isinstance(payload.get(key), dict):
                candidates.append(payload[key])
    candidates.append(payload)

    for c in candidates:
        if isinstance(c, list):
            items: list[ReceiveInventoryItem] = []
            for it in c:
                if isinstance(it, dict):
                    coerced = _coerce_item(it)
                    if coerced:
                        items.append(coerced)
            if items:
                return items
        if isinstance(c, dict):
            # maybe { receive_inventory: { items: [...] } }
            for v in c.values():
                if isinstance(v, list):
                    items: list[ReceiveInventoryItem] = []
                    for it in v:
                        if isinstance(it, dict):
                            coerced = _coerce_item(it)
                            if coerced:
                                items.append(coerced)
                    if items:
                        return items
    return []


class SapoClient:
    def __init__(self, config: AppConfig):
        self._cfg = config
        self._session = requests.Session()

    def _get(self, path: str, *, strategy: AuthStrategy | None = None, auth: tuple[str, str] | None = None, params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self._cfg.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "appPrintInv/1.0",
            **((strategy.headers or {}) if strategy else {}),
        }
        return self._session.get(url, headers=headers, auth=auth, params=params, timeout=self._cfg.timeout_seconds)

    def _attempt_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[requests.Response, str]:
        """
        Try BasicAuth first (because Postman screenshot uses it),
        then fall back to header strategies.
        """
        last_exc: Exception | None = None

        if self._cfg.token_primary and self._cfg.token_secondary:
            try:
                resp = self._get(path, auth=(self._cfg.token_primary, self._cfg.token_secondary), params=params)
                return resp, "basic-auth"
            except Exception as e:
                last_exc = e

        for s in self._cfg.auth_strategies:
            try:
                resp = self._get(path, strategy=s, params=params)
                return resp, s.name
            except Exception as e:
                last_exc = e

        raise SapoApiError(f"Không gọi được API (lỗi kết nối). Lỗi cuối: {last_exc}")

    def _resolve_id_by_code(self, code: str) -> tuple[int | None, str]:
        """
        If user inputs receive_inventory code (e.g. REI00497), try to resolve to numeric id.
        Endpoint guess: GET /admin/receive_inventories.json?query=<code>
        """
        code = code.strip()
        if not code:
            return None, ""

        # Common list endpoint patterns in admin JSON APIs; try a few lightweight ones.
        candidates: list[tuple[str, dict[str, Any]]] = [
            ("/admin/receive_inventories.json", {"query": code}),
            ("/admin/receive_inventories.json", {"q": code}),
            ("/admin/receive_inventories.json", {"code": code}),
            ("/admin/receive_inventories.json", {"search": code}),
        ]

        last_err: Exception | None = None
        for path, params in candidates:
            try:
                # Try BasicAuth then headers as well
                if self._cfg.token_primary and self._cfg.token_secondary:
                    resp = self._get(path, auth=(self._cfg.token_primary, self._cfg.token_secondary), params=params)
                    if resp.status_code == 200:
                        payload = resp.json()
                        rid = _find_id_in_list_payload(payload, code)
                        if rid is not None:
                            return rid, "basic-auth(list)"
                for s in self._cfg.auth_strategies:
                    resp = self._get(path, strategy=s, params=params)
                    if resp.status_code == 200:
                        payload = resp.json()
                        rid = _find_id_in_list_payload(payload, code)
                        if rid is not None:
                            return rid, f"{s.name}(list)"
            except Exception as e:
                last_err = e
                continue

        if last_err:
            return None, ""
        return None, ""

    def get_receive_inventory(self, marec: str) -> tuple[list[ReceiveInventoryItem], str, str]:
        """
        Trả về: (danh_sách_dòng, auth_strategy, url_đã_gọi).
        Không lọc theo receipt_status — mọi phiếu có id/mã hợp lệ đều đọc được chi tiết.
        url_đã_gọi dùng để hiển thị trên UI (GET .../admin/receive_inventories/{id}.json).
        """
        marec = marec.strip()
        if not marec:
            raise ValueError("Mã receive_inventories đang trống.")

        # If user types code like REI00497, try to resolve to numeric id.
        id_or_raw = marec
        if not marec.isdigit():
            resolved_id, _strat = self._resolve_id_by_code(marec)
            if resolved_id is not None:
                id_or_raw = str(resolved_id)

        path = f"/admin/receive_inventories/{id_or_raw}.json"
        full_url = f"{self._cfg.base_url}{path}"

        resp, strat = self._attempt_get(path, params=None)
        if resp.status_code == 200:
            payload = resp.json()
            items = _extract_items(payload)
            if not items:
                # surface a bit more context
                rid = _extract_receive_inventory_id(payload)
                rcode = _extract_receive_inventory_code(payload)
                raise SapoApiError(
                    "API OK nhưng không parse được danh sách sản phẩm.\n"
                    f"id={rid} code={rcode} strategy={strat}"
                )
            return items, strat, full_url

        raise SapoApiError(f"HTTP {resp.status_code}: {resp.text[:800]}")

    def list_received_receive_inventories(self) -> tuple[list[ReceiveInventorySummary], str, str]:
        """
        Danh sách đầy đủ trên app: chỉ phiếu có receipt_status == received
        (thử query receipt_status=received; nếu API không lọc thì lọc phía client).
        Trả về: (danh_sách, auth_strategy, url_gốc).
        """
        path = "/admin/receive_inventories.json"
        base_url = f"{self._cfg.base_url}{path}"

        param_variants: list[dict[str, Any]] = [
            {"limit": 250, "page": 1, "receipt_status": "received"},
            {"limit": 250, "page": 1, "status": "received"},
            {"limit": 250, "page": 1},
        ]

        working_params: dict[str, Any] | None = None
        strategy = ""

        for p0 in param_variants:
            resp, strat = self._attempt_get(path, params=p0)
            strategy = strat
            if resp.status_code != 200:
                continue
            resp.json()  # validate JSON
            working_params = dict(p0)
            break

        if working_params is None:
            raise SapoApiError(f"Không đọc được danh sách receive_inventories từ {base_url}")

        merged: dict[int, ReceiveInventorySummary] = {}
        page = 1
        limit = int(working_params.get("limit", 250))

        while page < 500:
            params = {**working_params, "page": page, "limit": limit}
            resp, strategy = self._attempt_get(path, params=params)
            if resp.status_code != 200:
                raise SapoApiError(f"HTTP {resp.status_code} khi đọc trang {page}: {resp.text[:500]}")
            payload = resp.json()
            raw = _iter_receive_inventory_list_dicts(payload)
            if not raw:
                break
            for obj in raw:
                summ = _summary_from_row(obj)
                if summ is None:
                    continue
                if str(summ.receipt_status).strip().lower() != "received":
                    continue
                merged[summ.id] = summ
            if len(raw) < limit:
                break
            page += 1

        items = sorted(merged.values(), key=lambda s: s.id, reverse=True)
        return items, strategy, base_url


def _iter_receive_inventory_list_dicts(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for it in payload:
            if isinstance(it, dict):
                rows.append(it)
        return rows
    if not isinstance(payload, dict):
        return rows
    for k in ("receive_inventories", "items", "data", "receive_inventory"):
        v = payload.get(k)
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict):
                    rows.append(it)
            if rows:
                return rows
    for v in payload.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for it in v:
                if isinstance(it, dict):
                    rows.append(it)
            if rows:
                return rows
    return rows


def _unwrap_receive_row(obj: dict[str, Any]) -> dict[str, Any]:
    inner = obj.get("receive_inventory")
    if isinstance(inner, dict):
        merged = {**inner, **{k: v for k, v in obj.items() if k != "receive_inventory"}}
        return merged
    return obj


def _summary_from_row(obj: dict[str, Any]) -> ReceiveInventorySummary | None:
    d = _unwrap_receive_row(obj)
    rid = _as_int(d.get("id"))
    if rid is None:
        return None
    code = str(d.get("code") or "").strip()
    st = str(d.get("receipt_status") or d.get("status") or "").strip()
    created = str(d.get("created_on") or d.get("created_at") or d.get("updated_on") or "").strip()
    return ReceiveInventorySummary(id=rid, code=code, receipt_status=st, created_on=created)


def _find_id_in_list_payload(payload: Any, code: str) -> int | None:
    """
    Try to find receive inventory id by matching code inside list endpoint payload.
    """
    code = code.strip().lower()
    if not code:
        return None

    # payload could be { receive_inventories: [...] } or just [...]
    lists: list[Any] = []
    if isinstance(payload, list):
        lists.append(payload)
    elif isinstance(payload, dict):
        for k in ("receive_inventories", "items", "data"):
            if isinstance(payload.get(k), list):
                lists.append(payload[k])
        # sometimes nested dict includes list
        for v in payload.values():
            if isinstance(v, list):
                lists.append(v)

    for lst in lists:
        for it in lst:
            if not isinstance(it, dict):
                continue
            it_code = (it.get("code") or it.get("receive_inventory_code") or "")
            if isinstance(it_code, str) and it_code.strip().lower() == code:
                try:
                    return int(it.get("id"))
                except Exception:
                    return None
    return None
