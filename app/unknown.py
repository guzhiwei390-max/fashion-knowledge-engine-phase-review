UNKNOWN = "Unknown"


def unknown_response(*fields: str) -> dict:
    response = {"result": UNKNOWN}
    if fields:
        response["unknown"] = list(fields)
    return response


def is_unknown(value: object) -> bool:
    return value in (None, "", UNKNOWN)
