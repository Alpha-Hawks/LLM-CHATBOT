# MLRITM Anvaya Portal & Chatbot Integration Specification

**Target Audience:** MLRITM IT Department & OrgMaker / Hilip ERP Vendor  
**Document Version:** 1.0  
**Compliance Standard:** India Digital Personal Data Protection (DPDP) Act, 2023  
**Status:** Ready for Vendor Review  

---

## 1. Executive Summary

This document specifies the technical integration contract between the **MLRITM Official Anvaya ERP Portal** (`https://anvaya.mlritm.ac.in/App`) and the **MLRITM Autonomous Student AI Advising Assistant**.

The integration enables students logged into Anvaya to view their current academic details (**attendance, internal marks, semester results, fee status, class timetable, and exam schedules**) directly through their chatbot assistant.

### Architectural Principles & Hard Constraints
1. **Zero Scraping & Zero Credential Re-Use:** The chatbot never requests, captures, or re-uses student Anvaya passwords or session cookies. No automated web scraping is used.
2. **Dual-Mode Operation (Config-Driven):**
   - **Primary Path (API Mode):** Secure, server-side REST API calls from the chatbot backend to Anvaya's backend, authenticated via vendor service credentials and scoped strictly to the student's authenticated roll number.
   - **Fallback Path (Deep-Link Mode):** If no API is configured or an endpoint is temporarily unavailable, the assistant provides a direct deep-link into the official Anvaya `/App` portal page where the student can view their live records directly.
3. **DPDP Act 2023 Privacy Compliance:** Live data is fetched on demand, cached only ephemerally in RAM (TTL $\le 180$ seconds), never persisted to disk, database, or audit logs, and never returned to any student other than the record owner.

---

## 2. SSO Identity Contract: The Anvaya Launch Button

Anvaya provides a button on the student portal header/dashboard: **"Open Academic Assistant"**. Clicking this button opens or POSTs to the chatbot with a short-lived, digitally signed JWT assertion.

### 2.1 Endpoint
- **URL:** `https://<chatbot-domain>/api/v1/auth/launch`
- **Method:** `POST` (Form encoded) or `GET` (Query parameter)
  - `POST` is preferred as it keeps the token out of browser histories and proxy logs.
- **Form Field Name:** `launch_token`

### 2.2 JWT Signature & Algorithm
- **Recommended Algorithm:** `RS256` (Asymmetric RSA PKCS#1 v1.5 with SHA-256)
- **Key Custody:** 
  - Anvaya ERP holds the Private Signing Key.
  - Chatbot backend holds only the Public Verification Key (or accesses a JWKS endpoint).
- **Supported Fallbacks:** `PS256`, `ES256`, or `HS256` (pre-shared 256-bit secret).

### 2.3 JWT Claims Specification

| Claim | Type | Required | Description | Example |
|:---|:---:|:---:|:---|:---|
| `sub` | `string` | **Yes** | Student Hall Ticket / Roll Number | `"237Y1A1270"` |
| `iss` | `string` | **Yes** | Issuer identifier | `"anvaya.mlritm.ac.in"` |
| `aud` | `string` | **Yes** | Chatbot audience identifier | `"mlritm-chatbot"` |
| `iat` | `integer` | **Yes** | Issued-at Unix timestamp (seconds) | `1726938000` |
| `exp` | `integer` | **Yes** | Expiration Unix timestamp ($\le \text{iat} + 120\text{s}$) | `1726938120` |
| `jti` | `string` | **Yes** | Unique token UUID for replay prevention | `"f47ac10b-58cc-4372-a567-0e02b2c3d479"` |
| `name` | `string` | Optional | Student official full name | `"GUNDA DINESH"` |
| `role` | `string` | Optional | Account role (must be student) | `"student"` |

### 2.4 Replay Protection & Seed Verification
1. Every `jti` is recorded upon arrival and rejected if seen again.
2. The chatbot validates that `sub` matches an enrolled roll number in the pre-seeded MLRITM student registry.
3. Tokens with lifetime exceeding 120 seconds or issued in the future are rejected.

---

## 3. Server-to-Server Read API Specification (Primary Path)

The college IT or OrgMaker vendor provides a read-only REST API scoped per student roll number.

### 3.1 Base URLs
- **Production Base URL:** e.g. `https://api.anvaya.mlritm.ac.in/v1`
- **Staging / Sandbox Base URL:** e.g. `https://sandbox.anvaya.mlritm.ac.in/api/v1`

### 3.2 Authentication Schemes Supported

The chatbot supports any of the following standard server-to-server schemes:

#### Option A: Service API Key (Recommended for fast turnaround)
- **Header:** `X-API-Key: <vendor_service_token>` or `Authorization: Bearer <vendor_service_token>`
- **Security:** Static pre-shared secret stored exclusively in server environment variables.

#### Option B: OAuth 2.0 Client Credentials Flow
- **Token Endpoint:** `POST https://auth.anvaya.mlritm.ac.in/oauth/token`
- **Request Body:**
  ```json
  {
    "grant_type": "client_credentials",
    "client_id": "mlritm_chatbot_service",
    "client_secret": "<vendor_issued_client_secret>"
  }
  ```
- **Response:**
  ```json
  {
    "access_token": "eyJhbGciOi...",
    "token_type": "Bearer",
    "expires_in": 3600
  }
  ```

---

## 4. Feature Endpoints & Response Shapes

Each feature endpoint is read-only and scopes data to the `{roll_number}` path parameter.

### 4.1 Attendance
- **Endpoint:** `GET /students/{roll_number}/attendance`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "overall_attendance": 86.4,
      "total_classes": 250,
      "attended_classes": 216,
      "is_shortage": false,
      "subjects": [
        {
          "subject_code": "IT401PC",
          "subject_name": "Operating Systems",
          "percentage": 88.5,
          "attended": 46,
          "total": 52
        },
        {
          "subject_code": "IT402PC",
          "subject_name": "Database Management Systems",
          "percentage": 84.0,
          "attended": 42,
          "total": 50
        }
      ]
    }
  }
  ```

### 4.2 Internal Marks
- **Endpoint:** `GET /students/{roll_number}/marks`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "semester": 4,
      "subjects": [
        {
          "subject_code": "IT401PC",
          "subject_name": "Operating Systems",
          "mid1": 24,
          "mid2": 22,
          "assignment": 5,
          "total": 28
        },
        {
          "subject_code": "IT402PC",
          "subject_name": "Database Management Systems",
          "mid1": 23,
          "mid2": 25,
          "assignment": 5,
          "total": 29
        }
      ]
    }
  }
  ```

