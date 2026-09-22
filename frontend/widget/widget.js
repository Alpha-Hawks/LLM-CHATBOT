/**
 * MLRITM Academic Advising Floating Widget.
 * Self-contained Web Component using Shadow DOM to isolate styles and prevent
 * conflicts with Anvaya's legacy CSS / JS.
 * Usage on Anvaya portal:
 *   <script src="https://chatbot.mlritm.ac.in/widget/widget.js" defer></script>
 */

(function () {
  if (customElements.get("mlritm-chat-widget")) return;

  // The chatbot server is wherever this script was loaded from, so the one-line embed
  // works on anvaya.mlritm.ac.in without extra configuration
  const scriptOrigin = document.currentScript && document.currentScript.src
    ? new URL(document.currentScript.src, window.location.href).origin
    : null;

  const template = document.createElement("template");
  template.innerHTML = `
    <style>
      :host {
        --primary: #4f46e5;
        --primary-hover: #4338ca;
        --bg-dark: #0f172a;
        --card-bg: #1e293b;
        --border-color: #334155;
        --text-color: #f8fafc;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 999999;
      }

      .chat-bubble {
        width: 56px;
        height: 56px;
        border-radius: 50%;
        background: linear-gradient(135deg, #6366f1, #4f46e5);
        color: white;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        box-shadow: 0 10px 25px -5px rgba(79, 70, 229, 0.4);
        transition: transform 0.2s, box-shadow 0.2s;
      }

      .chat-bubble:hover {
        transform: scale(1.05);
        box-shadow: 0 15px 30px -5px rgba(79, 70, 229, 0.5);
      }

      .chat-bubble svg {
        width: 26px;
        height: 26px;
      }

      .chat-window {
        display: none;
        position: fixed;
        bottom: 90px;
        right: 24px;
        width: 400px;
        height: min(640px, calc(100vh - 130px));
        background: var(--bg-dark);
        border: 1px solid var(--border-color);
        border-radius: 16px;
        box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
        flex-direction: column;
        overflow: hidden;
      }

      .chat-window.open {
        display: flex;
      }

      .chat-header {
        padding: 14px 16px;
        background: var(--card-bg);
        border-bottom: 1px solid var(--border-color);
        display: flex;
        align-items: center;
        justify-content: space-between;
        color: var(--text-color);
      }

      .chat-header h4 {
        margin: 0;
        font-size: 14px;
        font-weight: 600;
        display: flex;
        align-items: center;
        gap: 6px;
      }

      .close-btn {
        background: none;
        border: none;
        color: #94a3b8;
        font-size: 20px;
        cursor: pointer;
      }

      .chat-iframe {
        flex: 1;
        width: 100%;
        height: 100%;
        border: none;
      }
    </style>

    <div class="chat-window" id="widgetWindow">
      <div class="chat-header">
        <h4><span>🎓</span> MLRITM Student AI Assistant</h4>
        <button class="close-btn" id="closeBtn">&times;</button>
      </div>
      <iframe class="chat-iframe" id="chatFrame" title="MLRITM Student AI Assistant"></iframe>
    </div>

    <div class="chat-bubble" id="widgetBubble" title="Ask Academic Advisor">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
      </svg>
    </div>
  `;

  class MLRITMChatWidget extends HTMLElement {
    constructor() {
      super();
      const shadow = this.attachShadow({ mode: "open" });
      shadow.appendChild(template.content.cloneNode(true));

      this.bubble = shadow.getElementById("widgetBubble");
      this.window = shadow.getElementById("widgetWindow");
      this.closeBtn = shadow.getElementById("closeBtn");
      this.frame = shadow.getElementById("chatFrame");

      // Set target backend URL (host attribute > script origin > local dev server)
      const host = this.getAttribute("host") || scriptOrigin || "http://localhost:8000";
      // When Anvaya embeds the widget for a signed-in student it can pass a freshly minted,
      // single-use launch URL (/api/v1/auth/launch?launch_token=...) so the chat opens signed in
      const launchUrl = this.getAttribute("launch-url");
      this.frameSrc = launchUrl && launchUrl.startsWith(`${host}/`) ? launchUrl : `${host}/app/`;

      this.isOpen = false;
      this.initEvents();
    }

    initEvents() {
      this.bubble.addEventListener("click", () => this.toggle());
      this.closeBtn.addEventListener("click", () => this.toggle());
    }

    toggle() {
      this.isOpen = !this.isOpen;
      if (this.isOpen) {
        // Load on first open. Checked via the attribute: an iframe with no src still reports
        // this page's URL through the .src property, which would look like "already loaded".
        if (!this.frame.getAttribute("src")) {
          this.frame.setAttribute("src", this.frameSrc);
        }
        this.window.classList.add("open");
      } else {
        this.window.classList.remove("open");
      }
    }
  }

  customElements.define("mlritm-chat-widget", MLRITMChatWidget);

  // Auto-inject tag if script is loaded
  const existing = document.querySelector("mlritm-chat-widget");
  if (!existing) {
    const el = document.createElement("mlritm-chat-widget");
    document.body.appendChild(el);
  }
})();
