"""Verplichte velden van de MediaGuard-leesroutes die MSO meet."""

from typing import Any


def has_fields(body: Any, **fields: type) -> bool:
    return isinstance(body, dict) and all(type(body.get(name)) is kind for name, kind in fields.items())


def nonnegative(value: Any) -> bool:
    return type(value) is int and value >= 0


def wanted_item(body: Any) -> bool:
    if not has_fields(body, id=int, instance_id=str, media_type=str, kind=str, reason=str, title=str,
                      status=str, attempts=int):
        return False
    if not (0 < body["id"] < 2**63 and 0 <= body["attempts"] < 2**31 and body["instance_id"]):
        return False
    for field, limit in (("provider_id", 2**63), ("item_provider_id", 2**63), ("season_number", 2**31),
                         ("episode_number", 2**31), ("absolute_episode_number", 2**31), ("priority", 2**31)):
        value = body.get(field)
        if value is not None and (type(value) is not int or not -limit <= value < limit):
            return False
    for field in ("item_title", "air_date_utc", "last_searched_at", "last_reason_code"):
        value = body.get(field)
        if value is not None and not isinstance(value, str):
            return False
    return True


def wanted_page(body: Any, page: int, page_size: int) -> bool:
    if not has_fields(body, items=list, total=int) or not nonnegative(body["total"]):
        return False
    items = body["items"]
    expected = min(page_size, max(0, body["total"] - (page - 1) * page_size))
    return len(items) == expected and all(wanted_item(item) for item in items) and (
        len({item["id"] for item in items}) == len(items)
    )


def kpis(body: Any) -> bool:
    return has_fields(body, missing=int, cutoff=int, reason_distribution=dict) and (
        nonnegative(body["missing"]) and nonnegative(body["cutoff"])
        and all(nonnegative(count) for count in body["reason_distribution"].values())
    )


def reasons(body: Any) -> bool:
    return isinstance(body, dict) and all(
        has_fields(reason, label_nl=str, fix_nl=str) for reason in body.values()
    )


def quota(body: Any) -> bool:
    return isinstance(body, dict) and all(
        isinstance(row, dict) and all(nonnegative(row.get(key)) for key in (
            "hour_used", "hour_max", "day_used", "day_max", "remaining"
        ))
        for row in body.values()
    )


def diagnosis(body: Any) -> bool:
    return has_fields(body, wanted_id=int, primary_code=str, reasons=list) and all(
        has_fields(reason, label_nl=str, message_nl=str, suggested_fix_nl=str, ts=str)
        for reason in body["reasons"]
    )


def queue(body: Any) -> bool:
    return has_fields(body, page=int, pageSize=int, totalRecords=int, records=list) and (
        body["page"] > 0 and body["pageSize"] > 0 and nonnegative(body["totalRecords"])
        and all(has_fields(row, id=int) and "downloadId" in row
                and (row["downloadId"] is None or isinstance(row["downloadId"], str)) for row in body["records"])
    )
