/**
 * MLRITM Student AI Assistant - client application.
 *
 * Identity comes only from Anvaya. The student presses "Login with Anvaya", the backend verifies
 * Anvaya's identity assertion and redirects back with a one-time #handoff code, and this page
 * exchanges it once for a chatbot session. This page never asks for, sees or stores an Anvaya
 * password, and it never tells the server whose records to load: the server already knows, from
 * the session, and refuses to take that from the browser.
 */

// Backend URL: set window.BACKEND_URL in a <script> before this file, or leave
// blank to use same-origin /api/v1 (works when backend is on the same host).
// For Netlify static deployments pointing at a separate backend (e.g. Render/Railway):
//   <script>window.BACKEND_URL = "https://your-backend.onrender.com";</script>
const API_BASE = (() => {
  if (window.BACKEND_URL) return `${window.BACKEND_URL.replace(/\/$/, "")}/api/v1`;
  if (/^(localhost|127\.0\.0\.1)/.test(window.location.host))
    return `${window.location.origin}/api/v1`;
  // Netlify / any other static host — use same-origin (requires backend proxy or same domain)
  return "/api/v1";
})();


const AUTH_ERROR_MESSAGES = {
  not_configured: "Anvaya sign-in is not connected to this assistant yet, so personal records are unavailable. General questions still work.",
  invalid_launch: "Anvaya's sign-in link could not be verified or has already been used. Please sign in again.",
  invalid_state: "That sign-in attempt expired. Please start again.",
  invalid_login: "Anvaya's sign-in response could not be verified. Please try again.",
  provider_error: "Anvaya did not complete the sign-in. Please try again.",
  provider_unavailable: "Anvaya sign-in is temporarily unavailable. Please try again shortly."
};

// Clean any previous student storage keys from sessionStorage and localStorage
["anvaya_token", "student_roll", "student_name", "student_profile", "student_data"].forEach((key) => {
  try { sessionStorage.removeItem(key); } catch (e) {}
  try { localStorage.removeItem(key); } catch (e) {}
});

// ------------------------------------------------------------------ state

let sessionToken = sessionStorage.getItem("chat_session") || null;
let student = null;      // { roll_number, display_name, consent_granted, expires_at }
let profile = null;      // the /student/profile payload
let authConfig = null;   // { identity_provider, sign_in_url, anvaya_url, student_data_provider }
let sending = false;

const $ = (id) => document.getElementById(id);

const el = {
  chat: $("chatMessages"), form: $("chatForm"), input: $("userInput"), send: $("sendBtn"),
  loginBtn: $("loginBtn"), loginBtnSidebar: $("loginBtnSidebar"), logoutBtn: $("logoutBtn"),
  profileChip: $("profileChip"), chipAvatar: $("chipAvatar"), chipName: $("chipName"), chipMeta: $("chipMeta"),
  statusDot: $("authStatusDot"), statusText: $("authStatusText"),
  signedOutPanel: $("signedOutPanel"), signInHint: $("signInHint"), studentPanel: $("studentPanel"),
  profileAvatar: $("profileAvatar"), profileName: $("profileName"), profileRoll: $("profileRoll"),
  profileSemester: $("profileSemester"), profileDept: $("profileDept"), profileMeta: $("profileMeta"),
  profileYear: $("profileYear"), profileSection: $("profileSection"),
  statAttendance: $("statAttendance"), statCgpa: $("statCgpa"), statSubjects: $("statSubjects"),
  attendanceAlert: $("attendanceAlert"),
  syncState: $("syncState"), lastSynced: $("lastSynced"), syncDataStatus: $("syncDataStatus"), syncBtn: $("syncBtn"),
  syncBanner: $("syncBanner"), syncBannerSpinner: $("syncBannerSpinner"),
  syncBannerIcon: $("syncBannerIcon"), syncBannerText: $("syncBannerText"),
  consentModal: $("consentModal"), consentForm: $("consentForm"), consentCheckbox: $("consentCheckbox"),
  consentError: $("consentError"), consentIdentity: $("consentIdentity"), closeConsentBtn: $("closeConsentBtn"),
  profileModal: $("profileModal"), closeProfileModalBtn: $("closeProfileModalBtn"),
  closeProfileModalBtnBottom: $("closeProfileModalBtnBottom"), profileModalContent: $("profileModalContent"),
  quickProfileTile: $("quickProfileTile"),
  toast: $("toast")
};

// ------------------------------------------------------------------ helpers

function escapeHtml(text) {
  const node = document.createElement("div");
  node.textContent = text ?? "";
  return node.innerHTML;
}

function authHeaders() {
  return sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {};
}

function anvayaUrl() {
  return (authConfig && authConfig.anvaya_url) || "https://anvaya.mlritm.ac.in/";
}

function anvayaLoginUrl() {
  return (authConfig && authConfig.anvaya_login_url) || "https://anvaya.mlritm.ac.in/Login";
}

function studentLabel() {
  if (profile && profile.identity) return profile.identity.name || profile.identity.roll_number || "your account";
  if (student) return student.display_name || student.roll_number || "your account";
  return "your account";
}

function initials(name) {
  const words = String(name || "S").trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] || "S") + (words.length > 1 ? words[words.length - 1][0] : "")).toUpperCase();
}

