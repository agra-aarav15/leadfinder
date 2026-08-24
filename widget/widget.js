/* LeadFinder live chat widget.
   Embed with one line:  <script src="http://YOUR-SERVER:8787/widget.js"></script> */
(function () {
  "use strict";
  var SERVER = "__SERVER__";
  var VISITOR_KEY = "lf_visitor_id";

  function visitorId() {
    var id;
    try { id = localStorage.getItem(VISITOR_KEY); } catch (e) {}
    if (!id) {
      id = "v_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      try { localStorage.setItem(VISITOR_KEY, id); } catch (e) {}
    }
    return id;
  }

  var css = [
    ".lfw-bubble{position:fixed;bottom:26px;right:26px;width:58px;height:58px;border-radius:50%;",
    "border:1px solid rgba(255,255,255,.14);cursor:pointer;z-index:999999;",
    "background:rgba(28,28,30,.72);backdrop-filter:saturate(170%) blur(20px);-webkit-backdrop-filter:saturate(170%) blur(20px);",
    "box-shadow:0 10px 34px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.09);",
    "font-size:24px;color:#fff;display:flex;align-items:center;justify-content:center;transition:transform .25s cubic-bezier(.25,.1,.25,1)}",
    ".lfw-bubble:hover{transform:scale(1.07)}",
    ".lfw-panel{position:fixed;bottom:98px;right:26px;width:348px;height:486px;z-index:999999;display:none;flex-direction:column;overflow:hidden;",
    "background:rgba(16,16,18,.72);backdrop-filter:saturate(170%) blur(30px);-webkit-backdrop-filter:saturate(170%) blur(30px);",
    "border:1px solid rgba(255,255,255,.09);border-radius:22px;box-shadow:0 24px 70px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.06);",
    "font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',sans-serif;color:#f5f5f7}",
    ".lfw-open .lfw-panel{display:flex}",
    ".lfw-head{padding:16px 18px 13px;font-weight:600;font-size:14.5px;letter-spacing:-.01em}",
    ".lfw-head small{display:flex;align-items:center;gap:6px;font-weight:400;font-size:11.5px;color:#86868b;margin-top:3px;letter-spacing:.02em}",
    ".lfw-head small i{width:6px;height:6px;border-radius:50%;background:#30d158;display:inline-block}",
    ".lfw-msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}",
    ".lfw-msgs::-webkit-scrollbar{width:8px}.lfw-msgs::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:99px;border:2.5px solid transparent;background-clip:content-box}",
    ".lfw-msg{max-width:84%;padding:10px 13px;border-radius:17px;font-size:13.5px;line-height:1.45;white-space:pre-wrap;word-break:break-word}",
    ".lfw-agent{background:rgba(255,255,255,.08);color:#f5f5f7;border-bottom-left-radius:5px;align-self:flex-start}",
    ".lfw-user{background:linear-gradient(135deg,#0a84ff,#0a6eeb);color:#fff;border-bottom-right-radius:5px;align-self:flex-end}",
    ".lfw-typing{font-size:18px;letter-spacing:3px;padding:9px 15px;align-self:flex-start;color:#98989d;background:rgba(255,255,255,.08);border-radius:17px;border-bottom-left-radius:5px}",
    ".lfw-input{display:flex;border-top:1px solid rgba(255,255,255,.07);background:rgba(255,255,255,.03)}",
    ".lfw-input input{flex:1;border:none;outline:none;background:none;padding:14px 15px;font-size:13.5px;color:#f5f5f7;font-family:inherit}",
    ".lfw-input input::placeholder{color:#5a5a5f}",
    ".lfw-input button{border:none;background:none;color:#0a84ff;font-weight:600;padding:0 18px;cursor:pointer;font-size:13.5px;font-family:inherit}"
  ].join("");

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var root = document.createElement("div");
  root.className = "";
  root.innerHTML =
    '<button class="lfw-bubble" aria-label="Chat with us">💬</button>' +
    '<div class="lfw-panel">' +
    '  <div class="lfw-head">Chat with us<small><i></i>We typically reply instantly</small></div>' +
    '  <div class="lfw-msgs" id="lfw-msgs"></div>' +
    '  <div class="lfw-input"><input id="lfw-in" type="text" placeholder="Type a message…"/>' +
    '  <button id="lfw-send">Send</button></div>' +
    "</div>";
  document.body.appendChild(root);

  var msgsEl = root.querySelector("#lfw-msgs");
  var inputEl = root.querySelector("#lfw-in");
  var panelOpen = false;

  function addMsg(text, who) {
    var d = document.createElement("div");
    d.className = "lfw-msg lfw-" + who;
    d.textContent = text;
    msgsEl.appendChild(d);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  var typingEl = null;
  function showTyping(on) {
    if (on && !typingEl) {
      typingEl = document.createElement("div");
      typingEl.className = "lfw-agent lfw-typing";
      typingEl.textContent = "•••";
      msgsEl.appendChild(typingEl);
      msgsEl.scrollTop = msgsEl.scrollHeight;
    } else if (!on && typingEl) {
      typingEl.remove();
      typingEl = null;
    }
  }

  var ws;
  function connect() {
    var proto = SERVER.indexOf("https") === 0 ? "wss" : "ws";
    ws = new WebSocket(proto + "://" + SERVER.replace(/^https?:\/\//, "") + "/ws/" + visitorId());
    ws.onmessage = function (ev) {
      showTyping(false);
      try {
        var data = JSON.parse(ev.data);
        if (data.from === "agent") addMsg(data.text, "agent");
      } catch (e) {}
    };
    ws.onclose = function () { setTimeout(connect, 4000); }; // auto-reconnect
  }
  connect();

  function send() {
    var t = inputEl.value.trim();
    if (!t || !ws || ws.readyState !== 1) return;
    addMsg(t, "user");
    inputEl.value = "";
    showTyping(true);
    ws.send(t);
  }
  root.querySelector("#lfw-send").addEventListener("click", send);
  inputEl.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

  root.querySelector(".lfw-bubble").addEventListener("click", function () {
    panelOpen = !panelOpen;
    root.className = panelOpen ? "lfw-open" : "";
    if (panelOpen) inputEl.focus();
  });
})();
