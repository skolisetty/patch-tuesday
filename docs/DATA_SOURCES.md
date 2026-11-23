# Data sources, notices and licences

Everything the game shows comes from public data, downloaded once by `scripts/pull_data.sh` on
2026-11-23 and replayed from that offline copy. No exploit code, payloads, reference URLs or CVE
description text are stored in any file the game reads.

| Source | What it gives the project |
|---|---|
| NVD | The list of CVEs (publicly listed software vulnerabilities) with severity and affected product |
| CISA KEV | Which CVEs are known exploited, and the date each was listed |
| FIRST EPSS | The EPSS baseline score |
| MITRE ATT&CK, CAPEC, CTID mappings | The attack technique shown on each card |

## Sources and their terms

- **NVD** (NIST National Vulnerability Database), CVE 2.0 JSON year feeds 2020-2026.
  *This product uses data from the NVD API but is not endorsed or certified by the NVD.*
- **CISA Known Exploited Vulnerabilities Catalog**, catalogVersion 2026.11.20. CC0 1.0, see
  [`LICENSES/KEV-license.txt`](../LICENSES/KEV-license.txt). Use of the data does not imply
  endorsement by CISA or DHS.
- **EPSS** daily scores, model v2025.03.14, Sunday files 2025-03-23 to 2026-06-14, from FIRST.org
  (Forum of Incident Response and Security Teams), the Exploit Prediction Scoring System,
  https://www.first.org/epss. Attribution is requested by FIRST. `scripts/pull_data.sh` downloads 65
  EPSS files; the day-0 world reads the 53 dated on or before 2026-03-22.
- **MITRE ATT&CK** Enterprise 20.1 and **MITRE CAPEC** 2.1 STIX.
  "© 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of The
  MITRE Corporation." The ATT&CK licence and disclaimers are reproduced in
  [`LICENSES/ATTACK-LICENSE.txt`](../LICENSES/ATTACK-LICENSE.txt).
  CAPEC is used under MITRE's CAPEC terms of use;
  [`LICENSES/CAPEC-TERMS.txt`](../LICENSES/CAPEC-TERMS.txt) quotes the copyright statement the CAPEC
  STIX data carries and points to the terms. `scripts/pull_data.sh` also fetches the licence of the
  mitre/cti repository that distributes it, to `LICENSES/MITRE-CTI-LICENSE.txt`.
- **Center for Threat-Informed Defense, Mappings Explorer**: KEV (2025-07-28) to ATT&CK 16.1
  mappings, Apache License 2.0, text in
  [`LICENSES/CTID-MAPPINGS-APACHE-2.0.txt`](../LICENSES/CTID-MAPPINGS-APACHE-2.0.txt). Only ids,
  names and mapping types are kept.
- **CWE weakness-class labels** in `landing/cwe_names.csv` are short labels written for this
  project, keyed by CWE id.
- **RobotExpressive.glb** (the game character) is CC0, from the three.js examples.

## What is kept, and what never is

- From NVD: ids, dates, CVSS, CWE, vendor and product. The description text is never stored, only
  7 keyword flags derived from it.
- From the CTID mappings: only ids, names and mapping types.
- A card in the game shows only vendor, product, weakness class, CVSS, scores, KEV status and an
  ATT&CK technique id.

## Limits that come from the data

- The NVD year feeds are organised by CVE-ID year, so CVEs with pre-2020 ids are not candidates even
  if published later. KEV history counts still include every KEV entry.
- NVD records are as of the download date, not as of each simulated week. What that means for the
  model is in [MODEL_AND_GATE.md](MODEL_AND_GATE.md#residual-leak-nvd-enrichment).
- The replay range, 2026-03-22 to 2026-06-14, is fixed by the downloaded EPSS files.