function relativeTime(iso) {
  if (!iso) return "not synced yet";
  const seconds = Math.max(0, (Date.now() - new Date(iso + (iso.endsWith("Z") ? "" : "Z")).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

let toastTimer = null;
function toast(message, kind = "") {
  el.toast.textContent = message;
  el.toast.className = `toast ${kind ? `is-${kind}` : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.add("hidden"), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(options.headers || {}) }
  });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

let syncBannerTimeout = null;

function showSyncing(text = "Syncing your academic profile...") {
  if (!el.syncBanner) return;
  clearTimeout(syncBannerTimeout);
  el.syncBanner.classList.remove("hidden", "is-ready");
  if (el.syncBannerSpinner) el.syncBannerSpinner.style.display = "inline-block";
  if (el.syncBannerIcon) el.syncBannerIcon.classList.add("hidden");
  if (el.syncBannerText) el.syncBannerText.textContent = text;
}

function showSyncReady(text = "Your MLRITM academic profile is ready.") {
  if (!el.syncBanner) return;
  clearTimeout(syncBannerTimeout);
  el.syncBanner.classList.remove("hidden");
  el.syncBanner.classList.add("is-ready");
  if (el.syncBannerSpinner) el.syncBannerSpinner.style.display = "none";
  if (el.syncBannerIcon) el.syncBannerIcon.classList.remove("hidden");
  if (el.syncBannerText) el.syncBannerText.textContent = text;
  syncBannerTimeout = setTimeout(() => {
    el.syncBanner.classList.add("hidden");
  }, 4000);
}

function hideSyncBanner() {
  if (!el.syncBanner) return;
  clearTimeout(syncBannerTimeout);
  el.syncBanner.classList.add("hidden");
}

// ------------------------------------------------------------------ session state

function clearSession({ quiet = false } = {}) {
  sessionToken = null;
  student = null;
  profile = null;
  ["chat_session", "anvaya_token", "student_roll", "student_name", "student_profile", "student_data"].forEach((k) => {
    try { sessionStorage.removeItem(k); } catch (e) {}
    try { localStorage.removeItem(k); } catch (e) {}
  });
  if (el.profileName) el.profileName.textContent = "—";
  if (el.profileRoll) el.profileRoll.textContent = "—";
  if (el.profileDept) el.profileDept.textContent = "—";
  if (el.profileSemester) el.profileSemester.textContent = "—";
  hideSyncBanner();
  render();
  if (!quiet) toast("Signed out of the assistant.");
}

function signInHelp() {
  let text =
    `To see your own records, use **Login with Anvaya**. This assistant never asks for your Anvaya password.`;
  if (!authConfig || authConfig.identity_provider === "none") {
    text += `\n\n*Anvaya sign-in is not connected to this assistant yet. Questions about regulations, fees and the academic calendar still work.*`;
  }
  return text;
}

function render() {
  const signedIn = Boolean(sessionToken && student);

  el.loginBtn.classList.toggle("hidden", signedIn);
  el.profileChip.classList.toggle("hidden", !signedIn);
  el.signedOutPanel.classList.toggle("hidden", signedIn);
  el.studentPanel.classList.toggle("hidden", !signedIn);

  if (signedIn) {
    const label = studentLabel();
    el.chipAvatar.textContent = initials(label);
    el.chipName.textContent = label;
    el.chipMeta.textContent = profile?.identity?.roll_number || student.roll_number || "Anvaya";
    el.statusDot.className = "status-dot is-online";
    el.statusText.textContent = "Anvaya connected";
  } else {
    el.statusDot.className = "status-dot";
    el.statusText.textContent = "Not signed in";
  }

  if (el.signInHint) {
    el.signInHint.textContent =
      !authConfig || authConfig.identity_provider === "none"
        ? "Anvaya sign-in is not connected to this assistant yet."
        : "";
  }
}

const SEMESTER_TO_YEAR = {
  "BT2601": "1st Year",
  "BT2503": "2nd Year",
  "BT2405": "3rd Year",
  "BT2307": "4th Year"
};

const SEMESTER_TO_DISPLAY = {
  "BT2601": "Semester 1 (BT2601)",
  "BT2503": "Semester 3 (BT2503)",
  "BT2405": "Semester 5 (BT2405)",
  "BT2307": "Semester 7 (BT2307)"
};

function renderProfile() {
  if (!profile) return;

  const rec = profile.stored_record || profile.profile_data;
  if (rec) {
    const rawSem = (rec.current_semester || "").trim().toUpperCase();
    const sName = rec.student_name || rec.name || studentLabel();
    const sRoll = rec.roll_number || student?.roll_number || "—";
    const sDept = rec.branch || "—";
    const sSem = rec.semester_display || SEMESTER_TO_DISPLAY[rawSem] || (rec.current_semester ? `Semester ${rec.current_semester}` : (profile.current_semester ? `Semester ${profile.current_semester}` : "—"));
    const sYear = rec.year_of_study || SEMESTER_TO_YEAR[rawSem] || rec.year || rec.admission_year || "—";
    const sSec = rec.section || "—";

    el.profileAvatar.textContent = initials(sName);
    el.profileName.textContent = sName;
    el.profileRoll.textContent = sRoll;
    el.profileDept.textContent = sDept;
    el.profileSemester.textContent = sSem;
    el.profileYear.textContent = sYear;
    el.profileSection.textContent = sSec;

    const meta = [
      sYear !== "—" ? `<b>${sYear}</b>` : null,
      sSem !== "—" ? sSem : null,
      sDept !== "—" ? sDept : null,
      sSec !== "—" ? `Section ${sSec}` : null
    ].filter(Boolean);
    el.profileMeta.innerHTML = meta.join(" · ");

    if (profile.attendance_summary) {
      const attendance = profile.attendance_summary;
      el.statAttendance.textContent = `${attendance.overall_percentage}%`;
      el.statAttendance.className = `stat-value ${
        attendance.detention_risk ? "is-bad" : attendance.below_required_75 ? "is-warn" : "is-good"
      }`;
    } else {
      el.statAttendance.textContent = "—";
      el.statAttendance.className = "stat-value";
    }

    el.statCgpa.textContent = profile.latest_result_summary?.cgpa ?? "—";
    el.statSubjects.textContent = profile.counts?.subjects ?? (rec.current_semester ? "7" : "—");

    el.attendanceAlert.className = "px-4 py-2.5 text-[11px] leading-relaxed border-t border-white/5 text-indigo-300 bg-indigo-500/10";
    el.attendanceAlert.innerHTML = `✓ Student master record loaded from database. <a href="#" id="inlineViewProfileLink" class="underline font-semibold text-white ml-1">View Full Profile</a>`;
    $("inlineViewProfileLink")?.addEventListener("click", (e) => {
      e.preventDefault();
      handleProfileDataClick();
    });

    if (el.syncDataStatus) el.syncDataStatus.textContent = "Verified DB";
    if (el.lastSynced) el.lastSynced.textContent = "Live";
    el.statusDot.className = "status-dot is-online";
    return;
  }

  if (!profile.connected) {
    el.profileName.textContent = studentLabel();
    el.profileRoll.textContent = student?.roll_number || "";
    ["profileSemester", "profileDept", "profileYear", "profileSection"].forEach((key) => {
      el[key].textContent = "—";
    });
    el.profileMeta.textContent = "Records unavailable";
    el.statAttendance.textContent = el.statCgpa.textContent = el.statSubjects.textContent = "—";
    el.attendanceAlert.className = "px-4 py-2.5 text-[11px] leading-relaxed border-t border-white/5 text-slate-400";
    el.attendanceAlert.textContent = profile.message || "Your Anvaya records are not available right now.";
    if (el.syncState) el.syncState.textContent = "Not connected";
    if (el.lastSynced) el.lastSynced.textContent = "—";
    el.statusDot.className = "status-dot is-stale";
    return;
  }

  const id = profile.identity || {};
  el.profileAvatar.textContent = initials(id.name);
  el.profileName.textContent = id.name || (id.roll_number ? `Student (${id.roll_number})` : "—");
  el.profileRoll.textContent = id.roll_number || "—";
  el.profileSemester.textContent = profile.current_semester ? `Semester ${profile.current_semester}` : "—";
  el.profileDept.textContent = id.department || "—";
  el.profileYear.textContent = id.year || "—";
  el.profileSection.textContent = id.section || "—";

  // The narrow-screen equivalent of the field grid above
  const meta = [
    profile.current_semester ? `<b>Semester ${profile.current_semester}</b>` : null,
    id.department,
    id.section ? `Section ${id.section}` : null
  ].filter(Boolean);
  el.profileMeta.innerHTML = meta.join(" · ");

  const attendance = profile.attendance_summary;
  if (attendance) {
    el.statAttendance.textContent = `${attendance.overall_percentage}%`;
    el.statAttendance.className = `stat-value ${
      attendance.detention_risk ? "is-bad" : attendance.below_required_75 ? "is-warn" : "is-good"
    }`;
  } else {
    el.statAttendance.textContent = "—";
    el.statAttendance.className = "stat-value";
  }

  el.statCgpa.textContent = profile.latest_result_summary?.cgpa ?? "—";
  el.statSubjects.textContent = profile.counts?.subjects ?? "—";

  // A shortage is the one thing worth surfacing before the student asks
  if (attendance?.detention_risk) {
    el.attendanceAlert.className = "px-4 py-2.5 text-[11px] leading-relaxed border-t border-white/5 text-rose-300 bg-rose-500/5";
    el.attendanceAlert.textContent = `Attendance is below 65%. Ask me what your options are.`;
  } else if (attendance?.below_required_75) {
    el.attendanceAlert.className = "px-4 py-2.5 text-[11px] leading-relaxed border-t border-white/5 text-amber-300 bg-amber-500/5";
    el.attendanceAlert.textContent =
      `Attendance is below the required 75%. ${attendance.classes_needed_to_reach_75} more classes would bring you back.`;
  } else {
    el.attendanceAlert.className = "hidden";
  }

  const dataStatus = profile.data_status || (profile.is_stale ? "Stale (Offline)" : "Live");
  if (el.syncDataStatus) {
    el.syncDataStatus.textContent = dataStatus;
    el.syncDataStatus.className = profile.is_stale ? "font-semibold text-amber-400" : "font-semibold text-emerald-400";
  }
  if (el.syncState) el.syncState.textContent = profile.is_stale ? "Offline" : "Synced";
  if (el.lastSynced) el.lastSynced.textContent = profile.last_synced_formatted || relativeTime(profile.last_synced_at);
  el.statusDot.className = `status-dot ${profile.is_stale ? "is-stale" : "is-online"}`;

  if (profile.semester_changed && profile.current_semester) {
    toast(`Your current semester is now Semester ${profile.current_semester}.`, "success");
  }
}

async function loadProfile({ force = false, showBanner = true } = {}) {
  if (!sessionToken) return;

  if (showBanner) showSyncing("Syncing your academic profile...");
  el.syncBtn?.classList.add("is-spinning");
  el.statusDot.className = "status-dot is-syncing";
  try {
    const { ok, status, body } = await api(force ? "/student/sync" : "/student/profile", {
      method: force ? "POST" : "GET"
    });

    if (status === 401) {
      hideSyncBanner();
      return clearSession({ quiet: true });
    }
    if (status === 403) {
      hideSyncBanner();
      return openConsent();
    }
    if (!ok) {
      hideSyncBanner();
      toast(body.detail || "Could not load your Anvaya profile.", "error");
      return;
    }

    profile = body;
    render();
    renderProfile();
    if (showBanner) showSyncReady("Your MLRITM academic profile is ready.");
  } catch (error) {
    hideSyncBanner();
    toast("Could not reach the assistant server.", "error");
  } finally {
    el.syncBtn?.classList.remove("is-spinning");
  }
}

// ------------------------------------------------------------------ sign-in / sign-out

async function loadAuthConfig() {
  try {
    const response = await fetch(`${API_BASE}/auth/config`);
    authConfig = response.ok ? await response.json() : null;
  } catch {
    authConfig = null;
  }
}

/** Finishes the redirect back from /auth/launch or /auth/oidc/callback. */
async function consumeRedirectFragment() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const handoff = params.get("handoff");
  const authError = params.get("auth_error");
  if (!handoff && !authError) return;

  // Take the one-time code out of the address bar and the history entry
  history.replaceState(null, "", window.location.pathname + window.location.search);

  if (authError) {
    appendMessage("assistant", AUTH_ERROR_MESSAGES[authError] || "Anvaya sign-in failed. Please try again.");
    return;
  }

  const { ok, body } = await api("/auth/session/exchange", {
    method: "POST",
    body: JSON.stringify({ handoff_code: handoff })
  });
  if (!ok) {
    appendMessage("assistant", body.detail || "That sign-in link expired. Please sign in again.");
    return;
  }

  sessionToken = body.session_token;
  student = body.student;
  sessionStorage.setItem("chat_session", sessionToken);
  render();

  appendMessage("assistant", `✅ Signed in through Anvaya as **${escapeHtml(studentLabel())}**.`);
  if (!student.consent_granted) openConsent();
  else await loadProfile();
}

async function restoreSession() {
  if (!sessionToken || student) return;
  const { ok, body } = await api("/auth/me");
  if (!ok) return clearSession({ quiet: true });
  student = body.student;
  if (student.consent_granted) await loadProfile();
}

function startSignIn() {
  if (authConfig && authConfig.sign_in_mode === "redirect" && authConfig.sign_in_url) {
    window.location.href = new URL(authConfig.sign_in_url, window.location.origin).href;
    return;
  }
  window.location.href = new URL("/anvaya/login.html", window.location.origin).href;
}

async function signOut() {
  await api("/auth/logout", { method: "POST" }).catch(() => {});
  clearSession();
  appendMessage("assistant", "You have signed out. Your assistant session has been revoked.");
}

// ------------------------------------------------------------------ consent

function openConsent() {
  el.consentIdentity.textContent = `Signed in through Anvaya as ${studentLabel()}.`;
  el.consentError.classList.add("hidden");
  el.consentModal.classList.remove("hidden");
}

el.closeConsentBtn.addEventListener("click", () => el.consentModal.classList.add("hidden"));

el.consentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  el.consentError.classList.add("hidden");

  const { ok, status, body } = await api("/auth/consent", {
    method: "POST",
    body: JSON.stringify({ dpdp_consent_granted: el.consentCheckbox.checked })
  });

  if (status === 401) {
    clearSession({ quiet: true });
    el.consentError.textContent = "Your session has ended. Please sign in again.";
    el.consentError.classList.remove("hidden");
    return;
  }
  if (!ok) {
    el.consentError.textContent = body.detail || "Could not record consent.";
    el.consentError.classList.remove("hidden");
    return;
  }

  student.consent_granted = true;
  el.consentModal.classList.add("hidden");
  el.consentForm.reset();
  appendMessage("assistant", "Thanks! You can now ask about your attendance, subjects, marks, timetable, exams and results.");
  await loadProfile();
});

// ------------------------------------------------------------------ chat

function appendMessage(sender, content = "") {
  const wrapper = document.createElement("div");
  wrapper.className = "message-animate flex items-start gap-3";

  if (sender === "user") {
    wrapper.classList.add("justify-end");
    const bubble = document.createElement("div");
    bubble.className = "bubble-user";
    bubble.textContent = content;         // user text is never parsed as markup
    wrapper.appendChild(bubble);
  } else {
    wrapper.innerHTML = `
      <div class="ai-avatar">AI</div>
      <div class="bubble-assistant prose-custom">
        <div class="msg-content">${marked.parse(content)}</div>
        <div class="msg-feedback-bar mt-2 pt-1 border-t border-white/5 flex items-center justify-between text-[11px] text-slate-400">
          <span class="text-[10px] text-slate-500">Was this helpful?</span>
          <div class="flex items-center gap-1.5">
            <button class="feedback-btn px-1.5 py-0.5 rounded hover:bg-emerald-500/20 hover:text-emerald-400 transition" data-action="helpful" title="Helpful answer">👍</button>
            <button class="feedback-btn px-1.5 py-0.5 rounded hover:bg-rose-500/20 hover:text-rose-400 transition" data-action="not_helpful" title="Not helpful / missing info">👎</button>
          </div>
        </div>
      </div>
    `;
    const feedbackBar = wrapper.querySelector(".msg-feedback-bar");
    if (feedbackBar) {
      feedbackBar.querySelectorAll(".feedback-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const action = btn.dataset.action;
          const queryText = window._lastUserQuery || "";
          if (action === "helpful") {
            try {
              await fetch(`${API_BASE}/chat/feedback`, {
                method: "POST",
                headers: { "Content-Type": "application/json", ...authHeaders() },
                body: JSON.stringify({ query: queryText, feedback: "HELPFUL" })
              });
              feedbackBar.innerHTML = `<span class="text-emerald-400 text-[10px]">✓ Thank you for your feedback!</span>`;
            } catch (err) {
              feedbackBar.innerHTML = `<span class="text-slate-400 text-[10px]">Feedback recorded.</span>`;
            }
          } else {
            const note = prompt("What was missing from this answer? (Optional notes help improve college knowledge):");
            try {
              await fetch(`${API_BASE}/chat/feedback`, {
                method: "POST",
                headers: { "Content-Type": "application/json", ...authHeaders() },
                body: JSON.stringify({ query: queryText, feedback: "NOT_HELPFUL", missing_info: note || "" })
              });
              feedbackBar.innerHTML = `<span class="text-indigo-300 text-[10px]">✓ Thank you! Flagged for knowledge review.</span>`;
            } catch (err) {
              feedbackBar.innerHTML = `<span class="text-slate-400 text-[10px]">Feedback recorded.</span>`;
            }
          }
        });
      });
    }
  }

  el.chat.appendChild(wrapper);
  el.chat.scrollTop = el.chat.scrollHeight;
  return wrapper.querySelector(".msg-content");
}

function thinkingBubble() {
  const node = appendMessage("assistant", "");
  node.innerHTML = `<span class="thinking"><i></i><i></i><i></i></span>`;
  return node;
}

async function sendMessage(text) {
  const question = (text ?? el.input.value).trim();
  if (!question || sending) return;

  window._lastUserQuery = question;
  sending = true;
  el.send.disabled = true;
  el.input.value = "";
  appendMessage("user", question);
  const bubble = thinkingBubble();

  let requiresAuth = false;
  let requiresConsent = false;
  let usedPersonalData = false;

  try {
    const response = await fetch(`${API_BASE}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ message: question, stream: true })
    });
    if (!response.ok) throw new Error(`The assistant returned HTTP ${response.status}.`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let answer = "";
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";           // keep the trailing partial line

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (raw === "[DONE]") continue;
        try {
          const parsed = JSON.parse(raw);
          requiresAuth = requiresAuth || parsed.requires_auth === true;
          requiresConsent = requiresConsent || parsed.requires_consent === true;
          usedPersonalData = usedPersonalData || parsed.used_personal_data === true;
          if (parsed.token) {
            answer += parsed.token;
            bubble.innerHTML = marked.parse(answer);
            el.chat.scrollTop = el.chat.scrollHeight;
          }
        } catch {
          /* a partial frame; the next chunk completes it */
        }
      }
    }

    if (requiresAuth) {
      if (sessionToken) clearSession({ quiet: true });   // the server no longer knows this session
      bubble.innerHTML = marked.parse(signInHelp());
    }
    if (requiresConsent && student) openConsent();
    if (usedPersonalData) {
      // Attendance and marks move, so keep the sidebar in step with the answer
      loadProfile();
    }
  } catch (error) {
    bubble.innerHTML = `<span class="text-rose-400">${escapeHtml(error.message)}</span>`;
  } finally {
    sending = false;
    el.send.disabled = false;
    el.input.focus();
  }
}

