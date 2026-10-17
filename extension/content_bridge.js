/**
 * Isolated Content Bridge
 * Bridges messages between page_agent.js (MAIN world) and background.js (Extension)
 */

window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data || !event.data.ps_jev_req) return;

  const req = event.data;

  // Check if extension context is still valid
  if (!chrome || !chrome.runtime || !chrome.runtime.id) {
    window.postMessage(
      {
        ps_jev_res: true,
        reqId: req.reqId,
        type: req.type,
        response: { ok: false, error: "Extension context invalidated, please refresh page", fallback: true }
      },
      "*"
    );
    return;
  }

  try {
    chrome.runtime.sendMessage(
      { type: req.type, payload: req.payload },
      (response) => {
        const lastErr = chrome.runtime && chrome.runtime.lastError;
        window.postMessage(
          {
            ps_jev_res: true,
            reqId: req.reqId,
            type: req.type,
            response: lastErr
              ? { ok: false, error: lastErr.message, fallback: true }
              : (response || { ok: false, fallback: true })
          },
          "*"
        );
      }
    );
  } catch (err) {
    window.postMessage(
      {
        ps_jev_res: true,
        reqId: req.reqId,
        type: req.type,
        response: { ok: false, error: err.message, fallback: true }
      },
      "*"
    );
  }
});

// Notify page_agent that content bridge is active
try {
  window.postMessage({ ps_bridge_ready: true }, "*");
} catch (e) {}
