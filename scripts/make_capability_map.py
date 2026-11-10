#!/usr/bin/env python3
"""
make_capability_map.py  —  build the reviewable capability mapping sheet

The Tech Hub workbook describes each supplier with free text capability
phrases. The fab cutaway on the dashboard needs each supplier tagged with one
or more of the 18 fixed capability keys, because those keys are what light up
the fab zones. This script proposes that mapping so you can correct it.

Output: capability_map.xlsx, one row per distinct phrase, with

    Capability Phrase    the phrase exactly as it appears in the workbook
    Suppliers            how many suppliers use it
    Suggested Key        the proposed one of the 18 keys, or blank
    Suggested Capability the human readable name of that key
    Alternates           other keys that also matched, strongest first
    Confidence           high, medium, low, or none
    Approved Key         EMPTY, for you to fill in. Overrides Suggested Key.
    Notes                why the suggestion was made

Paste the finished sheet into the master workbook as a tab named
Capability_Map. build_dashboard_data.py reads it from there, prefers
Approved Key over Suggested Key, and warns about any phrase missing from it.

Usage
    python make_capability_map.py --source Tech_Hub_Company_Listing.xlsx
    python make_capability_map.py --source master.xlsx --existing capability_map.xlsx

Requires: pandas, openpyxl
"""

from __future__ import annotations

import argparse
import collections
import re
from pathlib import Path

import pandas as pd


SOURCE_SHEET = "Scraped capability statements"
PHRASE_COLUMNS = ["Primary_Capability", "Secondary_Capabilities"]
PHRASE_DELIMITER = ";"

# The 18 fixed keys the fab cutaway understands, copied from the page.
CAPABILITIES = {
    "hepa":  "HEPA/ULPA Filtration & Fan Filter Units",
    "iot":   "Environmental & IoT Sensor Mesh",
    "amhs":  "Automated Material Handling (AMHS/OHB)",
    "dep":   "Semiconductor Deposition Equipment Manufacturing",
    "litho": "Lithography, Etch & Metrology Equipment",
    "cnc":   "Precision CNC Machining",
    "sens":  "Pressure & Specialized Sensor Manufacturing",
    "vac":   "Vacuum Pumps & Exhaust Abatement",
    "gas":   "Gas & Chemical Delivery Systems",
    "chem":  "Specialty Chemicals & Industrial Gases",
    "rf":    "RF & Plasma Power Subsystems",
    "vib":   "Vibration Isolation & Motion Control",
    "upw":   "Ultrapure Water Treatment (Filtration & RO)",
    "pump":  "Pumps & Fluid Distribution",
    "opt":   "Precision Optics & Photonics",
    "mat":   "Advanced Materials & Substrates",
    "elec":  "Electronics, Power & Interconnect",
    "batt":  "Backup Power, Batteries & Hydrogen",
}