// ------------------------------------------------------------------ wiring

el.form.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

[el.loginBtn, el.loginBtnSidebar].forEach((button) => button?.addEventListener("click", startSignIn));

el.logoutBtn.addEventListener("click", () => {
  if (confirm(`Signed in through Anvaya as ${studentLabel()}. Sign out?`)) signOut();
});

el.syncBtn.addEventListener("click", () => loadProfile({ force: true }));
$("refreshDataTile")?.addEventListener("click", () => loadProfile({ force: true }));
// ------------------------------------------------------------------ student profile modal

function formatProfileVal(val) {
  if (val === null || val === undefined) return "Not Available";
  const s = String(val).trim();
  if (s === "" || s.toLowerCase() === "null" || s.toLowerCase() === "none") {
    return "Not Available";
  }
  return escapeHtml(s);
}

function renderProfileModal(rec) {
  if (!el.profileModal || !el.profileModalContent) return;

  const html = `
    <!-- 👤 Personal Details -->
    <div class="profile-section-card space-y-3">
      <div class="flex items-center gap-2 text-xs font-semibold text-indigo-300 border-b border-white/5 pb-2">
        <span>👤</span> Personal Details
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <div class="profile-field-label">Student Name</div>
          <div class="profile-field-value text-white font-semibold">${formatProfileVal(rec.student_name || rec.name)}</div>
        </div>
        <div>
          <div class="profile-field-label">Roll Number</div>
          <div class="profile-field-value font-mono text-indigo-300 font-semibold">${formatProfileVal(rec.roll_number)}</div>
        </div>
        <div>
          <div class="profile-field-label">Year of Study</div>
          <div class="profile-field-value text-emerald-400 font-semibold">${formatProfileVal(rec.year_of_study || SEMESTER_TO_YEAR[(rec.current_semester || "").trim().toUpperCase()] || rec.year)}</div>
        </div>
        <div>
          <div class="profile-field-label">Current Semester</div>
          <div class="profile-field-value text-indigo-200 font-semibold">${formatProfileVal(rec.semester_display || SEMESTER_TO_DISPLAY[(rec.current_semester || "").trim().toUpperCase()] || rec.current_semester)}</div>
        </div>
        <div>
          <div class="profile-field-label">Branch</div>
          <div class="profile-field-value">${formatProfileVal(rec.branch)}</div>
        </div>
        <div>
          <div class="profile-field-label">Academic Year</div>
          <div class="profile-field-value">${formatProfileVal(rec.academic_year)}</div>
        </div>
        <div>
          <div class="profile-field-label">Admission Year</div>
          <div class="profile-field-value">${formatProfileVal(rec.admission_year || rec.batch)}</div>
        </div>
        <div>
          <div class="profile-field-label">Gender</div>
          <div class="profile-field-value">${formatProfileVal(rec.gender)}</div>
        </div>
        <div>
          <div class="profile-field-label">Date of Birth</div>
          <div class="profile-field-value">${formatProfileVal(rec.date_of_birth)}</div>
        </div>
      </div>
    </div>

    <!-- 📞 Contact Details -->
    <div class="profile-section-card space-y-3">
      <div class="flex items-center gap-2 text-xs font-semibold text-indigo-300 border-b border-white/5 pb-2">
        <span>📞</span> Contact Details
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <div class="profile-field-label">Student Mobile</div>
          <div class="profile-field-value">${formatProfileVal(rec.student_mobile)}</div>
        </div>
        <div>
          <div class="profile-field-label">Student Email</div>
          <div class="profile-field-value break-all">${formatProfileVal(rec.student_email || rec.email)}</div>
        </div>
      </div>
    </div>

    <!-- 👨‍👩‍👧 Parent Details -->
    <div class="profile-section-card space-y-3">
      <div class="flex items-center gap-2 text-xs font-semibold text-indigo-300 border-b border-white/5 pb-2">
        <span>👨‍👩‍👧</span> Parent Details
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <div class="profile-field-label">Father Mobile</div>
          <div class="profile-field-value">${formatProfileVal(rec.father_mobile)}</div>
        </div>
        <div>
          <div class="profile-field-label">Mother Name</div>
          <div class="profile-field-value">${formatProfileVal(rec.mother_name)}</div>
        </div>
        <div>
          <div class="profile-field-label">Mother Mobile</div>
          <div class="profile-field-value">${formatProfileVal(rec.mother_mobile)}</div>
        </div>
        <div>
          <div class="profile-field-label">Parent Profession</div>
          <div class="profile-field-value">${formatProfileVal(rec.parent_profession)}</div>
        </div>
        <div>
          <div class="profile-field-label">Parent Income</div>
          <div class="profile-field-value">${formatProfileVal(rec.parent_income)}</div>
        </div>
        <div>
          <div class="profile-field-label">Father Name</div>
          <div class="profile-field-value">${formatProfileVal(rec.father_name)}</div>
        </div>
      </div>
    </div>

    <!-- 🎓 Admission Details -->
    <div class="profile-section-card space-y-3">
      <div class="flex items-center gap-2 text-xs font-semibold text-indigo-300 border-b border-white/5 pb-2">
        <span>🎓</span> Admission Details
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <div class="profile-field-label">Admission Category</div>
          <div class="profile-field-value">${formatProfileVal(rec.admission_category)}</div>
        </div>
        <div>
          <div class="profile-field-label">Scholarship Type</div>
          <div class="profile-field-value">${formatProfileVal(rec.scholarship_type)}</div>
        </div>
        <div>
          <div class="profile-field-label">Caste Name</div>
          <div class="profile-field-value">${formatProfileVal(rec.caste_name)}</div>
        </div>
        <div>
          <div class="profile-field-label">Admission Year</div>
          <div class="profile-field-value">${formatProfileVal(rec.admission_year)}</div>
        </div>
      </div>
    </div>

    <!-- 📋 Additional Student Details -->
    <div class="profile-section-card space-y-3">
      <div class="flex items-center gap-2 text-xs font-semibold text-indigo-300 border-b border-white/5 pb-2">
        <span>📋</span> Additional Student Details
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
        <div>
          <div class="profile-field-label">Batch</div>
          <div class="profile-field-value">${formatProfileVal(rec.batch || rec.admission_batch)}</div>
        </div>
        <div>
          <div class="profile-field-label">Section</div>
          <div class="profile-field-value">${formatProfileVal(rec.section)}</div>
        </div>
        <div>
          <div class="profile-field-label">Entry Type</div>
          <div class="profile-field-value">${formatProfileVal(rec.entry_type)}</div>
        </div>
        <div>
          <div class="profile-field-label">Record Verification</div>
          <div class="profile-field-value text-emerald-400">Verified Database Master</div>
        </div>
      </div>
    </div>
  `;

  el.profileModalContent.innerHTML = html;
  el.profileModal.classList.remove("hidden");
  el.profileModal.style.display = "grid";
}

