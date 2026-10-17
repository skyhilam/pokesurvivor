/**
 * Background Service Worker for PokéSurvivor Jev Bot
 * Forwards requests from content script to local Jev Bridge (http://127.0.0.1:8765)
 */

const BRIDGE_URL = "http://127.0.0.1:8765";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return false;

  const endpointMap = {
    "JEV_HEALTH": "/health",
    "JEV_MEMORY": "/knowledge",
    "JEV_RUN_START": "/run/start",
    "JEV_CHECKPOINT": "/run/checkpoint",
    "JEV_RUN_END": "/run/end",
    "JEV_OPPORTUNITY": "/decide/opportunity",
    "JEV_OPP_OUTCOME": "/opportunity/outcome",
    "JEV_DISCOVER": "/discover",
    "JEV_META": "/decide/meta",
    "JEV_UPGRADE": "/decide/upgrade",
    "JEV_LEVELUP": "/decide/levelup",
    "JEV_ITEM": "/decide/item",
    "JEV_BOND": "/decide/bond",
    "JEV_TACTICS": "/decide/tactics",
    "JEV_POSTMORTEM": "/decide/postmortem",
    "JEV_TELEMETRY": "/telemetry"
  };

  const endpoint = endpointMap[message.type];
  if (!endpoint) return false;

  const isPost = message.type !== "JEV_HEALTH" && message.type !== "JEV_MEMORY";
  const url = `${BRIDGE_URL}${endpoint}`;

  const fetchOptions = {
    method: isPost ? "POST" : "GET",
    headers: isPost ? { "Content-Type": "application/json" } : {},
    body: isPost ? JSON.stringify(message.payload || {}) : undefined,
  };

  fetch(url, fetchOptions)
    .then(async (res) => {
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      sendResponse({ ok: true, data });
    })
    .catch((err) => {
      // Return fallback without crashing
      sendResponse({ ok: false, error: err.message, fallback: true });
    });

  return true; // Keep channel open for async sendResponse
});