# Keyword rules. Each entry is (key, weight, regex). Higher weight wins.
# Weight 3 means the term is decisive for that key, 2 strong, 1 suggestive.
RULES: list[tuple[str, int, str]] = [
    # --- lithography, etch, metrology ---
    ("litho", 3, r"photolith|lithograph|\beuv\b|\bduv\b|reticle|photomask|\bstepper\b"),
    ("litho", 3, r"\betch(ing|ant)?\b|plasma etch|ion implant|\bcmp\b|planariz"),
    ("litho", 3, r"metrology|interferomet|ellipsomet|profilomet|wafer inspection"),
    ("litho", 2, r"wafer fabrication|semiconductor fabrication|front.?end of line|\bfeol\b"),
    ("litho", 2, r"\bdram\b|\bnand\b|chip design|\bic design\b|integrated circuit|\bfoundry\b"),
    ("litho", 1, r"\binspection\b.*(wafer|semiconductor|optical)|critical dimension"),

    # --- deposition ---
    ("dep", 3, r"deposition|\bcvd\b|\bpvd\b|\bald\b|mocvd|\bpecvd\b|epitax|sputter"),
    ("dep", 2, r"thin.?film|evaporat(ion|or)|\bmbe\b"),

    # --- optics and photonics ---
    ("opt", 3, r"optic|photonic|\blens(es)?\b|interferometer|spectromet|waveguide"),
    ("opt", 3, r"\blaser\b|\bmirror\b|\bprism\b|diffract|collimat|\bphotodiode\b"),
    ("opt", 2, r"imaging|\binfrared\b|\buv\b.*(curing|optics)|fiber.?optic|beam shaping"),
    ("opt", 1, r"polish|grinding.*(glass|optical|crystal)|\bwindow"),

    # --- advanced materials and substrates ---
    ("mat", 3, r"substrate|\bsilicon\b|\bsapphire\b|\bgallium\b|\bgermanium\b|\bsic\b"),
    ("mat", 3, r"advanced material|nanomaterial|graphene|\bcrystal(line|s)?\b|\bquartz\b"),
    ("mat", 2, r"\bceramic|\bpolymer|composite|\balloy\b|powder metal|carbide|\bresin\b"),
    ("mat", 2, r"specialty glass|\bglass\b.*(precision|technical)|insulation|\bfiber\b.*material"),
    ("mat", 2, r"\badhesive|\bsealant|\bepoxy\b|\babrasive"),
    ("mat", 1, r"\bmaterials?\b.*(science|engineering|informatics|characteriz)"),

    # --- specialty chemicals and industrial gases ---
    ("chem", 3, r"specialty (chemical|gas)|industrial gas|electronic.?grade|photoresist|\bslurry\b"),
    ("chem", 3, r"gas (blending|analysis|generation|manufactur)|cylinder (fill|gas)"),
    ("chem", 3, r"chlor.?alkali|\bsolvent|\bacid\b|caustic|\breagent|electrolyte"),
    ("chem", 2, r"\bchemical(s| manufact| blend| process| suppl)|\bgases\b|nitrogen|\bargon\b|\bhelium\b"),
    ("chem", 1, r"\bcoating(s)?\b|surface treat|\bplating\b.*chemical|\blubricant"),

    # --- gas and chemical delivery systems ---
    ("gas", 3, r"gas (delivery|panel|distribution|handling|cabinet)|chemical delivery"),
    ("gas", 3, r"mass flow|ultra.?high.?purity|\buhp\b|gas (line|system)s?\b"),
    ("gas", 2, r"\bvalve|\bregulator|\bfitting|\bmanifold|tube (bending|weld)|orbital weld"),
    ("gas", 2, r"\bgasket|\bseal(s|ing)\b|expansion joint|\bo.?ring\b"),
    ("gas", 1, r"piping system|\btubing\b|\bhose\b|process utilit"),

    # --- vacuum ---
    ("vac", 3, r"\bvacuum\b|turbomolecular|\babatement\b|\bscrubber|exhaust (system|treat|gas)"),
    ("vac", 2, r"leak (detection|test)|dry pump|\bblower\b.*exhaust"),

    # --- pumps and fluid distribution ---
    ("pump", 3, r"\bpump(s|ing)?\b(?!.*vacuum)|fluid (distribution|handling|power|transfer)"),
    ("pump", 2, r"\bhydraulic|\bpneumatic|\bchiller|cooling (tower|water|loop)|\bplumbing\b"),
    ("pump", 1, r"\bpiping\b|\bflange|\bcompressor"),

    # --- ultrapure water ---
    ("upw", 3, r"ultrapure water|\bupw\b|reverse osmosis|\bdeioniz|\bdi water\b"),
    ("upw", 3, r"wastewater|water (treatment|recycl|purif|reclaim)|\beffluent\b"),
    ("upw", 2, r"\bwater\b.*(filtration|quality|system)|water management"),

    # --- cleanroom air ---
    ("hepa", 3, r"\bhepa\b|\bulpa\b|fan filter|\bffu\b|cleanroom"),
    ("hepa", 3, r"\bhvac\b|air handling|air filtration|\bventilation\b"),
    ("hepa", 2, r"dust collection|contamination control|particle (count|control)|\bairflow\b"),

    # --- environmental and IoT sensing ---
    ("iot", 3, r"\biot\b|\bscada\b|telemetry|data acquisition|environmental monitor"),
    ("iot", 3, r"building (automation|management)|remote monitoring|condition monitoring"),
    ("iot", 2, r"\bmonitoring\b.*(system|platform|network|central.?station)|digital twin"),
    ("iot", 2, r"industrial (automation|internet|controls)|\bplc\b|process control"),

    # --- sensors and instrumentation ---
    ("sens", 3, r"\bsensor|\bsensing\b|\btransducer|accelerometer|load cell|thermocouple|strain gau?ge"),
    ("sens", 3, r"pressure (measure|sensor|transduc|gau?ge|instrument)"),
    ("sens", 2, r"instrumentation|\bcalibrat|\bprobe(s)?\b|flow (meter|measure)|\bgau?ges?\b"),

    # --- RF and plasma power ---
    ("rf", 3, r"\brf\b|radio.?frequency|\bplasma\b(?!.*etch)|\bmicrowave\b"),
    ("rf", 3, r"power (conversion|supplies|supply|generator)|induction heating"),
    ("rf", 2, r"\bamplifier|\binverter\b|\bmagnetron\b|impedance match"),

    # --- electronics, power, interconnect ---
    ("elec", 3, r"\bpcb\b|printed circuit|\bsmt\b|through.?hole|surface mount|\bsolder"),
    ("elec", 3, r"connector|\binterconnect|wire harness|\bcable (assembl|manufact)"),
    ("elec", 3, r"semiconductor packaging|advanced packaging|\bdie attach|wire bond"),
    ("elec", 2, r"electronic(s| assembl| manufact)|\bcircuit\b|\bswitchgear|\btransformer"),
    ("elec", 2, r"electrical (contract|system|distribution|install)|\bbus ?bar"),
    ("elec", 2, r"\bharness(es)?\b|\bcable\b|box build|wire form|\bbackplane\b"),
    ("elec", 1, r"\bmotor\b|\bdrives?\b|^electrical$|\bassembly\b"),

    # --- backup power, batteries, hydrogen ---
    ("batt", 3, r"\bbatter(y|ies)|lithium|fuel cell|\bhydrogen\b|electrolyzer|\bups\b"),
    ("batt", 3, r"energy storage|backup power|\bgenset\b|standby (power|generator)"),
    ("batt", 2, r"\bsolar\b|photovoltaic|\bpv\b.*(panel|system)|renewable energy"),

    # --- vibration isolation and motion control ---
    ("vib", 3, r"vibration|seismic|\bdamper|isolation (slab|system|mount)|\bisolator"),
    ("vib", 3, r"motion control|\bactuator|\bservo\b|precision (stage|positioning)"),
    ("vib", 2, r"\bbearing(s)?\b|linear (motion|guide)|\bgimbal|slip ring"),

    # --- material handling ---
    ("amhs", 3, r"material handling|\bamhs\b|\boht\b|overhead (hoist|buffer|convey)"),
    ("amhs", 3, r"\bconveyor|\bcrane\b|\bhoist\b|\bagv\b|\bamr\b|\bstocker\b|lift assist"),
    ("amhs", 2, r"\brobot(ic|s)?\b|automated (storage|retrieval|guided)|palletiz"),
    ("amhs", 1, r"warehouse automation|\bforklift"),

    # --- precision machining and fabrication ---
    ("cnc", 3, r"\bcnc\b|machining|\bmilling\b|\bturning\b|\blathe\b|\bedm\b|swiss turn"),
    ("cnc", 3, r"injection mold|\bmould|\bdie (cast|mak)|\btooling\b|\bstamping\b"),
    ("cnc", 3, r"additive manufactur|3d print|rapid prototyp|\bprototyping\b"),
    ("cnc", 2, r"\bfabricat|sheet metal|\bwelding\b|laser cutt|waterjet|\bgrinding\b"),
    ("cnc", 2, r"metal (form|work|fabricat)|\bcasting\b|\bforging\b|\bdeburr|\bhoning\b"),
    ("cnc", 2, r"mechanical assembly|\bboring\b|\bbroaching\b|\breaming\b|\bswaging\b|\bextrusion\b|\bbending\b"),
    ("cnc", 1, r"\banodiz|\bpassivat|heat treat|precision (component|part)|\bmachine shop\b"),
    ("cnc", 1, r"\bcmm\b|\bkitting\b|\bdrilling\b|secondary process"),
]