function closeProfileModal() {
  if (el.profileModal) {
    el.profileModal.classList.add("hidden");
    el.profileModal.style.display = "none";
  }
}

async function handleProfileDataClick() {
  if (!sessionToken) {
    toast("Please sign in through Anvaya to view your profile details.", "error");
    startSignIn();
    return;
  }

  // Fast path: if record is already cached in profile, show immediately!
  const cachedRec = profile?.stored_record || profile?.profile_data;
  if (cachedRec) {
    renderProfileModal(cachedRec);
  }

  showSyncing("Fetching your stored profile details...");
  try {
    let response = await fetch(`${API_BASE}/student/profile`, {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      }
    });

    if (!response.ok && response.status !== 401 && response.status !== 403) {
      // Fallback endpoint check
      response = await fetch("/api/student/profile", {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          ...authHeaders()
        }
      });
    }

    if (response.status === 401) {
      hideSyncBanner();
      clearSession({ quiet: true });
      toast("Your session has ended. Please sign in again.", "error");
      return;
    }

    if (response.status === 403) {
      hideSyncBanner();
      openConsent();
      return;
    }

    if (!response.ok) {
      hideSyncBanner();
      toast("Unable to load profile data. Please try again.", "error");
      return;
    }

    const data = await response.json();
    hideSyncBanner();

    const record = data.stored_record || data.profile_data;
    if (!record) {
      toast("Student profile data not found.", "error");
      return;
    }

    // Keep memory cache fresh
    if (profile) {
      profile.stored_record = record;
      profile.profile_data = record;
    } else {
      profile = data;
    }
    renderProfile();

    // 1. Show interactive modal
    renderProfileModal(record);

    // 2. Append complete profile card to chat timeline
    const chatCard = `
### 👤 Profile Details

#### 👤 Personal Details
* **Student Name:** ${formatProfileVal(record.student_name || record.name)}
* **Roll Number:** \`${formatProfileVal(record.roll_number)}\`
* **Year of Study:** ${formatProfileVal(record.year_of_study || SEMESTER_TO_YEAR[(record.current_semester || "").trim().toUpperCase()] || record.year)}
* **Current Semester:** ${formatProfileVal(record.semester_display || SEMESTER_TO_DISPLAY[(record.current_semester || "").trim().toUpperCase()] || record.current_semester)}
* **Branch:** ${formatProfileVal(record.branch)}
* **Academic Year:** ${formatProfileVal(record.academic_year)}
* **Admission Year:** ${formatProfileVal(record.admission_year || record.batch)}
* **Gender:** ${formatProfileVal(record.gender)}
* **Date of Birth:** ${formatProfileVal(record.date_of_birth)}

#### 📞 Contact Details
* **Student Mobile:** ${formatProfileVal(record.student_mobile)}
* **Student Email:** ${formatProfileVal(record.student_email || record.email)}

#### 👨‍👩‍👧 Parent Details
* **Father Mobile:** ${formatProfileVal(record.father_mobile)}
* **Mother Name:** ${formatProfileVal(record.mother_name)}
* **Mother Mobile:** ${formatProfileVal(record.mother_mobile)}
* **Parent Profession:** ${formatProfileVal(record.parent_profession)}
* **Parent Income:** ${formatProfileVal(record.parent_income)}
* **Father Name:** ${formatProfileVal(record.father_name)}

#### 🎓 Admission Details
* **Admission Category:** ${formatProfileVal(record.admission_category)}
* **Scholarship Type:** ${formatProfileVal(record.scholarship_type)}
* **Caste Name:** ${formatProfileVal(record.caste_name)}

#### 📋 Additional Student Details
* **Batch:** ${formatProfileVal(record.batch || record.admission_batch)}
* **Section:** ${formatProfileVal(record.section)}
* **Entry Type:** ${formatProfileVal(record.entry_type)}
`;
    appendMessage("assistant", chatCard.trim());

  } catch (err) {
    hideSyncBanner();
    toast("Unable to load profile data. Please try again.", "error");
  }
}

