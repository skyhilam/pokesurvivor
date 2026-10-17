document.addEventListener("DOMContentLoaded", () => {
  const bridgeStatus = document.getElementById("bridge-status");
  const keyStatus = document.getElementById("key-status");
  const modelName = document.getElementById("model-name");
  const callCount = document.getElementById("call-count");
  const openBtn = document.getElementById("open-game-btn");

  // Check bridge health
  chrome.runtime.sendMessage({ type: "JEV_HEALTH" }, (res) => {
    if (res && res.ok && res.data) {
      bridgeStatus.textContent = "CONNECTED";
      bridgeStatus.className = "val-good";
      keyStatus.textContent = res.data.typesafe_key_present ? "Active" : "Missing";
      keyStatus.className = res.data.typesafe_key_present ? "val-good" : "val-warn";
      modelName.textContent = res.data.model || "jev-latest";
      callCount.textContent = (res.data.stats && res.data.stats.total_calls) || 0;
    } else {
      bridgeStatus.textContent = "OFFLINE (Port 8765)";
      bridgeStatus.className = "val-warn";
      keyStatus.textContent = "Unknown";
    }
  });

  if (openBtn) {
    openBtn.onclick = () => {
      chrome.tabs.create({ url: "https://pokesurvivor.com/en/" });
    };
  }
});
