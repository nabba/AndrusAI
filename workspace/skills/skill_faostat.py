"""
FAOSTAT API Skill Module
========================

Provides functions to query the FAO STAT API for agricultural, food security,
land use, and forestry data.

Base URL: https://fenixservices.fao.org/faostat/api/v1/en/

Functions:
    - faostat_list_domains(): List all available data domains
    - faostat_get_data(domain, area, element, year): Query specific data

Example:
    >>> domains = faostat_list_domains()
    >>> data = faostat_get_data("QC", "US", "Production", 2020)
"""

import requests
from typing import Optional, Dict, Any, List
import json

FAOSTAT_BASE_URL = "https://fenixservices.fao.org/faostat/api/v1/en/"

# Known domain codes (partial list - can be extended)
KNOWN_DOMAINS = {
    "QC": "Crops and livestock products",
    "EF": "Emissions - Agriculture",
    "EI": "Emissions - Land use",
    "GA": "Food balances",
    "HC": "Land use",
    "HT": "Forestry",
    "LC": "Land cover",
    "OC": "Soil",
    "PE": "Population",
    "PP": "Production",
    "PQ": "Food security",
    "RA": "Inputs (Fertilizers, Pesticides)",
    "RL": "Land use (Crops)",
    "RP": "Prices",
    "SA": "Investment",
    "SD": "Sustainability",
    "TM": "Trade",
    "UG": "Food supply",
}


def faostat_list_domains() -> List[Dict[str, str]]:
    """
    List all available FAOSTAT data domains.

    Returns:
        List of dicts with 'code' and 'description' keys.

    Example:
        >>> domains = faostat_list_domains()
        >>> print(domains[:3])
        [{'code': 'QC', 'description': 'Crops and livestock products'}, ...]
    """
    try:
        response = requests.get(
            f"{FAOSTAT_BASE_URL}domains",
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        # Extract domain codes and descriptions
        domains = []
        if isinstance(data, dict) and "data" in data:
            for item in data["data"]:
                domains.append({
                    "code": item.get("domainCode", ""),
                    "description": item.get("domainName", "")
                })
        elif isinstance(data, list):
            for item in data:
                domains.append({
                    "code": item.get("domainCode", ""),
                    "description": item.get("domainName", "")
                })

        return domains
    except requests.exceptions.Timeout:
        raise RuntimeError("FAOSTAT API request timed out")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot connect to FAOSTAT API")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"FAOSTAT API HTTP error: {e}")
    except json.JSONDecodeError:
        raise RuntimeError("Invalid JSON response from FAOSTAT")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {e}")


def faostat_get_data(
    domain: str,
    area: Optional[str] = None,
    element: Optional[str] = None,
    year: Optional[int] = None,
    item: Optional[str] = None,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Retrieve FAOSTAT data for a given domain and optional filters.

    Args:
        domain: Domain code (e.g., "QC" for crops, "PP" for production)
        area: Country/region code (e.g., "US", "BR", "CN"). Use None for all.
        element: Element code (e.g., "Production", "Yield", "Area Harvested")
        year: Year as integer (e.g., 2020). Use None for all years.
        item: Item code (e.g., "0156" for wheat). Use None for all.
        limit: Max records to return (default 1000, max 10000)

    Returns:
        Dict with 'data' key containing list of records and 'metadata'.

    Example:
        >>> result = faostat_get_data("QC", area="US", element="Production", year=2020)
        >>> print(result['data'][0])
        {'area': 'United States', 'element': 'Production', 'item': 'Wheat', 'value': 49258000, 'year': 2020}
    """
    if not domain:
        raise ValueError("Domain code is required")

    params: Dict[str, Any] = {
        "domain": domain.upper(),
        "limit": min(limit, 10000)
    }
    if area:
        params["area"] = area
    if element:
        params["element"] = element
    if year:
        params["year"] = year
    if item:
        params["item"] = item

    try:
        response = requests.get(
            f"{FAOSTAT_BASE_URL}data",
            params=params,
            timeout=20
        )
        response.raise_for_status()
        result = response.json()

        return {
            "data": result.get("data", []),
            "metadata": {
                "domain": domain,
                "area": area,
                "element": element,
                "year": year,
                "item": item,
                "total_records": len(result.get("data", []))
            }
        }
    except requests.exceptions.Timeout:
        raise RuntimeError("FAOSTAT API request timed out")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot connect to FAOSTAT API")
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            raise ValueError(f"Invalid domain or parameters: {domain}")
        raise RuntimeError(f"FAOSTAT API HTTP error: {e}")
    except json.JSONDecodeError:
        raise RuntimeError("Invalid JSON response from FAOSTAT")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {e}")


def faostat_search(
    domain: str,
    query: str,
    search_type: str = "item"  # "item", "area", "element"
) -> List[Dict[str, Any]]:
    """
    Search within a FAOSTAT domain for items matching a query string.

    Args:
        domain: Domain code
        query: Search string
        search_type: Field to search ("item", "area", "element")

    Returns:
        List of matching records.

    Example:
        >>> results = faostat_search("QC", "wheat", search_type="item")
    """
    if search_type not in ("item", "area", "element"):
        raise ValueError("search_type must be 'item', 'area', or 'element'")

    result = faostat_get_data(domain=domain)
    matches = []
    for record in result.get("data", []):
        field_value = record.get(search_type, "")
        if field_value and query.lower() in str(field_value).lower():
            matches.append(record)

    return matches


# Convenience wrappers for common use cases
def faostat_get_crop_production(
    country_code: str,
    crop_code: Optional[str] = None,
    year: Optional[int] = None
) -> Dict[str, Any]:
    """Get crop production data for a country."""
    return faostat_get_data(
        domain="PP",
        area=country_code,
        item=crop_code,
        element="Production",
        year=year
    )


def faostat_get_forest_area(
    country_code: str,
    year: Optional[int] = None
) -> Dict[str, Any]:
    """Get forest area data for a country."""
    return faostat_get_data(
        domain="HT",
        area=country_code,
        element="Forest area",
        year=year
    )


if __name__ == "__main__":
    # Quick sanity check / demo
    print("FAOSTAT Skill Module - Self Test")
    print("=" * 50)

    try:
        print("\n1. Listing domains...")
        domains = faostat_list_domains()
        print(f"   Found {len(domains)} domains")
        print(f"   First 3: {domains[:3]}")
    except Exception as e:
        print(f"   ERROR: {e}")

    try:
        print("\n2. Querying sample data (QC domain, US, 2020)...")
        result = faostat_get_data("QC", area="US", year=2020, limit=5)
        print(f"   Got {result['metadata']['total_records']} records")
        if result['data']:
            print(f"   Sample: {result['data'][0]}")
    except Exception as e:
        print(f"   ERROR: {e}")

    print("\nTest complete.")
