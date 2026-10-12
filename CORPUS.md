# CORPUS — the licence manifest

> **How to use this file.** This is the `corpus` node's gate artifact: every PDF on disk, where it came from, under what licence, and the sha256 that proves the bytes have not drifted. The PDFs themselves never enter git (`corpus/` is ignored); this file does. Verify with `shasum -a 256 -c corpus.sha256` from the repo root.

**Acquired 2026-10-07.** 18 documents · **3,939 pp** · ~453 MB on disk. Corpus frozen for the demo (D11): additions and removals get recorded here, no silent edits.

## Layer A — vendored (redistributable)

| file (`corpus/vendored/…`) | document | source | licence | pp | bytes | sha256 |
|---|---|---|---|---|---|---|
| `faa/phak_faa-h-8083-25c.pdf` | Pilot's Handbook of Aeronautical Knowledge, FAA-H-8083-25C (2023) | [faa.gov](https://www.faa.gov/regulations_policies/handbooks_manuals/aviation/faa-h-8083-25c.pdf) | US Gov work — public domain | 522 | 77598564 | `247929cace0ab56b376e683eba540cc4c8f39f199ab35414e8b604e24f395cb7` |
| `faa/afh_faa-h-8083-3c.pdf` | Airplane Flying Handbook, FAA-H-8083-3C (2021) | [faa.gov](https://www.faa.gov/sites/faa.gov/files/regulations_policies/handbooks_manuals/aviation/airplane_handbook/00_afh_full.pdf) | US Gov work — public domain | 406 | 273248888 | `90dda95dcbe992bd798f1932b02b5a02d620d8344565c0516a6917f29dea1f8d` |
| `faa/aim_2026-07-09.pdf` | Aeronautical Information Manual, Basic w/ Chg 1–3, eff. 2026-07-09 | [faa.gov](https://www.faa.gov/air_traffic/publications/media/AIM_Basic_w_Chg_1_and_2_and_3_dtd_7-9-26_FINAL.pdf) | US Gov work — public domain | 918 | 43343724 | `8b11e7a168e1ef9ea16853b8ab7b3353f01490dffb51f2736a0a72e6278f8ff5` |
| `faa/fsb_b737_rev17_2020-11-16.pdf` | Flight Standardization Board Report, Boeing 737, Rev 17 (2020-11-16) | [faa.gov](https://www.faa.gov/sites/faa.gov/files/2022-08/737_FSB_Report.pdf) | US Gov work — public domain | 67 | 436618 | `657d8269ec603671976b9a8aa51370db8874a701055e05021cf1117965596aa6` |
| `ntsb/aar9901_usair427_b737.pdf` | NTSB/AAR-99/01 — USAir 427, B737-3B7, 1994 | [ntsb.gov](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR9901.pdf) | US Gov work — public domain | 370 | 3171457 | `c22adf6abb7d154b9f5167051735d6a134e9b6fe4e66a87cff1e4fe78694aa19` |
| `ntsb/aar0706_southwest1248_b737.pdf` | NTSB/AAR-07/06 — Southwest 1248, B737-7H4, Midway overrun, 2005 | [ntsb.gov](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR0706.pdf) | US Gov work — public domain | 248 | 3820071 | `e4a11354f29de4c4e16ab0c30527d2d8bd6c4b8fab33f9b7379bbee74d7fa501` |
| `ntsb/aar1003_usairways1549_a320.pdf` | NTSB/AAR-10/03 — US Airways 1549, A320-214, Hudson, 2009 | [ntsb.gov](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR1003.pdf) | US Gov work — public domain | 213 | 5994104 | `fe9e1733e8400f3ea9ce0832f36d934c829ac369e8a66765dc423d18973640ef` |
| `ntsb/aar1004_continental1404_b737.pdf` | NTSB/AAR-10/04 — Continental 1404, B737-500, Denver, 2008 | [ntsb.gov](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR1004.pdf) | US Gov work — public domain | 117 | 1677976 | `85d357998c5203bfc1020ce883ca32e82df19ed15ddc1e8318389fa221977595` |
| `ntsb/aar1903_southwest1380_b737.pdf` | NTSB/AAR-19/03 — Southwest 1380, B737-7H4, engine failure, 2018 | [ntsb.gov](https://www.ntsb.gov/investigations/AccidentReports/Reports/AAR1903.pdf) | US Gov work — public domain | 193 | 6920369 | `ca9d64cb9bc77178d61aec5ea4303a541711b5583d306f095039c8df50933dd3` |
| `aaib/aaib_1-2025_b737-8200_ei-het.pdf` | AAIB 1/2025 — B737-8200 (MAX), go-around level bust, Stansted, 2023 | [gov.uk](https://assets.publishing.service.gov.uk/media/67372436cb0e41c0dba0f737/Boeing_737-8200_EI-HET_1-25.pdf) | © Crown, OGL v3.0 — attribution required | 18 | 3209092 | `d0d57e8efb273fde9acf1f2f82e6bfa1bc7fbf587e8174d91cb310cc20816b14` |
| `aaib/aaib_9-2020_a320-214_oe-loa.pdf` | AAIB 9/2020 — A320-214, contained engine failure + evacuation, Stansted, 2019 | [gov.uk](https://assets.publishing.service.gov.uk/media/5f21754ad3bf7f1b0fa79fc7/Airbus_A320-214_OE-LOA_09-20.pdf) | © Crown, OGL v3.0 — attribution required | 33 | 1548556 | `90f045bd2dcb44bc6dc99d11595f54946dda8d057f6e8b23a31121c2d8befd07` |
| `airbus-safety-first/gnss-interference.pdf` | GNSS Interference (Apr 2026) | [airbus.com](https://mms-safetyfirst.s3.eu-west-3.amazonaws.com/pdf/safety+first/gnss-interference.pdf) | © Airbus — reprint with acknowledgement | 12 | 2063504 | `4fbe1a0a69fe549da78993e368d833c097e5ecc8e0e9d93e767146074726c8e0` |
| `airbus-safety-first/preventing-inadvertent-slide-deployments.pdf` | Preventing Inadvertent Slide Deployments (Sep 2026) | [airbus.com](https://mms-safetyfirst.s3.eu-west-3.amazonaws.com/pdf/safety+first/preventing-inadvertent-slide-deployments.pdf) | © Airbus — reprint with acknowledgement | 13 | 8872540 | `cdb79e669b66a4a871211b7342c9a54b70c9b4fa90ef9b68a46b6e94042fc8b7` |
| `airbus-safety-first/proper-ewis-maintenance-for-safe-operations.pdf` | Proper EWIS Maintenance for Safe Operations (Jul 2026) | [airbus.com](https://mms-safetyfirst.s3.eu-west-3.amazonaws.com/pdf/safety+first/proper-ewis-maintenance-for-safe-operations.pdf) | © Airbus — reprint with acknowledgement | 11 | 5045323 | `269d70cd46f7e00e1f6da1e1f029c3f183f9f11e6f0ef010b8544a7108378e5b` |
| `airbus-safety-first/use-of-non-certified-equipment-on-cockpit-windows.pdf` | Use of Non-Certified Equipment on Cockpit Windows (May 2026) | [airbus.com](https://mms-safetyfirst.s3.eu-west-3.amazonaws.com/pdf/safety+first/use-of-non-certified-equipment-on-cockpit-windows.pdf) | © Airbus — reprint with acknowledgement | 9 | 4234953 | `58d55e2332519a1eaeca389f42622d082b3ae8220ec2943ee8452be84b8bc672` |
| `airbus-safety-first/safe-aircraft-parking.pdf` | Safe Aircraft Parking (Jun 2020) | [airbus.com](https://mms-safetyfirst.s3.eu-west-3.amazonaws.com/pdf/safety+first/safe-aircraft-parking.pdf) | © Airbus — reprint with acknowledgement | 10 | 2248177 | `429761d9da1ad3562fff1475520109663f02758aeb34d8545a89bc750d365dda` |

## Layer B — link-only (© OEM; local copy only — never committed, never reposted)

| file (`corpus/link-only/…`) | document | source | licence | pp | bytes | sha256 |
|---|---|---|---|---|---|---|
| `boeing/b737_mmel_rev63.pdf` | Boeing 737 MMEL, Rev 63 | [boeing.com](https://www.boeing.com/content/dam/boeing/boeingdotcom/commercial/mmel/B737_MMEL_Rev_63_signed.pdf) | © Boeing — free download; site terms forbid posting on any network | 333 | 3399785 | `3485b668a18885e94cc03ad02fe6ec34f24079d73208ad25d40bab269d135480` |
| `airbus/a320_mmel_rev30_draft.pdf` | Airbus A320 MMEL, Rev 30 (**draft**), FAA-hosted | [faa.gov](https://www.faa.gov/sites/faa.gov/files/aircraft/draft_docs/mmel/MMEL_A-320_Rev_30_Draft.pdf) | © Airbus; EASA will not disclose OSD MMEL to third parties | 446 | 3710064 | `30b24d3b67c2186605d6002eaa928e9ac36852157590017a5338eab04892d0d5` |

## Document status notes

- FSB 737 = **Revision 17** (2020-11-16), the latest **final**. A Rev 22 **draft** exists at `faa.gov/aircraft/draft_docs/fsb/` — not used.
- The A320 MMEL is a **draft** revision (the FAA publishes draft MMELs; final revs are not public).
- AIM edition = Basic with Changes 1–3, effective 2026-07-09.
- AAIB documents: EI-HET = report 1/2025; OE-LOA = report 9/2020.

## Deliberately not included (and why)

- **Boeing ACAP / airport-planning docs** — dimension and weight tables, no relationships: `CORPUS.md` records them as the worst graph material (D14).
- **TCDS (FAA A16WE, EASA A.064)** — mostly tables; deferred, can be pulled from FAA DRS if wanted.
- **Airworthiness Directives** — many small documents; deferred.
- **NASA ASRS** — the Kaggle dump needs account auth; deferred.
- **USAF AFMAN 11-2X Vol 3** — the host 403s automated fetches; needs a browser download.
- **More NTSB / AAIB / Safety First** — e.g. the AAIB Titan A321-253NX (G-OATW) final report and ~30 further Safety First articles exist upstream. Add only if the dev slice shows the set is thin where it hurts.

## Dev slice v1 (2026-10-07) — 508 pp, 12 files

Cut from the frozen files for pipeline development and extraction-cost measurement. The demo and the eval set run on the **full** corpus — slice metrics are never results. Whole documents are byte-identical copies (`cp`; hash equals the source's); page ranges are carved with pypdf, so **slice physical page 1 = the source physical page named in the file** (printed labels in the table). Every file inherits its source's licence layer.

| slice file (`corpus/slice/…`) | source | source range | printed | pp | sha256 |
|---|---|---|---|---|---|
| `vendored/faa/fsb_b737_rev17_2020-11-16.pdf` | FSB 737 Rev 17 | whole | 1–67 | 67 | `657d8269ec603671976b9a8aa51370db8874a701055e05021cf1117965596aa6` |
| `vendored/ntsb/aar1903_southwest1380_b737.pdf` | NTSB AAR-19/03 | whole | 1–193 | 193 | `ca9d64cb9bc77178d61aec5ea4303a541711b5583d306f095039c8df50933dd3` |
| `vendored/ntsb/aar1004_continental1404_b737.pdf` | NTSB AAR-10/04 | whole | 1–117 | 117 | `85d357998c5203bfc1020ce883ca32e82df19ed15ddc1e8318389fa221977595` |
| `vendored/aaib/aaib_9-2020_a320-214_oe-loa.pdf` | AAIB 9/2020 | whole | 1–33 | 33 | `90f045bd2dcb44bc6dc99d11595f54946dda8d057f6e8b23a31121c2d8befd07` |
| `vendored/aaib/aaib_1-2025_b737-8200_ei-het.pdf` | AAIB 1/2025 | whole | 1–18 | 18 | `d0d57e8efb273fde9acf1f2f82e6bfa1bc7fbf587e8174d91cb310cc20816b14` |
| `vendored/airbus-safety-first/gnss-interference.pdf` | Safety First | whole | 1–12 | 12 | `4fbe1a0a69fe549da78993e368d833c097e5ecc8e0e9d93e767146074726c8e0` |
| `vendored/airbus-safety-first/proper-ewis-maintenance-for-safe-operations.pdf` | Safety First | whole | 1–11 | 11 | `269d70cd46f7e00e1f6da1e1f029c3f183f9f11e6f0ef010b8544a7108378e5b` |
| `vendored/airbus-safety-first/safe-aircraft-parking.pdf` | Safety First | whole | 1–10 | 10 | `429761d9da1ad3562fff1475520109663f02758aeb34d8545a89bc750d365dda` |
| `link-only/airbus/a320_mmel_rev30_draft__src-p157-174.pdf` | A320 MMEL | 157–174 | 26-1…26-18 | 18 | `3f1b0bae0cd994a487ac18de48d198983712f69ddf7583ca3c8f5669f0a295ec` |
| `link-only/airbus/a320_mmel_rev30_draft__src-p248-254.pdf` | A320 MMEL | 248–254 | 29-1…29-7 | 7 | `0969c2e2c8b7e4d82174e33ad298291ef23dfed98b510784151baa281100067b` |
| `link-only/boeing/b737_mmel_rev63__src-p147-164.pdf` | 737 MMEL | 147–164 | 26-1…26-18 | 18 | `d1b3b00bb2ec33dcb17251491787ff487c400d08bd9c9da47b9f9c7ec93ceb58` |
| `link-only/boeing/b737_mmel_rev63__src-p206-209.pdf` | 737 MMEL | 206–209 | 29-1…29-4 | 4 | `633c2e507b65b46ea1b2c2482fca408221ae3d750cf494f333b86dc6a452718a` |

Notes:
- Deliberately excluded: PHAK ch. 7 (and all handbooks) — handbooks get parse-only smoke tests; parsing is CPU, and extraction-cost-per-class is measured separately.
- Composition is report-leaning on purpose (diversity of failure modes + cross-document entity overlap); it is not a proportional mini-corpus.
- The slice is fixed once; changes go through this section, never by editing files in place.

## Verify

```
shasum -a 256 -c corpus.sha256     # 18 × OK — the corpus
shasum -a 256 -c slice.sha256      # 12 × OK — dev slice v1
```
