"""Fixed identities for the demo tenant. All companies, GSTINs and bank details are
fictional (GSTINs are well-formed with valid check characters, but made up).

Shared by the seed (vendor master) and scripts/make_sample_invoices.py (invoices), so the
two always agree.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoVendor:
    name: str
    address: str
    gstin: str
    email: str
    bank_account: str  # full number appears only on the synthetic invoice image
    bank_ifsc: str

    @property
    def bank_last4(self) -> str:
        return self.bank_account[-4:]


BUYER_NAME = "Demo Traders Pvt Ltd"
BUYER_ADDRESS = "201 Market Yard, Pune, Maharashtra 411037"
BUYER_GSTIN = "27DEMCT1001D1ZJ"

KAVERI = DemoVendor(
    "Kaveri Office Supplies LLP",
    "14 Lakeview Road, Pune, Maharashtra 411001",
    "27KAVCO4821K1ZJ",
    "accounts@kaveri-office.example",
    "501004821190517",
    "HDFC0106756",
)
NILGIRI = DemoVendor(
    "Nilgiri Cloud Services Pvt Ltd",
    "88 Residency Road, Bengaluru, Karnataka 560025",
    "29NILCC7310N1ZD",
    "billing@nilgiri-cloud.example",
    "918020073108850",
    "ICIC0000418",
)
SHREE_GANESH = DemoVendor(
    "Shree Ganesh Packaging Works",
    "Plot 7, MIDC Bhosari, Pune, Maharashtra 411026",
    "27SGPCW2264S1ZG",
    "sgpw.accounts@example.com",
    "33012264001651",
    "SBIN0041869",
)
# Not in the vendor master: invoices from it must go to a human.
ZEPHYR = DemoVendor(
    "Zephyr Event Rentals",
    "Okhla Phase II, New Delhi 110020",
    "07ZEPCT5531Q1ZM",
    "pay@zephyr-events.example",
    "77010055310093",
    "YESB0000771",
)

KNOWN_VENDORS = (KAVERI, NILGIRI, SHREE_GANESH)


@dataclass(frozen=True)
class DemoLine:
    description: str
    hsn_sac: str
    quantity: str
    unit_price: str
    tax_rate: str


@dataclass(frozen=True)
class DemoPO:
    po_number: str
    vendor: DemoVendor
    date: str
    lines: tuple[DemoLine, ...]


PO_KAVERI_STATIONERY = DemoPO(
    "PO-2026-0412",
    KAVERI,
    "2026-09-05",
    (
        DemoLine("A4 copier paper, 75 gsm (ream)", "4802", "40", "245.00", "12"),
        DemoLine("Gel pens, blue (box of 10)", "9608", "25", "120.00", "18"),
        DemoLine("Box files, foolscap", "4820", "30", "85.50", "18"),
    ),
)
PO_KAVERI_SUPPLIES = DemoPO(
    "PO-2026-0431",
    KAVERI,
    "2026-09-15",
    (
        DemoLine("Whiteboard markers (box of 12)", "9608", "10", "310.00", "18"),
        DemoLine("Stapler, heavy duty", "8472", "4", "640.00", "18"),
    ),
)
PO_KAVERI_TONER = DemoPO(
    "PO-2026-0450",
    KAVERI,
    "2026-09-20",
    (DemoLine("Printer toner cartridge, black", "8443", "6", "2450.00", "18"),),
)
# Ordered 1000 boxes; the sample invoice bills the first 500 (a normal part-delivery).
PO_SHREE_GANESH_PACKAGING = DemoPO(
    "PO-2026-0398",
    SHREE_GANESH,
    "2026-08-28",
    (
        DemoLine("Corrugated boxes 18x12x10 in", "4819", "1000", "32.40", "12"),
        DemoLine("Packing tape 48 mm x 65 m", "3919", "60", "54.00", "18"),
    ),
)

DEMO_POS = (PO_KAVERI_STATIONERY, PO_KAVERI_SUPPLIES, PO_KAVERI_TONER, PO_SHREE_GANESH_PACKAGING)
