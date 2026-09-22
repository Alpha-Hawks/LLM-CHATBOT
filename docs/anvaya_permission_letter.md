# Request for an Official Anvaya Integration for the Student AI Academic Assistant

**Date:** [Insert Submission Date]  
**From:**  
Project Team — Academic Advising Chatbot  
Department of Computer Science and Engineering,  
Marri Laxman Reddy Institute of Technology and Management (MLRITM),  
Dundigal, Hyderabad - 500043.

**To:**  
The Principal, MLRITM  
The System Administrator & Online Services, MLRITM  
(through the ERP (E-Governance) Committee)

**Copy to:** Dean IQAC; Head of the Department concerned; Controller of Examinations

**Subject: Request for an official, authorized integration between Anvaya (ORGMAKER ERP) and the Student AI Academic Assistant**

Respected Sir/Madam,

We are developing the **MLRITM AI Academic Assistant** as our capstone project. It answers questions on the autonomous regulations (MLR20/MLR22), fees, scholarships and the academic calendar. We would also like it to answer a student's questions about **their own** attendance, results and timetable when they open it from Anvaya (`https://anvaya.mlritm.ac.in/`).

We will only do this through a mechanism that MLRITM and ORGMAKER (Hilip Technologies) officially provide. The assistant **does not and will not**:
- ask for, receive or store any student's Anvaya password;
- read, scrape or automate Anvaya's web pages;
- use any undocumented or hidden interface;
- let a student choose or enter another student's identity.

We checked public sources and found no published integration method for ORGMAKER (details in the attached research note). We therefore request the following.

### 1. Student sign-in (identity) — in order of preference
1. **OpenID Connect (or SAML 2.0) single sign-on,** with Anvaya/ORGMAKER as the identity provider. We would need the issuer/metadata URL, a client registration, an approved redirect URI, and the list of claims issued (a stable student identifier; roll number and name if available).
2. **A signed launch link:** an "AI Assistant" menu item in Anvaya that opens the assistant with a short-lived, signed token (JWT). We would need the signing algorithm, the verification key (a public key or JWKS URL is preferred), and the claims included (issuer, audience, subject, issue/expiry time, unique token ID).

### 2. Student records — read-only
- A **server-to-server, read-only API** for attendance, results and timetable. Ideally each call is scoped to the signed-in student. If only institution-level credentials can be issued, the assistant enforces per-student authorization in its backend and requests only the signed-in student's records.

### 3. Information we need from MLRITM / ORGMAKER
- Which of the above mechanisms ORGMAKER supports, and its integration/administration documentation.
- The authoritative, unchanging student identifier, and how roll-number changes, lateral entries and detained students are handled.
- Whether PG students (`anvaya.pg.mlritm.ac.in`) use a separate system.
- Embedding constraints (iframe/CSP, cookies) if the assistant is shown inside Anvaya.
- Session rules: lifetime, and what happens on logout or when a student is deactivated.
- Test (sandbox) credentials, data fields available, rate limits and data freshness.
- MLRITM's data-governance conditions: written approval, data-processing terms, DPDP Act 2023 notice/consent and retention, audit logging, and whether record data may be sent to an external AI model.

### Safeguards already built into the assistant
1. **Anvaya-issued identity only.** Every sign-in is verified: signature, issuer, audience, expiry, and single use. No passwords are involved.
2. **Backend authorization.** Records are loaded only for the signed-in student, and each record's owner is checked before it is shown. The AI model never decides who may see what.
3. **Explicit DPDP Act 2023 consent** before any personal record is shown, recorded with a timestamp.
4. **Short, revocable sessions.** Session tokens are random, stored only as hashes, expire after 20 minutes and are revoked on sign-out.
5. **Off by default.** Until MLRITM enables an official mechanism, sign-in and personal records remain disabled.

We would be grateful if the College could forward this request to the ERP vendor, Hilip Technologies (ORGMAKER), and let us demonstrate the system and its security design to the ERP Committee.

Thank you for your support.

Yours faithfully,

**Project Team Members:**
- [Student Name 1] — Roll No: [Roll Number 1]
- [Student Name 2] — Roll No: [Roll Number 2]
- [Student Name 3] — Roll No: [Roll Number 3]

**Project Guide:**  
[Faculty Name], [Designation], Department of CSE, MLRITM

**Head of Department:**  
Head, Department of Computer Science and Engineering, MLRITM

**Attachment:** Anvaya integration research note (public sources)