### 4.3 Semester Examination Results
- **Endpoint:** `GET /students/{roll_number}/results`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "latest_semester": 3,
      "sgpa": 8.45,
      "cgpa": 8.32,
      "backlog_count": 0,
      "results_history": [
        { "semester": 1, "sgpa": 8.15 },
        { "semester": 2, "sgpa": 8.36 },
        { "semester": 3, "sgpa": 8.45 }
      ]
    }
  }
  ```

### 4.4 Fee Status & Payments
- **Endpoint:** `GET /students/{roll_number}/fees`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "academic_year": "2024-2025",
      "total_fee": 115000,
      "paid_fee": 115000,
      "balance_due": 0,
      "payment_status": "Paid in Full",
      "last_receipt_number": "MLRITM/2024/7821"
    }
  }
  ```

### 4.5 Class Timetable
- **Endpoint:** `GET /students/{roll_number}/timetable`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "day": "Monday",
      "schedule": [
        { "period": 1, "time": "09:30 - 10:30", "subject": "Operating Systems", "room": "IT-201" },
        { "period": 2, "time": "10:30 - 11:30", "subject": "DBMS", "room": "IT-201" },
        { "period": 3, "time": "11:30 - 12:30", "subject": "Java Lab", "room": "Lab 3" }
      ]
    }
  }
  ```

### 4.6 Examination Schedule
- **Endpoint:** `GET /students/{roll_number}/exams`
- **Sample Response (HTTP 200):**
  ```json
  {
    "data": {
      "roll_number": "237Y1A1270",
      "exam_title": "B.Tech II Year II Semester Regular Examinations",
      "exams": [
        { "subject_name": "Operating Systems", "date": "2025-05-12", "session": "FN (10:00 - 13:00)", "hall": "Block-A 302" },
        { "subject_name": "Database Management Systems", "date": "2025-05-15", "session": "FN (10:00 - 13:00)", "hall": "Block-A 302" }
      ]
    }
  }
  ```

---

## 5. Fallback Path: Deep-Link Specification

If the vendor API is unavailable or certain endpoints are omitted from `config/anvaya.config`, the chatbot automatically falls back to deep-linking.

The student receives a direct action link that launches the exact authenticated portal view in Anvaya:

| Feature | Deep-Link URL |
|:---|:---|
| **Attendance** | `https://anvaya.mlritm.ac.in/App/StudentAttendance` |
| **Internal Marks** | `https://anvaya.mlritm.ac.in/App/InternalMarks` |
| **Exam Results** | `https://anvaya.mlritm.ac.in/App/ExamResults` |
| **Fee Payments** | `https://anvaya.mlritm.ac.in/App/FeePayments` |
| **Class Timetable** | `https://anvaya.mlritm.ac.in/App/ClassTimetable` |
| **Exam Schedule** | `https://anvaya.mlritm.ac.in/App/ExamSchedule` |
| **Student Profile** | `https://anvaya.mlritm.ac.in/App/StudentProfile` |

---

## 6. Action Items Checklist for MLRITM IT & Vendor

- [ ] **Step 1 (SSO Button):** Implement Anvaya launch button that generates an RS256 signed JWT with claims (`sub`, `iss`, `aud`, `iat`, `exp <= 120s`, `jti`) and POSTs to `/api/v1/auth/launch`.
- [ ] **Step 2 (Public Key Exchange):** Supply the corresponding RSA Public Key (PEM) or hosted JWKS URL to MLRITM Chatbot administrators.
- [ ] **Step 3 (API Access):** Provide Sandbox and Production Base URLs, Service Authentication Credential (API Key or OAuth2 Client ID/Secret), and confirmed endpoint paths.
- [ ] **Step 4 (Deep-Link Verification):** Verify and confirm official `/App/...` paths for deep-link fallback routing.
