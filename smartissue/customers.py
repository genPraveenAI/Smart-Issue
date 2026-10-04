from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROFILE_SEED_PATH = ROOT / "data" / "customer_profiles.json"
PROFILE_STORE_PATH = ROOT / ".data" / "customer_profiles.json"
ACCOUNT_PRODUCTS = ("Current account", "Easy-access savings", "Cash ISA")
DEMOGRAPHIC_FIELDS = (
    "first_name",
    "last_name",
    "date_of_birth",
    "email",
    "phone",
    "address_line_1",
    "address_line_2",
    "city",
    "postcode",
    "country",
)
FIELD_LIMITS = {
    "first_name": 80,
    "last_name": 80,
    "date_of_birth": 10,
    "email": 254,
    "phone": 40,
    "address_line_1": 160,
    "address_line_2": 160,
    "city": 100,
    "postcode": 20,
    "country": 80,
}


def _read_document(path: Path | None) -> tuple[dict[str, Any], Path | None]:
    store_path = path or PROFILE_STORE_PATH
    read_path = store_path
    if path is None and not store_path.exists():
        read_path = PROFILE_SEED_PATH
    try:
        document = json.loads(read_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimeError(f"Could not read customer profiles at {read_path}.") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Customer profile JSON is invalid at line {error.lineno}, column {error.colno}.") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Customer profile JSON must use schema_version 1.")
    profiles = document.get("profiles")
    if not isinstance(profiles, list) or any(not isinstance(profile, dict) for profile in profiles):
        raise ValueError("Customer profile JSON must contain a profiles array of objects.")
    ids = [profile.get("customer_id") for profile in profiles]
    if any(not isinstance(customer_id, str) or not customer_id.strip() for customer_id in ids):
        raise ValueError("Every customer profile requires a customer_id.")
    if len(ids) != len(set(ids)):
        raise ValueError("Customer profile IDs must be unique.")
    return document, store_path if path is None else None


def _write_document(document: dict[str, Any], path: Path | None) -> None:
    destination = path or PROFILE_STORE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f".{destination.name}.tmp")
    temporary_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    temporary_path.replace(destination)


def load_customer_profiles(*, path: Path | None = None) -> list[dict[str, Any]]:
    document, _ = _read_document(path)
    return document["profiles"]


def search_customers(query: str, *, path: Path | None = None) -> list[dict[str, Any]]:
    profiles = load_customer_profiles(path=path)
    term = " ".join(query.split()).casefold()
    if not term:
        return profiles
    search_fields = ("customer_id", "first_name", "last_name", "email", "phone", "city", "postcode")
    return [
        profile
        for profile in profiles
        if any(term in str(profile.get(field, "")).casefold() for field in search_fields)
    ]


def _normalize_demographics(demographics: dict[str, str]) -> dict[str, str]:
    if set(demographics) != set(DEMOGRAPHIC_FIELDS):
        raise ValueError("All customer demographic fields must be supplied.")
    normalized = {}
    for field in DEMOGRAPHIC_FIELDS:
        value = demographics[field]
        if not isinstance(value, str):
            raise ValueError(f"{field.replace('_', ' ').capitalize()} must be text.")
        normalized[field] = " ".join(value.split())[: FIELD_LIMITS[field]]
    for field in DEMOGRAPHIC_FIELDS:
        if field != "address_line_2" and not normalized[field]:
            raise ValueError(f"{field.replace('_', ' ').capitalize()} is required.")
    try:
        date.fromisoformat(normalized["date_of_birth"])
    except ValueError as error:
        raise ValueError("Date of birth must use YYYY-MM-DD format.") from error
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized["email"]):
        raise ValueError("Enter a valid email address.")
    return normalized


def update_customer_demographics(
    customer_id: str,
    demographics: dict[str, str],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    document, store_path = _read_document(path)
    customer = next((item for item in document["profiles"] if item["customer_id"] == customer_id), None)
    if customer is None:
        raise ValueError("Customer not found.")
    customer.update(_normalize_demographics(demographics))
    customer["updated_at"] = datetime.now(UTC).isoformat()
    _write_document(document, path or store_path)
    return customer


def open_customer_account(
    customer_id: str,
    product_type: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    if product_type not in ACCOUNT_PRODUCTS:
        raise ValueError("Select a supported account product.")
    document, store_path = _read_document(path)
    customer = next((item for item in document["profiles"] if item["customer_id"] == customer_id), None)
    if customer is None:
        raise ValueError("Customer not found.")
    accounts = customer.setdefault("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("Customer accounts must be stored as an array.")
    account = {
        "account_id": f"AC-{uuid.uuid4().hex[:8].upper()}",
        "product_type": product_type,
        "currency": "GBP",
        "branch": "042",
        "status": "Active",
        "opened_at": datetime.now(UTC).isoformat(),
    }
    accounts.append(account)
    customer["updated_at"] = datetime.now(UTC).isoformat()
    _write_document(document, path or store_path)
    return account