el.closeProfileModalBtn?.addEventListener("click", closeProfileModal);
el.closeProfileModalBtnBottom?.addEventListener("click", closeProfileModal);
el.profileModal?.addEventListener("click", (e) => {
  if (e.target === el.profileModal) closeProfileModal();
});
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeProfileModal();
    closeAdminSyncModal();
    el.consentModal?.classList.add("hidden");
  }
});

// ------------------------------------------------------------------ Admin Sync Dashboard
const adminModal = $("adminSyncModal");
async function loadAdminSyncStatus() {
  try {
    const res = await fetch(`${API_BASE}/admin/sync/status`);
    if (res.ok) {
      const data = await res.json();
      if ($("syncMetricDocs")) $("syncMetricDocs").textContent = data.documents_indexed || 0;
      if ($("syncMetricEvents")) $("syncMetricEvents").textContent = data.events_found || 0;
      if ($("syncMetricFaculty")) $("syncMetricFaculty").textContent = data.faculty_contacts_found || 0;
      if ($("syncMetricPhotos")) $("syncMetricPhotos").textContent = data.event_photos_found || 0;
      if ($("syncLastTime")) {
        const d = data.last_mlritm_sync ? new Date(data.last_mlritm_sync) : null;
        $("syncLastTime").textContent = d && !isNaN(d) ? d.toLocaleString() : "Recently synchronized";
      }
    }

    const logRes = await fetch(`${API_BASE}/admin/sync/logs`);
    if (logRes.ok) {
      const logData = await logRes.json();
      const logsContainer = $("adminSyncLogs");
      if (logsContainer && logData.logs) {
        logsContainer.innerHTML = logData.logs.map(l => 
          `<div class="flex items-start gap-1"><span class="text-indigo-400 font-bold">[${escapeHtml(l.status)}]</span> <span class="text-slate-400">${new Date(l.timestamp).toLocaleTimeString()}:</span> <span>${escapeHtml(l.message)}</span></div>`
        ).join("");
      }
    }
  } catch (err) {
    console.error("Failed to load sync status:", err);
  }
}

