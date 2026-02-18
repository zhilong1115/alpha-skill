"""Parse SEC 13F filings from EDGAR."""

from __future__ import annotations

from typing import Optional

import requests
from bs4 import BeautifulSoup

EDGAR_HEADERS = {
    "User-Agent": "StockTrader research@example.com",
    "Accept": "application/xml, text/xml, application/json",
}


def parse_13f_xml(xml_content: str) -> list[dict]:
    """Parse 13F XML filing into a list of holdings.

    Args:
        xml_content: Raw XML content of a 13F-HR information table.

    Returns:
        List of dicts with keys: name, cusip, value_thousands, shares, share_type.
    """
    holdings = []
    try:
        soup = BeautifulSoup(xml_content, "html.parser")
        # 13F info tables use <infotable> or <ns1:infotable> tags
        entries = soup.find_all(["infotable", "ns1:infotable"])
        if not entries:
            # Try finding by common tag patterns
            entries = soup.find_all(True, recursive=True)
            entries = [
                e for e in entries
                if e.name and "infotable" in e.name.lower()
            ]

        for entry in entries:
            holding = {}
            # Name
            name_tag = entry.find(
                lambda t: t.name and "nameofissuer" in t.name.lower()
            )
            holding["name"] = name_tag.text.strip() if name_tag else ""

            # CUSIP
            cusip_tag = entry.find(
                lambda t: t.name and "cusip" in t.name.lower()
            )
            holding["cusip"] = cusip_tag.text.strip() if cusip_tag else ""

            # Value (in thousands)
            val_tag = entry.find(
                lambda t: t.name and "value" in t.name.lower()
            )
            try:
                holding["value_thousands"] = int(val_tag.text.strip()) if val_tag else 0
            except ValueError:
                holding["value_thousands"] = 0

            # Shares
            shares_tag = entry.find(
                lambda t: t.name and "sshprnamt" in t.name.lower()
            )
            try:
                holding["shares"] = int(shares_tag.text.strip()) if shares_tag else 0
            except ValueError:
                holding["shares"] = 0

            # Share type
            type_tag = entry.find(
                lambda t: t.name and "sshprnamttype" in t.name.lower()
            )
            holding["share_type"] = type_tag.text.strip() if type_tag else "SH"

            if holding["cusip"]:
                holdings.append(holding)

    except Exception as e:
        print(f"[filing_parser] Error parsing 13F XML: {e}")

    return holdings


def fetch_latest_13f(cik: str) -> list[dict]:
    """Fetch and parse the latest 13F filing for a given CIK.

    Args:
        cik: SEC Central Index Key (numeric string).

    Returns:
        List of holding dicts from the most recent 13F filing.
    """
    try:
        padded = cik.zfill(10)
        # Get submissions index
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        # Find first 13F-HR
        for i, form in enumerate(forms):
            if "13F" in str(form).upper():
                accession = accessions[i].replace("-", "")
                doc = primary_docs[i]
                doc_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{padded}/{accession}/{doc}"
                )
                doc_resp = requests.get(doc_url, headers=EDGAR_HEADERS, timeout=15)
                doc_resp.raise_for_status()
                return parse_13f_xml(doc_resp.text)

        print(f"[filing_parser] No 13F filing found for CIK {cik}")
        return []

    except Exception as e:
        print(f"[filing_parser] Error fetching 13F for CIK {cik}: {e}")
        return []
