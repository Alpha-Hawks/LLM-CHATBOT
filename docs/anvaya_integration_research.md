# Anvaya Integration Research (Public Sources)

**Checked:** 20 September 2026  
**Question:** What official mechanism does Anvaya (MLRITM's ERP) support for letting a third-party application identify a signed-in student and read that student's records?

## Method

- Public sources only: web search, the vendor's public pages, public app-store and marketplace listings, and public MLRITM pages.
- Anvaya itself was not probed. The only request to `anvaya.mlritm.ac.in` loaded its public login page. No URLs were guessed, no logins were attempted, and no apps or network traffic were inspected.
- A second reviewer re-opened every cited source to confirm the quotes.

## What Anvaya is

The login page identifies the system as **ORGMAKER ERP** by **Hilip Technologies** (ASP.NET MVC on IIS). The page has a username/password form, a "Forgot Password / Unlock Account" link and **no "Sign in with …" / SSO option**.

## Findings

| Mechanism | Status | Evidence |
|---|---|---|
| SSO (SAML 2.0) | Not found | No vendor, listing or MLRITM page mentions it |
| OAuth 2.0 / OpenID Connect | Not found | No mention anywhere public |
| Google / Microsoft sign-in | Not found | MLRITM portal cards link only to plain login pages |
| Signed launch token / secure launch URL | Not found | No mention anywhere public |
| Server-to-server API for third parties | Not found | No API documentation, developer portal or SDK |
| Embedding / plugins / marketplace | Not found | — |
| LTI / LMS integration | Not found | MLRITM describes Anvaya's LMS-like functions only |
| Webhooks / data export | Not found | — |
| Vendor-internal "authorized APIs" | Mentioned only | Hilip's privacy policy for its own *ORGMaker - Student* app says data is "fetched from your institution's systems through authorized APIs". This describes the vendor's own app. It gives no specification and makes no offer to third parties ([hilip.net/Home/PrivacyPolicy](https://hilip.net/Home/PrivacyPolicy)) |
| ORGMaker - Student Android app | Mentioned only | A first-party app by Hilip. The Play listing states "No data shared with third parties". It is not an integration route |

The vendor site `orgmaker.com` returned HTTP 403 during the check, so no product documentation there could be read.

**Conclusion:** no official integration mechanism for Anvaya is publicly documented. It must be requested from MLRITM, which is the data owner and the vendor's licensed customer.

## Who owns this at MLRITM (from public pages)

- **ERP (E-Governance) Committee:** "Ensure data privacy, automation, and system integration" ([institute-level committees](https://mlritm.ac.in/institute-level-committees)). No members are published.
- **Dean IQAC:** "Ensure LMS–ERP–Website integration, data integrity, cybersecurity…" ([Dean IQAC](https://www.mlritm.ac.in/Dean_IQAC)).
- Official role addresses listed in the [MLRITM phone directory](https://mlritm.ac.in/Phone_Directory):
  - Principal: principal@mlritm.ac.in
  - Dean IQAC: deaniqac@mlritm.ac.in
  - Controller of Examinations: coe@mlritm.ac.in
  - System Administrator & Online Services: the named contact in the directory

## Vendor contacts (for MLRITM to use)

- Hilip Technologies / ORGMAKER support: support@orgmaker.com, support@hilip.net ([Play listing](https://play.google.com/store/apps/details?id=com.hilip.studentapp.studentapp), [privacy policy](https://hilip.net/Home/PrivacyPolicy))
- Hilip Technologies contact page: technologies@hilip.net ([hilip.net/Contact](https://hilip.net/Contact))

## Open questions for MLRITM / ORGMAKER

1. Can ORGMAKER act as an OpenID Connect or SAML identity provider? If so: metadata URL, client registration, redirect-URI rules, key rotation, and which claims it issues.
2. Can Anvaya open an external app with a short-lived, signed, audience-bound assertion (a signed launch link)? If so: algorithm, claims, expiry, replay protection, and how the verification key is distributed.
3. Is a read-only, server-to-server student-data API available to approved third parties? If so: authentication, per-student scoping, fields (attendance, marks/results, timetable), rate limits, freshness, and a test tenant.
4. What is the authoritative, unchanging student identifier?
5. Is the PG portal (`anvaya.pg.mlritm.ac.in`) a separate system?
6. What are the embedding constraints (iframe/CSP, cookies) and session rules (lifetime, logout, deactivation)?
7. Does MLRITM's contract with Hilip allow third-party integration, and what are the data-governance conditions (approval, DPDP Act 2023 obligations, audit, use of external AI models)?

## How the chatbot is prepared

The chatbot ships with sign-in and personal records **disabled**. It can accept either standard mechanism, OpenID Connect or a signed launch token, as soon as MLRITM supplies the configuration. It also has an interface where an official data API can be plugged in. See the README section "Anvaya-Authenticated Student Access".