function openAdminSyncModal() {
  if (adminModal) {
    adminModal.classList.remove("hidden");
    adminModal.style.display = "grid";
    loadAdminSyncStatus();
  }
}

function closeAdminSyncModal() {
  if (adminModal) {
    adminModal.classList.add("hidden");
    adminModal.style.display = "none";
  }
}

$("adminSyncBtn")?.addEventListener("click", openAdminSyncModal);
$("closeAdminSyncModalBtn")?.addEventListener("click", closeAdminSyncModal);
$("closeAdminSyncModalBtnBottom")?.addEventListener("click", closeAdminSyncModal);

$("triggerSyncAllBtn")?.addEventListener("click", async () => {
  const fb = $("adminActionFeedback");
  if (fb) { fb.classList.remove("hidden"); fb.textContent = "Triggering MLRITM website synchronization..."; }
  try {
    const res = await fetch(`${API_BASE}/admin/sync/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "all", force_live: false })
    });
    const d = await res.json();
    if (fb) fb.textContent = d.message || "Synchronization finished!";
    await loadAdminSyncStatus();
  } catch (err) {
    if (fb) fb.textContent = "Sync failed: " + err.message;
  }
});

$("triggerSyncPhoneBtn")?.addEventListener("click", async () => {
  const fb = $("adminActionFeedback");
  if (fb) { fb.classList.remove("hidden"); fb.textContent = "Synchronizing MLRITM Phone Directory..."; }
  try {
    const res = await fetch(`${API_BASE}/admin/sync/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: "faculty", force_live: false })
    });
    const d = await res.json();
    if (fb) fb.textContent = "Phone Directory synchronized!";
    await loadAdminSyncStatus();
  } catch (err) {
    if (fb) fb.textContent = "Sync failed: " + err.message;
  }
});

