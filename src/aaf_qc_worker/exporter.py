"""Exporter identification from the AAF Header's identification history."""

from datetime import datetime


def unknown_exporter_message(header, rule):
    identifications = list(header["IdentificationList"].value or [])
    fields = rule["field_map"]
    accepted = [rule, *rule.get("also_accepted", [])]
    for item in identifications:
        product = (item[fields["product"]].value or "").casefold()
        company = (item[fields["company"]].value or "").casefold()
        for known in accepted:
            if known["product_contains"].casefold() in product and any(
                known["vendor_contains"].casefold() in value for value in (company, product)
            ):
                return None
    # Dates decide recency; list order breaks ties and covers missing dates.
    latest = max(
        enumerate(identifications),
        key=lambda pair: (pair[1][fields["date"]].value or datetime.min, pair[0]),
    )[1] if identifications else None
    product = latest[fields["product"]].value if latest is not None else None
    company = latest[fields["company"]].value if latest is not None else None
    return rule["message"].format(
        product=product or "Unknown product", company=company or "Unknown company",
    )
