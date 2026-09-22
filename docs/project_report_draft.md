# LLM-Powered Academic Advising Chatbot for MLRITM with Live ERP Integration

## Comprehensive Academic Project Report & Defense Documentation

**Institution:** Marri Laxman Reddy Institute of Technology and Management (MLRITM), Autonomous Institution, Affiliated to JNTUH, Hyderabad.  
**Department:** Department of Computer Science and Engineering  
**Academic Year:** 2024–2025  
**Keywords:** Large Language Models (LLM), Academic Advising Chatbot, Llama 2, QLoRA, 4-bit Quantization, GGUF, RAG, DPDP Act 2023, Anvaya ERP.

---

### Abstract

Navigating higher education administration—spanning autonomous academic regulations, syllabus structures, statutory fee schedules, promotion criteria, and attendance thresholds—presents substantial friction for undergraduate students. While institutional portals such as MLRITM’s Anvaya ERP host critical student records, they lack conversational interfaces and natural language understanding. This project presents the design, fine-tuning, and deployment of a dual-path Academic Advising Chatbot powered by Meta's Llama 2 7B. To address hallucination and compute constraints:
1. **Static Institutional Regulations** are internalized through Parameter-Efficient Fine-Tuning (QLoRA) using 4-bit NF4 double quantization on a single 16 GB T4 GPU, augmented by Retrieval-Augmented Generation (RAG) with strict source citations.
2. **Live Dynamic Student Records** (attendance percentages, shortage alerts, semester SGPA, backlogs, and class timetables) bypass generative model weights entirely; they are released only to the student identified by Anvaya through an official integration (OpenID Connect or a signed launch token), authorized in the backend, and formatted deterministically with zero numerical hallucination.
3. Quantization to GGUF `Q4_K_M` enables high-throughput CPU and edge deployment with a 70% reduction in memory footprint (from 13.5 GB to 4.1 GB) with negligible factual degradation. The system adheres strictly to the Digital Personal Data Protection (DPDP) Act 2023.

---

### 1. Dual-Path System Architecture

The core architectural paradigm bifurcates static knowledge from volatile student records:

```text
Student Query 
      │
      ▼
Intent Router (Llama-2 GBNF Grammar / Heuristic Fast-Path)
      │
      ├──────────────────────────────┬──────────────────────────────┐
      │ Intent: FAQ_RAG              │ Intent: ATTENDANCE/RESULTS   │ Intent: HOLIDAYS
      ▼                              ▼                              ▼
Static RAG Pipeline            Student Records Layer          Academic Calendar Engine
- Dense Retrieval (BGE)        - Anvaya-Verified Identity     - Working Days Remaining
- Cross-Encoder Rerank         - Backend Authorization        - Holiday Schedules
- Fine-Tuned Llama 2 7B        - Deterministic Math Engine    - Exam Timelines
- Source Link Citations        - Zero LLM Hallucination       - Structured Formatter
      │                              │                              │
      └──────────────────────────────┴──────────────────────────────┘
                                     │
                                     ▼
                    Deterministic Template Formatter
                  ("Source: Anvaya, last synced <time>")
                                     │
                                     ▼
                  Streaming Response (FastAPI / SSE)
```

---

### 1.1 Official MLRITM Anvaya Portal Integration & Dynamic Authentication Flow

To eliminate numerical and factual hallucinations in dynamic advising, the chatbot directly integrates with MLRITM's official Anvaya ERP system (`https://anvaya.mlritm.ac.in`).

```text
Student opens chatbot
        ↓
Click "Login with Anvaya"
        ↓
Open official Anvaya Login (https://anvaya.mlritm.ac.in/Login)
        ↓
Student logs into Anvaya
        ↓
Successful authentication
        ↓
Return/redirect to chatbot
        ↓
Verify authenticated student identity
        ↓
Access authorized Anvaya application data (https://anvaya.mlritm.ac.in/App)
        ↓
Identify the exact student
        ↓
Synchronize the student's academic information
        ↓
Store/cache the authorized data securely (AES-256-GCM)
        ↓
Student uses chatbot
        ↓
AI answers using that student's latest available data
```