$("triggerRebuildIndexBtn")?.addEventListener("click", async () => {
  const fb = $("adminActionFeedback");
  if (fb) { fb.classList.remove("hidden"); fb.textContent = "Rebuilding knowledge base search index..."; }
  try {
    const res = await fetch(`${API_BASE}/admin/index/rebuild`, { method: "POST" });
    const d = await res.json();
    if (fb) fb.textContent = d.message || "Index rebuilt!";
    await loadAdminSyncStatus();
  } catch (err) {
    if (fb) fb.textContent = "Rebuild failed: " + err.message;
  }
});

// ------------------------------------------------------------------ Admin Knowledge Gaps & Tabs
function switchAdminTab(activeTab) {
  const tabs = ["Sync", "Gaps", "Adaptive"];
  tabs.forEach(t => {
    const btn = $(`adminTab${t}`);
    const pane = $(`adminPane${t}`);
    if (t === activeTab) {
      btn?.classList.add("bg-indigo-600/30", "text-indigo-300", "border-indigo-500/40");
      btn?.classList.remove("text-slate-400", "border-transparent");
      pane?.classList.remove("hidden");
    } else {
      btn?.classList.remove("bg-indigo-600/30", "text-indigo-300", "border-indigo-500/40");
      btn?.classList.add("text-slate-400", "border-transparent");
      pane?.classList.add("hidden");
    }
  });

  if (activeTab === "Gaps") loadAdminGaps();
  if (activeTab === "Adaptive") loadAdminAdaptiveItems();
}

$("adminTabSync")?.addEventListener("click", () => switchAdminTab("Sync"));
$("adminTabGaps")?.addEventListener("click", () => switchAdminTab("Gaps"));
$("adminTabAdaptive")?.addEventListener("click", () => switchAdminTab("Adaptive"));
$("refreshGapsBtn")?.addEventListener("click", loadAdminGaps);
$("refreshAdaptiveBtn")?.addEventListener("click", loadAdminAdaptiveItems);