# Phrases that should deliberately stay unmapped: real services, but nothing
# the fab cutaway can light up. Matching one suppresses a weak suggestion.
NON_FAB = re.compile(
    r"janitorial|custodial|\blegal\b|\battorney|\baccounting\b|\binsurance\b|payroll|"
    r"staffing|recruit|\bhiring\b|\btraining\b|\bcurricul|\beducation\b|\bapprentice|"
    r"\bmarketing\b|branding|graphic design|\bweb design\b|public relations|"
    r"real estate|\bleasing\b|property manage|\blandscap|\bsnow removal\b|"
    r"general contract|site development|\bsurveying\b|architect|interior design|"
    r"\btrucking\b|\bfreight\b|\bcourier\b|last mile|\bwarehousing\b|"
    r"security (guard|officer|patrol)|\bcatering\b|food service|\bvending\b|"
    r"access control|central.?station|physical.?security|\bsurveillance\b|\balarm\b|"
    r"\bcloud (service|solution|comput)|bioinformatic|\bbroadband\b|\btelecom\b|"
    r"\bcargo\b|cold storage|\blogistics\b|supply chain (manage|consult)|\bshipping\b|"
    r"\bconsulting\b|business (advisory|development|planning)|grant writing|"
    r"\bcybersecurity\b|\bit support\b|help desk|managed (it|services)|"
    r"\bsoftware (develop|integrat|engineer)|\bapp development\b|website",
    re.I,
)