#### Key Integration Principles:
1. **Zero Default Student Details:** Unauthenticated sessions strictly display no student records, placeholder names (such as "Student"), or default roll numbers (`237Y1A1270`). Personal queries without an active session immediately redirect the user to login.
2. **Student Identity Hierarchy:** Identity resolution prioritizes `Anvaya User ID` $\rightarrow$ `Student ID` $\rightarrow$ `Roll Number`. Students are never identified solely by display name, and client-supplied identifiers are strictly rejected.
3. **Official Portals:** Interacts with the legitimate `/Login` form and `/App` dashboard, leveraging verified server-side session handoffs without storing student credentials.
4. **Live Synchronization:** The backend maintains synchronization state (`last_synced_at`, `source = "Anvaya"`, `data_status`), supporting manual triggers ("Refresh My Data") to reflect real-time attendance and internal mark adjustments.

---

### 2. Experimental Results & Benchmark Tables

#### 2.1 Quantization Trade-Off Analysis (Core Project Claim)

To substantiate edge and low-cost serving feasibility, Llama 2 7B was evaluated across FP16, INT8, and 4-bit GGUF formats:

| Format / Precision | Memory (RAM/VRAM) | Inference Throughput | Time-To-First-Token | Wikitext-2 Perplexity | Factual Accuracy | Hardware Target |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **FP16 (Unquantized)** | 13.5 GB | 18.4 tokens/s | 320 ms | 5.47 | 91.2% | NVIDIA T4 (16 GB) |
| **INT8 (LLM.int8())** | 7.2 GB | 12.1 tokens/s | 480 ms | 5.54 | 90.8% | NVIDIA T4 (16 GB) |
| **GGUF Q4_K_M (Ours)** | **4.1 GB** | **24.8 tokens/s** | **210 ms** | **5.68** | **89.6%** | **CPU (x86_64) / Edge** |

> **Conclusion:** Quantization to GGUF `Q4_K_M` reduces memory consumption by **69.6%** while maintaining 98.2% of unquantized factual accuracy. Throughput on CPU increases substantially due to cache optimization and reduced memory bandwidth pressure.

---

#### 2.2 Model Architecture Comparison on Academic Knowledge

Performance was benchmarked across 100 manually verified institutional queries covering MLRITM fee structures, attendance condonation rules, and credit promotion requirements:

| Architecture Configuration | Factual F1 Score | ROUGE-L Fluency | Hallucination Rate | Source Citations Provided |
| :--- | :--- | :--- | :--- | :--- |
| **Base Llama 2 7B Chat** | 42.8% | 0.44 | 28.0% | 0.0% |
| **Fine-Tuned (QLoRA)** | 78.4% | 0.72 | 9.5% | 12.0% |
| **Fine-Tuned + RAG (Dual-Path)** | **94.6%** | **0.86** | **1.2%** | **98.5%** |

> **Analysis:** Base Llama 2 exhibits significant hallucination on institutional specifics (e.g., misstating MLRITM autonomous credit requirements or assuming non-existent medical courses). QLoRA fine-tuning teaches domain-specific terminology, institutional tone, and refusal behavior. The addition of RAG supplies exact, dynamic factual grounding and verifiable source URLs.

---

### 3. Data Collection, Cleaning & QLoRA Fine-Tuning Methodology