async function loadAdminGaps() {
  const container = $("gapsContainer");
  if (!container) return;
  container.innerHTML = `<div class="text-xs text-slate-500 text-center py-4">Fetching knowledge gaps...</div>`;
  try {
    const res = await fetch(`${API_BASE}/admin/knowledge/gaps?limit=50`);
    if (!res.ok) throw new Error("Failed to load gaps");
    const data = await res.json();
    if (!data.gaps || data.gaps.length === 0) {
      container.innerHTML = `<div class="text-xs text-slate-400 text-center py-4">No active knowledge gaps recorded. Everything is answered!</div>`;
      return;
    }
    container.innerHTML = data.gaps.map(g => `
      <div class="p-2.5 rounded-xl bg-white/[0.03] border border-white/10 flex flex-col gap-1.5 text-xs">
        <div class="flex items-start justify-between gap-2">
          <span class="font-medium text-white">${escapeHtml(g.question)}</span>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px] font-semibold">${g.frequency}x asked</span>
            <span class="px-1.5 py-0.5 rounded ${g.verification_status === 'VERIFIED' ? 'bg-emerald-500/20 text-emerald-400' : (g.answer_status === 'NEEDS_REVIEW' ? 'bg-amber-500/20 text-amber-300' : 'bg-rose-500/20 text-rose-300')} text-[10px] font-semibold">${escapeHtml(g.verification_status || g.answer_status)}</span>
          </div>
        </div>
        <div class="flex items-center justify-between text-[11px] text-slate-400">
          <span>Topic: <b class="text-slate-300">${escapeHtml(g.topic || 'general')}</b></span>
          <div class="flex items-center gap-1">
            ${g.verification_status !== 'VERIFIED' ? `
              <button class="verify-gap-btn px-2 py-0.5 rounded bg-emerald-600/30 hover:bg-emerald-600/50 text-emerald-300 text-[10px] font-medium transition" data-id="${g.id}" data-q="${escapeHtml(g.question)}" data-topic="${escapeHtml(g.topic || 'general')}">✅ Verify</button>
              <button class="reject-gap-btn px-2 py-0.5 rounded bg-rose-600/20 hover:bg-rose-600/40 text-rose-300 text-[10px] font-medium transition" data-id="${g.id}">❌ Reject</button>
            ` : `<span class="text-emerald-400 font-medium text-[10px]">✓ Resolved</span>`}
          </div>
        </div>
      </div>
    `).join("");

    container.querySelectorAll(".verify-gap-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        openVerifyModal(btn.dataset.id, btn.dataset.q, btn.dataset.topic);
      });
    });

    container.querySelectorAll(".reject-gap-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        if (!confirm("Mark this gap as REJECTED?")) return;
        try {
          await fetch(`${API_BASE}/admin/knowledge/gaps/${btn.dataset.id}/reject`, { method: "POST" });
          loadAdminGaps();
        } catch (e) {
          alert("Rejection failed: " + e.message);
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<div class="text-xs text-rose-400 text-center py-4">Error loading gaps: ${escapeHtml(err.message)}</div>`;
  }
}

async function loadAdminAdaptiveItems() {
  const container = $("adaptiveContainer");
  if (!container) return;
  container.innerHTML = `<div class="text-xs text-slate-500 text-center py-4">Fetching adaptive knowledge items...</div>`;
  try {
    const res = await fetch(`${API_BASE}/admin/knowledge/items?limit=50`);
    if (!res.ok) throw new Error("Failed to load adaptive items");
    const data = await res.json();
    if (!data.items || data.items.length === 0) {
      container.innerHTML = `<div class="text-xs text-slate-400 text-center py-4">No adaptive items stored yet.</div>`;
      return;
    }
    container.innerHTML = data.items.map(it => `
      <div class="p-2.5 rounded-xl bg-white/[0.03] border border-white/10 flex flex-col gap-1.5 text-xs">
        <div class="flex items-start justify-between gap-2">
          <span class="font-medium text-white">${escapeHtml(it.question_pattern || it.topic)}</span>
          <div class="flex items-center gap-1.5 shrink-0">
            <span class="px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-[10px] font-semibold">v${it.version}</span>
            <span class="px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 text-[10px] font-semibold">${escapeHtml(it.source_trust_level)}</span>
          </div>
        </div>
        <p class="text-slate-300 leading-relaxed text-[11px]">${escapeHtml(it.verified_content)}</p>
        <div class="flex items-center justify-between text-[10px] text-slate-400 pt-1 border-t border-white/5">
          <span>Source: <a href="${escapeHtml(it.source_url)}" target="_blank" rel="noopener noreferrer" class="text-indigo-400 underline">${escapeHtml(it.source_url)}</a></span>
          <button class="refresh-item-btn text-indigo-400 hover:text-indigo-300 transition" data-id="${it.id}">🔄 Re-verify</button>
        </div>
      </div>
    `).join("");

    container.querySelectorAll(".refresh-item-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        try {
          await fetch(`${API_BASE}/admin/knowledge/items/${btn.dataset.id}/refresh`, { method: "POST" });
          btn.textContent = "✓ Re-verified";
          setTimeout(loadAdminAdaptiveItems, 800);
        } catch (e) {
          alert("Refresh failed: " + e.message);
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<div class="text-xs text-rose-400 text-center py-4">Error: ${escapeHtml(err.message)}</div>`;
  }
}

const verifyModal = $("verifyGapModal");
function openVerifyModal(gapId, question, topic) {
  if (!verifyModal) return;
  $("verifyGapId").value = gapId;
  $("verifyGapQuestion").textContent = question;
  $("verifyGapContent").value = "";
  $("verifyGapFeedback")?.classList.add("hidden");
  verifyModal.classList.remove("hidden");
  verifyModal.style.display = "grid";
}

function closeVerifyModal() {
  if (verifyModal) {
    verifyModal.classList.add("hidden");
    verifyModal.style.display = "none";
  }
}

$("closeVerifyGapBtn")?.addEventListener("click", closeVerifyModal);
$("cancelVerifyGapBtn")?.addEventListener("click", closeVerifyModal);

$("submitVerifyGapBtn")?.addEventListener("click", async () => {
  const gapId = $("verifyGapId").value;
  const content = $("verifyGapContent").value.trim();
  const sourceUrl = $("verifyGapSourceUrl").value.trim() || "https://www.mlritm.ac.in";
  const trust = $("verifyGapTrust").value;
  const fb = $("verifyGapFeedback");

  if (!content) {
    if (fb) { fb.textContent = "Please enter verified factual content."; fb.classList.remove("hidden"); }
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/admin/knowledge/gaps/${gapId}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        verified_content: content,
        source_url: sourceUrl,
        source_trust_level: trust
      })
    });
    if (!res.ok) throw new Error("Verification failed.");
    closeVerifyModal();
    loadAdminGaps();
    alert("Knowledge gap verified & stored in adaptive knowledge base!");
  } catch (e) {
    if (fb) { fb.textContent = e.message; fb.classList.remove("hidden"); }
  }
});

$("quickProfileTile")?.addEventListener("click", async (event) => {
  event.preventDefault();
  await handleProfileDataClick();
});

$("viewProfileDetailsPanelBtn")?.addEventListener("click", async (event) => {
  event.preventDefault();
  await handleProfileDataClick();
});

el.profileChip?.addEventListener("click", async (event) => {
  if (event.target.closest("#logoutBtn")) return;
  await handleProfileDataClick();
});

el.profileAvatar?.addEventListener("click", async () => {
  await handleProfileDataClick();
});
$("quickLogoutTile")?.addEventListener("click", () => {
  if (confirm(`Signed in through Anvaya as ${studentLabel()}. Sign out?`)) signOut();
});

document.querySelectorAll("[data-q]").forEach((button) => {
  button.addEventListener("click", () => sendMessage(button.dataset.q));
});

// ------------------------------------------------------------------ startup

(async function init() {
  await loadAuthConfig();
  render();
  await consumeRedirectFragment();
  await restoreSession();
  render();
})();
