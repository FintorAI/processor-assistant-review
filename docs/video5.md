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
- Current address
    - Unit type is Unit 1313
    - Unit # is 1313 (remove it from Unit type)
    - Climbing ivy dr
- Changed property type to PUD

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