Feedback 5 Notes
Jhonel Suttles
#2605968111

Thread ID: 019f02d0-c57e-71e1-9994-138a0a72a584

Almas Initial Notes:
🧾 File Summary
Client Name: Jhonel & Jonathan Suttles
Property Address: 12859 Climbing Ivy Dr, Germantown, MD 20874
Closing Date: 7/15
AUS Findings: DU

Borrower(s) on Loan: Both
Borrower(s) on Title: Both
Loan Program: FHA MMP 5%

💼 Employment & Income
VOE Contact Email: Jhonel - TWN

Jonathan:
PVOE - admin@starboardtransportation.com
VOE current - nfeinstone@arklineinc.com

Income Details:
Jhonel - Capital One | $6518.39 x 26 / 12 = $14,123.18 Base a month + Bonus 2 year avg $1,366.48
Jonathan - ARK Line | 2-year average $4611.46 base

Dependents: 2
Gabriella Suttles - 6 years old 9/18/19 Female
Aria Suttles - 4 years old 1/12/22 Female
💰 Assets
Source of Assets (Blend, Bank Statements, etc.): Bank Statements
Available Funds: $1131.48
Funds for Settlement: gift funds and seller help 
Gift Funds: $39k - gift letter in efolder already

🧑‍🤝‍🧑 Team Contacts
(image here)


Title Company: Classic Settlements | Andrea Conte | aconte@settlements.com

🏠 Appraisal
Who will pay for the appraisal: Borrower


📝 Additional Notes
Use this section for any special circumstances or key updates:

Seller help is 3% (13,800) 

HOA is 88 a month
Currently under the MMP income limit with your income calc
Clients are making up .5% in agent commission
Requested case # & SSN Verif - Please order appraisal

==========================
Notes wrote by Agent:
(see from thread)
==========================

Almas Notes edited by Ash:

AUS Findings: DU
Loan Program: FHA MMP 5%
Income Details:
Jhonel - Capital One | $6518.39 x 26 / 12 = $14,123.18 
Base a month + Bonus 2 year avg $1,366.48
Jonathan - ARK Line | 2-year average $4611.46 base

Dependents: 2
Gabriella Suttles - 6 years old 9/18/19 Female
Aria Suttles - 4 years old 1/12/22 Female
💰 Assets
Source of Assets (Blend, Bank Statements, etc.): Bank Statements
Available Funds: $1131.48
Funds for Settlement: gift funds and seller help 
Gift Funds: $39k - gift letter in efolder already

Currently under the MMP income limit with your income calc
Clients are making up .5% in agent commission
Requested case # & SSN Verif - Please order appraisal