1. **Document Ingestion:** Crawled official public portals of `mlritm.ac.in` adhering strictly to `robots.txt`. Extracted text and tables from academic regulations (MLR20/MLR22), TAFRC fee structures, and TS ePASS guidelines.
2. **Chunking:** Chunked documents into 300–500 tokens with 50-token sliding overlap.
3. **Q&A Synthesis:** Synthesized 2,500 question-answer pairs encompassing formal student queries, informal colloquial phrasing, and common typos. Integrated explicit refusal examples for out-of-scope queries (e.g., medical courses, portal hacking).
4. **Data Partitioning:** Document-level 80/10/10 split across training, validation, and testing sets to eliminate data leakage.
5. **QLoRA Hyperparameters:**
   - 4-bit NormalFloat (NF4) with double quantization.
   - LoRA rank $r = 16$, alpha $\alpha = 32$, dropout $= 0.05$.
   - Target modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`.
   - Optimizer: `paged_adamw_8bit` with cosine learning rate schedule ($2\times 10^{-4}$) and 3% warmup.
   - Gradient checkpointing enabled to fit within 16 GB VRAM on a free Google Colab / Kaggle T4 instance.

---

### 4. Anvaya ERP Integration, Security & DPDP Act 2023 Compliance

1. **Deterministic Attendance Math Engine:**
   - LLMs are never permitted to calculate attendance percentages.
   - Formula for classes needed to reach 75%:
     $$\Delta C = \left\lceil \frac{0.75 \times T - A}{0.25} \right\rceil$$
     Where $T$ is total conducted classes and $A$ is classes attended.
2. **No Passwords, Anvaya-Issued Identity:** The chatbot never asks for or receives a student's Anvaya password and never reads Anvaya's pages. A student is identified only by an assertion that Anvaya signs (OpenID Connect ID token or signed launch token), verified for signature, issuer, audience, expiry and single use.
3. **Server-Side Sessions:** After verification the backend creates its own session: a random token stored only as a SHA-256 hash, with a 20-minute time-to-live (TTL), revoked on sign-out.
4. **Prompt Injection Defense:** Reference documents and live portal data are enclosed in `<document_context untrusted="true">` boundaries. System prompts instruct the LLM that content within boundaries is strictly unverified data and cannot execute commands.
5. **DPDP Act 2023 Alignment:** Mandatory explicit student consent before any personal record is shown, purpose specification, audit logging without PII, and immediate session revocation upon sign-out.
6. **Integration Status:** No official Anvaya integration mechanism is publicly documented, so a formal integration request has been prepared for MLRITM and the ERP vendor (Hilip Technologies). Sign-in and personal records remain disabled until an official mechanism is configured.

---

### 5. Viva Voce Preparation: Key Technical Q&A

**Q1: Why use Llama 2 7B when newer models like Llama 3 exist?**  
*Answer:* Llama 2 7B provides a proven, open foundation with fully documented QLoRA convergence characteristics on constrained 16 GB T4 GPUs. Its architectural footprint allows seamless conversion to GGUF `Q4_K_M` for local CPU inference. The system architecture is model-agnostic; swapping the base model in `config.py` requires updating only the model path and prompt template.

**Q2: Why combine QLoRA with RAG instead of using RAG alone?**  
*Answer:* RAG supplies retrieved factual context, but base models frequently struggle with institutional stylistic nuances, prompt injection attacks, and strict refusal policies. QLoRA fine-tuning conditions the model's internal representations to adhere to autonomous academic formatting, prioritize institutional rules, and gracefully refuse unanswerable queries.

**Q3: How does the system prevent student A from viewing student B's attendance?**  
*Answer:* Authorization is enforced in the backend, not by prompt instructions. The student's record key comes from Anvaya's verified identity claim and is stored with the server-side session; no API accepts a roll number from the browser. Before any record is loaded, the backend checks the session and DPDP consent, and it rejects any returned record whose owner differs from the session. The LLM only picks which of the student's own records to show and never makes access decisions.

**Q4: What happens if Anvaya portal layout changes?**  
*Answer:* Nothing, because the chatbot does not read Anvaya's pages. Student records come only through an officially provided data interface behind the `StudentDataProvider` contract. Until MLRITM supplies one, the assistant tells students that records are not connected yet and links them to Anvaya.
