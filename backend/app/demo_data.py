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