Cover Letter
- Removed File Summary, Team Contacts, Appraisal, Additional Notes
    - FIX (DONE): draft_cover_letter._strip_boilerplate is now section-aware and
      emoji-aware. A new _norm_header() normalizes each line (drops leading emoji /
      symbols + trailing ':') so headers match regardless of the emoji prefix. The
      File Summary, Team Contacts, Appraisal, and Additional Notes sections are
      dropped wholesale (header + content until the next section). Exceptions:
      AUS Findings / Loan Program are salvaged out of File Summary; the
      Employment & Income header label is dropped but its content kept; the
      💰 Assets header is kept as a divider. Identifier prefixes (client name,
      property address, closing date, borrowers, VOE contact email, "Use this
      section…") are still stripped from the kept sections.
    - NOTE: the literal feedback ("remove Additional Notes") is honored, so the
      few Additional-Notes lines Ash kept by hand (MMP income limit, agent
      commission, case #) are dropped. A per-section keep-list (like the File
      Summary AUS/Loan Program salvage) can re-add specific lines if desired.
- Don’t put in UW notes (OCR notes)
    - FIX (DONE): removed the block in draft_cover_letter.py that appended the
      OCR'd Almas-image transcription verbatim into CX.KM.SUBMISSION.NOTES. The
      image DocRepo references are still attached to the 8.1 flag for traceability,
      and the contact info those images carry is handled separately by
      review_file_contacts.
- Escrow Company -> Case # 
    - Wasn’t able to write
    - FIX (DONE): review_file_contacts now pulls the settlement statement "File #"
      (added ESS schema field `contact_settlement_agent_file_number` +
      ess_contact_bypass injection) and writes it to the Escrow Company's
      Escrow Case # via the File Contacts API `referenceNumber`. Verified in the
      Test instance that `referenceNumber` == Encompass loan field 186 (same
      field). Emits an info-overwrite flag.
    - Related cleanup: field 186 was mislabeled as "EMD Amount" in the field
      registry. EMD now maps to `URLAROA0103`; field 186 → `escrow_case_number`.
      (definitions/step_06, step_01 YAML + data_gathering FIELD_MAP + factory-reset.)
- Seller 1 and Seller 2
    - Address
    - City
    - State
    - Zip
    - Just something has to be filled out
    - FIX (DONE): review_file_contacts._sync_seller_addresses writes the subject
      property address (LOS fields 11/12/14/15) to the SELLER / SELLER2 file
      contacts, split into address/city/state/postalCode, with an info-overwrite
      flag. Only enriches seller contacts that ALREADY exist — never fabricates a
      Seller 1/2 (a genuinely missing seller still raises the missing-contact flag).

File Contacts — write-quality fixes (Buyer's/Seller's Agent + Escrow, per feedback)
- FIX (DONE): full address strings are split into address/city/state/postalCode
  instead of being crammed into the `address` field (_split_address).
- FIX (DONE): smart comparison reconstructs the existing contact's full address
  and compares it (normalized — Blvd↔Boulevard, Ste↔Suite, punctuation) against
  the doc's full address, so formatting-only differences no longer trigger an
  overwrite; only genuine field discrepancies are written/flagged.
- FIX (DONE): every contact-write flag now enumerates the written field(s) as a
  bullet list (Address / City / State / ZIP / Company License # / Phone / …)
  instead of a single inline string.

Borrower summary origination
- Driver's License present but expiry date could not be read.
    - Could not read Government ID?
    - verify if we were able to read this
    - INVESTIGATION: The IDs *were* read. The eFolder has 2 Driver's License
      copies (Jhonel + Jonathan), both extracted successfully — but the
      extraction service returned only a raw `document_content` OCR blob
      (source `efolderDirectProcess`) instead of the structured schema fields.
      The expiry is in that blob (Jhonel `Date of exp 01/22/2034`,
      Jonathan `06/22/2034`), but `dl_expiry` was never populated, so
      run_pre_checks (1.1) raised "ID Expiry Unknown".
    - ROOT CAUSE: not a missing schema. The live catchingDoc "Driver's License"
      schema already defines dl_expiry / dl_name / license_number / etc. with
      good descriptions. The runtime `/efolder/direct` extraction simply did not
      apply it and fell back to a content dump. The proper long-term fix is
      server-side (LG-docsOrch extraction Lambda must apply the DL schema).
    - FIX (DONE, client-side fallback): data_gathering._normalize_efolder_output
      now detects when an ID doc (Driver's License / Passport / Permanent
      Resident Card) returns only `document_content` and parses dl_expiry,
      borrower_dob, dl_name, dl_borrower_name, and dl_present out of the OCR text
      (`_parse_id_document_content`, confidence 0.6 so a real schema extraction
      still wins). Also hardened run_pre_checks to parse MM/DD/YYYY (state-ID
      format) in addition to ISO, so a populated expiry is no longer misread as
      "unknown".
    - FIX (DONE, Government ID write): the same content parser now also extracts
      the Government ID number (the "Customer identifier", e.g. `MD-10272427156`
      → `10272427156`, state prefix stripped) as `dl_gov_id` and the ID type
      (`DL` for a Driver's License) as `dl_gov_id_type`. review_borrower_summary
      maps each license copy to the right person by name and writes Government ID
      / Government ID Type for the borrower (fields 5053 / 5055) and co-borrower
      (5054 / 5056), with an info-overwrite flag. Registered dl_gov_id /
      dl_gov_id_type on the DL doc and 5053–5056 in FIELD_MAP + step_02 YAML.
- Current address and former address
    - If Unit # says "Unit 1313", remove "Unit" and should only be 1313 
    - Unit type then should be "Unit"
    - FIX (DONE): review_urla_page1 (4.1) normalizes the Unit # / Unit Type for
      borrower + co-borrower, current + former (FR0125/FR0127, FR0225/FR0227,
      FR0325/FR0327, FR0425/FR0427). A designator glued onto the Unit #
      ("Unit 1313") is split — Unit # becomes `1313`, Unit Type becomes `Unit`
      (also handles Apt/Suite/Bldg/… and `#1313`); a bare identifier is left as-is.
- Subject property information
    - Googled the address, looked at the pic, looks like its attached because it has an apartment attached to it, but it also had HOA dues, so changed property type to PUD
    - FEASIBILITY: not via a web "Google" search (no internal address-search API;
      external places APIs can't authoritatively classify PUD). Reliable
      programmatic paths instead: (a) extract "Project Type" from the Appraisal
      (Form 1004) — authoritative; or (b) heuristic flag from HOA dues present
      (field 233) + property_type/attachment `Attached` + project type not Condo.
      Flag-to-verify, not auto-write, since misclassifying property type affects
      pricing/eligibility.
    - FIX (DONE, path c = a + b + external Zillow): update_transmittal_summary
      (10.1) now runs PUD detection on non-condo properties. Signals:
        (a) document-backed — appraisal `appraisal_project_type` (URAR Project Type
            checkbox) indicates PUD (authoritative);
        (b) heuristic — HOA dues present (field 233, now read into state) +
            Attached dwelling (CX.ATTACHMENT.TYPE or property_type);
        (c) external — live Zillow lookup via HasData (shared/zillow_client.py):
            home/structure type (Townhouse/Condo/…), `hasAttachedProperty`, and
            HOA dues parsed from `atAGlanceFacts`. This AUTOMATES the manual
            "Go to Zillow" check. Best-effort: needs HASDATA_API_KEY (in .env);
            on any error/disabled it silently falls back to (a)+(b) + a deep-link.
      When any signal is present it SKIPS the old "Not in a Project" 1012 auto-write
      and raises a `Possible PUD — Verify Property / Project Type` flag (warning if
      the appraisal says PUD, HOA+Attached agree, or 2+ Zillow signals; else info).
      The flag carries the real Zillow listing URL (or an address deep-link
      fallback) and recommends setting Property Type (1041) = PUD and Project Type
      (1012) = "Other: P/PUD".
    - Zillow client uses HasData's Zillow Listing API
      (`https://api.hasdata.com/scrape/zillow/listing`, keyword=full address) — raw
      requests+BeautifulSoup against zillow.com gets 403'd; HasData handles proxy
      rotation / CAPTCHA. Pass the FULL address (street, city, state, ZIP); a
      partial/typo'd address returns a search-results page (no single property).
    - Verified live on the real subject 12859 Climbing Ivy Dr, Germantown, MD
      20874 → home_type=TOWNHOUSE, structure=End of Row/Townhouse, HOA=$88/mo →
      PUD detected (2 signals → warning). Control: 5548 Daffodil Dr →
      SINGLE_FAMILY, Ranch/Rambler, no HOA → correctly NO PUD signal.
    - IMPORTANT FINDING: Zillow's `hasAttachedProperty` boolean is UNRELIABLE — it
      returned False for the Climbing Ivy end-of-row townhouse that obviously
      shares a wall. So detection leans on `home_type`/`structure_type`
      (townhouse/row/duplex/twin/condo/…) + HOA dues, NOT on that boolean (the
      boolean is still used as a bonus signal when True, never to suppress).
    - Registered field 233 (HOA dues) in FIELD_MAP + step_10 YAML; added an
      `appraisal` doc bucket (read-side, `appraisal_project_type`) to
      required_docs.json. NOTE: the appraisal Project Type field still needs the
      server-side catchingDoc Appraisal schema to populate it; until then path (a)
      is dormant and (b)+(c) carry detection.
    - NO auto-write of property/project type — deliberately flag-only.

1003 URLA P1
- Copies work phone number from part 2 to this
- Income Calculation Worksheet

1003 URLA P3
- Retirement Funds
    - Since FHA loan, they only use 60% of value (262… x 0.6)

2015 Itemization 
- Doesn’t really need any money for closing (negative CTC)

FHA Management
- CAIVRS # is extracted from CAIVRS document in efolder for borrower and co-borrower
- Case number
    - FHA Government Documents -> Case Number (special code, 703 since its a regular property)

HUD Transmittal
- Underwriter normally fills this out
- Source / EIN = MMP /52
    - Gov’t 
- Case number + ADP code

Transmittal Summary
- Project type == Other: PUD, Property Type == PUD
- Project name 
- Go to Zillow (Germantown view)
    - FIX (DONE): handled by the same PUD-detection rule in
      update_transmittal_summary (10.1) — see "Subject property information" above.
      The flag recommends Project Type (1012) = "Other: P/PUD" + Property Type
      (1041) = PUD, and embeds the Zillow deep-link for the address. Project Name
      (CX.CONDO.PROJECT.NAME) stays a CUA/browser lookup (cannot be derived here).


For FHA loans, bucket is HUD .. Transmittal (verify in efolder)

- Run AUS
    - Button on Fintor that runs these
    - 95% are Fannie Mae

- Make mark as ready for UW button configurable
	- Retirement statement (Fidelity) / terms of withdrawal

- Hit finished on the In Processing / Submitted today loan

- Homebuyers education cert
- DPA registration
- DPA approval