CONFIDENCE_BY_WEIGHT = {3: "high", 2: "medium", 1: "low"}


def normalize(phrase: str) -> str:
    """Tidy a phrase for display. Keeps the original casing."""
    return " ".join(str(phrase).split()).strip(" ,;.")


def phrase_key(phrase: str) -> str:
    """
    The lookup key. build_dashboard_data.py has an identical function, so the
    two sides cannot drift. Case insensitive, since the workbook spells the
    same phrase several ways (CNC Machining, CNC machining).
    """
    return normalize(phrase).lower()


def collect_phrases(source: Path) -> dict[str, tuple[str, int, list[str]]]:
    """
    Gather every capability phrase, folding case variants into one entry so the
    review sheet has one row per phrase rather than three spellings of it.

    Returns {lookup key: (display spelling, total uses, other spellings seen)}.
    """
    frame = pd.ExcelFile(source).parse(SOURCE_SHEET).dropna(how="all")
    seen: dict[str, collections.Counter] = {}
    for column in PHRASE_COLUMNS:
        if column not in frame.columns:
            print(f"  WARN  column {column} not found, skipped")
            continue
        for value in frame[column].dropna():
            for part in str(value).split(PHRASE_DELIMITER):
                phrase = normalize(part)
                if phrase:
                    seen.setdefault(phrase_key(phrase), collections.Counter())[phrase] += 1

    folded = {}
    for key, spellings in seen.items():
        display, _ = spellings.most_common(1)[0]
        variants = [s for s in spellings if s != display]
        folded[key] = (display, sum(spellings.values()), variants)
    return folded


def classify(phrase: str) -> tuple[str, list[str], str, str]:
    """Return (best_key, alternate_keys, confidence, note)."""
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for key, weight, pattern in RULES:
        found = re.search(pattern, phrase, re.I)
        if found and weight >= scores.get(key, 0):
            if weight > scores.get(key, 0):
                hits[key] = []
            scores[key] = weight
            hits.setdefault(key, []).append(found.group(0).strip())

    service_only = bool(NON_FAB.search(phrase))

    if not scores:
        note = "no fab capability keyword found; reads as a business service" if service_only \
               else "no fab capability keyword found"
        return "", [], "none", note

    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    best = ranked[0]
    top_weight = scores[best]

    # A business service phrase needs a decisive keyword, not a suggestive one.
    if service_only and top_weight < 3:
        return "", ranked, "none", (
            f"reads as a business service; only weak fab signal "
            f"({', '.join(sorted(set(hits[best])))})"
        )

    confidence = CONFIDENCE_BY_WEIGHT[top_weight]
    note = f"matched {', '.join(sorted(set(hits[best]))[:3])}"
    if service_only:
        note += "; also reads as a business service, worth a look"
    return best, ranked[1:], confidence, note


def load_existing(path: Path | None) -> dict[str, str]:
    """Carry forward Approved Key values from a previous review."""
    if not path or not path.exists():
        return {}
    frame = pd.read_excel(path)
    lookup = {c.lower().strip(): c for c in frame.columns}
    phrase_col = lookup.get("capability phrase")
    approved_col = lookup.get("approved key")
    if not phrase_col or not approved_col:
        print(f"  WARN  {path.name} has no Capability Phrase / Approved Key columns, ignored")
        return {}
    kept = {
        phrase_key(r[phrase_col]): str(r[approved_col]).strip()
        for _, r in frame.iterrows()
        if pd.notna(r.get(phrase_col)) and pd.notna(r.get(approved_col)) and str(r[approved_col]).strip()
    }
    print(f"  carried forward {len(kept)} approved key(s) from {path.name}")
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose the capability mapping sheet.")
    parser.add_argument("--source", required=True, help="path to the master .xlsx")
    parser.add_argument("--out", default="capability_map.xlsx", help="output mapping workbook")
    parser.add_argument("--existing", help="a previous capability_map.xlsx whose Approved Key column to keep")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise SystemExit(f"source workbook not found: {source.resolve()}")

    print(f"Reading {source.name}")
    folded = collect_phrases(source)
    total_uses = sum(n for _, n, _ in folded.values())
    variant_count = sum(len(v) for _, _, v in folded.values())
    print(f"  {len(folded)} distinct phrase(s), {total_uses} total assignment(s)")
    if variant_count:
        print(f"  folded {variant_count} case variant(s) into their main spelling")

    approved = load_existing(Path(args.existing) if args.existing else None)

    rows, tally = [], collections.Counter()
    ordered = sorted(folded.values(), key=lambda item: (-item[1], item[0].lower()))
    for phrase, n, variants in ordered:
        key, alternates, confidence, note = classify(phrase)
        if variants:
            note += f"; also spelled {', '.join(repr(v) for v in variants[:2])}"
        override = approved.get(phrase_key(phrase), "")
        effective = override or key
        tally[confidence if not override else "approved"] += 1
        tally["mapped" if effective else "unmapped"] += 1
        rows.append({
            "Capability Phrase": phrase,
            "Suppliers": n,
            "Suggested Key": key,
            "Suggested Capability": CAPABILITIES.get(key, ""),
            "Alternates": ", ".join(alternates[:3]),
            "Confidence": confidence,
            "Approved Key": override,
            "Notes": note,
        })

    out = Path(args.out)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Capability_Map", index=False)
        pd.DataFrame(
            [{"Key": k, "Capability": v} for k, v in CAPABILITIES.items()]
        ).to_excel(writer, sheet_name="Valid_Keys", index=False)

        sheet = writer.sheets["Capability_Map"]
        for column, width in zip("ABCDEFGH", (62, 10, 14, 44, 20, 12, 14, 56)):
            sheet.column_dimensions[column].width = width
        sheet.freeze_panes = "A2"

    total = len(rows)
    print(f"\nWrote {out}  ({total} rows)")
    print(f"  mapped    {tally['mapped']:4}  ({tally['mapped'] / total * 100:.0f}%)")
    print(f"    high      {tally['high']:4}")
    print(f"    medium    {tally['medium']:4}")
    print(f"    low       {tally['low']:4}")
    print(f"  unmapped  {tally['unmapped']:4}  ({tally['unmapped'] / total * 100:.0f}%)")
    print("\nReview the Approved Key column, then paste the Capability_Map sheet")
    print("into the master workbook as a tab of the same name.")


if __name__ == "__main__":
    main()
