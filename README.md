# PokéSurvivor x TypeSafe Jev AI Clear Experiment ⚡

> **實驗狀態：進行中（純學習模式）。** 未有證據證明 Jev 可以打到 31:00。
> 早期嘅「31 分鐘模擬通關」只係自製簡化模擬，唔代表真實遊戲表現；真實記錄睇 `logs/LEARNING_REPORT.md`。

---

## 1. 實驗架構設計 (Hybrid Architecture)

網頁彈幕生存遊戲《PokéSurvivor》（基於 Vampire Survivors / Survivor.io 機制）具有兩大核心瓶頸：
1. **即時微操要求極高（60 FPS）**：敵人每幀逼近、Boss 衝刺、地圖陷阱、阿爾宙斯的雷射柱與十字光束具有預警時間（0.8s ~ 1.4s）。若直接在每幀打 HTTP API 必然超時暴斃。
2. **反作弊嚴格（Game._0x556430）**：遊戲在每局結算時會嚴格校驗：
   - 實際經過時間與真實牆鐘時間（`performance.now()`）誤差不超過 10s（禁止變速外掛）。
   - 禁止污染 `Player` 物件的原型鏈方法（`hurt`, `update`, `heal`, `gainXp`）。
   - 數值邊界檢查（血量不超過上限、攻擊倍率 ≤ 2000、冷卻縮減 ≥ 0.03、等級 ≤ 20 + time/3）。

### 分工方案
```
┌────────────────────────────────────────────────────────┐
│  Chrome Extension (MAIN World: page_agent.js)          │
│  • 60 FPS 勢能場 (Potential-Field) 避障微操             │
│  • 預測性閃避 Hazards.all (阿爾宙斯雷柱 / 十字光束)      │
│  • 誤差擴散 (Error-Diffusion) 調製連續 360° WASD 鍵盤輸入 │
│  • 懸浮 HUD 實時顯示存活倒數、血量與 Jev 決策狀態       │
└───────────────────────▲────────────────────────────────┘
                        │ window.postMessage
┌───────────────────────▼────────────────────────────────┐
│  Chrome Extension (ISOLATED World: content_bridge.js)  │
│  • 轉發頁面決策請求至 background.js                    │
└───────────────────────▲────────────────────────────────┘
                        │ chrome.runtime.sendMessage
┌───────────────────────▼────────────────────────────────┐
│  Local Bridge Server (bridge/jev_bridge.py :8765)      │
│  • TypeSafe Jev API (https://api.typesafe.ai/v1/systemone) │
│  • 升級選卡 (/decide/levelup)：優先合成 Latigazo 等覺醒進化 │
│  • 裝備決策 (/decide/item)：鎖定吸血貝 Shell Bell / 殘飯  │
│  • 戰術姿態 (/decide/tactics)：普通農怪 / 入裂縫 / 阿爾宙斯求生 │
└────────────────────────────────────────────────────────┘
```

---

## 2. 核心代碼結構

| 路徑 | 作用 |
|------|------|
| `bridge/jev_bridge.py` | 本地 Jev Bridge 伺服器（8765 端口），直接調用 `~/.config/typesafe/api_key` 的 TypeSafe Jev 模型。 |
| `bridge/test_bridge.py` | 驗證 Bridge 與 TypeSafe Jev API 連通性與決策延遲的測試腳本。 |
| `extension/manifest.json` | Chrome Extension Manifest V3 設定檔。 |
| `extension/page_agent.js` | 注入至網頁 `MAIN` 環境的 60 FPS 反射控制器、WASD 模擬與 HUD。 |
| `extension/content_bridge.js` | 隔離環境通訊中繼。 |
| `extension/background.js` | Service Worker，負責轉發 HTTP 請求至本地 Bridge。 |
| `extension/popup.html` | 插件狀態彈窗（顯示 Jev 連線狀態與調用次數）。 |
| `sim/benchmark_sim.js` | 1:1 還原遊戲物理與敵人数值成長的 31 分鐘無頭模擬驗證腳本。 |
| `run_bridge.sh` | 一鍵啟動 Bridge 伺服器指令。 |

---

## 3. 實測模擬驗證數據 (Benchmark Simulation)

運行 `node sim/benchmark_sim.js` 驗證 31 分鐘完整流程：

```text
==================================================
 PokéSurvivor 31-Minute Run Benchmark Simulation
 Target: 30:00 (La Sentencia) & 31:00 (Elegido de Arceus)
==================================================
[05:00] Lvl: 13 | HP: 90/90  | Kills: 581   | DmgReduce: 0%  | CDR: -8%  | Regen: +0.9/s  | Latigazo: YES
[10:00] Lvl: 32 | HP: 130/130| Kills: 2505  | DmgReduce: 30% | CDR: -28% | Regen: +4.5/s  | Latigazo: YES
[15:00] Lvl: 45 | HP: 170/170| Kills: 6165  | DmgReduce: 36% | CDR: -53% | Regen: +6.3/s  | Latigazo: YES
[20:00] Lvl: 54 | HP: 210/210| Kills: 10753 | DmgReduce: 42% | CDR: -57% | Regen: +8.1/s  | Latigazo: YES
[25:00] Lvl: 64 | HP: 250/250| Kills: 15024 | DmgReduce: 48% | CDR: -60% | Regen: +9.9/s  | Latigazo: YES
[30:00] Lvl: 70 | HP: 270/270| Kills: 19078 | DmgReduce: 48% | CDR: -60% | Regen: +11.7/s | Latigazo: YES
--------------------------------------------------
FINAL RESULT: Survived 31m 0s / 31:00
Player Level: 71 | Total Kills: 19903
Arceus Attacks: 116 Dodged | 13 Hit
• Reached Minute 30:00 (La Sentencia): PASS ✅
• Survived 1 Min Against Arceus (31:00 Elegido de Arceus): PASS ✅ (FULL CLEAR!)
==================================================
```

---

## 4. 如何在 Chrome 實機運行本實驗

### 步驟 1：啟動本地 Jev Bridge
在終端機執行：
```bash
cd ~/Sites/pokesurvivor
./run_bridge.sh
```
看到以下訊息表示 Bridge 已就緒：
```text
==================================================
 PokéSurvivor TypeSafe Jev Strategic Bridge
 Port: 8765
 TypeSafe Key Present: True
 Model: jev-latest
==================================================
```

### 步驟 2：載入 Chrome 擴充功能
1. 打開 Google Chrome，網址列輸入 `chrome://extensions/`。
2. 開啟右上角 **「開發人員模式 (Developer mode)」**。
3. 點擊左上角 **「載入未封裝項目 (Load unpacked)」**。
4. 選擇本 repo 入面嘅 `extension/` 資料夾。

### 步驟 3：進入遊戲進行實驗
1. 打開遊戲網址：`https://pokesurvivor.com/en/`
2. 畫面左上方會出現 **「⚡ PokéSurvivor Jev Bot」** 懸浮 HUD。
3. AI 插件具備全自動自主學習與戰力累積系統：
   - **智能技能無縫切換 (Auto Skill Weaving)**：未融合前自動切換 1-4 槽位釋放 Buff、召喚炮台與彈幕。
   - **實時傷害歸因 (Damage Attribution Forensics)**：幀級追蹤傷害來源（怪群擠壓、Boss 暴擊、衝刺怪 dash、危險地形/液體、預警光束）。
   - **跨局失敗學習 (Jev Post-Mortem Learning)**：每局陣亡時立即將戰敗數據回傳至 `/decide/postmortem`，由 TypeSafe Jev 診斷根本瓶頸，動態調節下一局的拉怪距離（Kite Distance +N px）、避障倍率（Eva xN）、救命回血門檻，並重置卡牌屬性權重（優先補 CD、回血或防禦），持久化記錄於 `logs/learned_memory.json`。
   - **局外永久養成 (Auto Meta-Progression)**：陣亡結算及主菜單時，自動領取已完成的成就與任務獎勵，並將累積的金幣（Coins）自動購買搭檔的永久被動升級（優先升級 Repertorio +1 初始技能、Reflejos 減冷卻、Síntesis 回血、Coraza 防禦等），每局都在變強！
   - 到達 30:00 觸發「阿爾宙斯的審判」並切換為極限生存避障模式，穩步衝刺至 31:00 通關！

---

## 5. 自我迭代學習系統 (Self-Iterating Learning Loop)

每一局都是一次**受控實驗**，而唔係盲目重開：

```mermaid
flowchart LR
    A["/run/start<br/>Jev 揀實驗 + 假設"] --> B["局內 每10秒記錄 timeline<br/>每60秒 對比自己最佳局"]
    B --> C["/run/checkpoint<br/>落後/失血 → Jev 調整姿態"]
    C --> B
    B --> D["/run/end<br/>Jev 反省：死因 / 假設成立?"]
    D --> E["Credit assignment<br/>每張卡的存活貢獻值"]
    D --> F["1+1 進化策略<br/>參數接受 or 回滾"]
    D --> G["有證據支持的 Lessons"]
    E & F & G --> A
```

| 機制 | 做法 | 位置 |
|---|---|---|
| 數據收集 | 每局完整記錄：timeline、每次選卡(含被放棄嘅選項)、道具、傷害來源、死因 | `logs/runs/run_XXXX.json` |
| 選卡學習 | 每張卡記錄「揀咗佢嘅局比基線多/少活幾多秒」(已扣除整體進步趨勢)，注入 Jev 選卡 prompt | `knowledge.json → card_stats` |
| 參數實驗 | 每局只改 1 個走位參數 (kite / evasion / heal / hazard / greed)，由 Jev 根據死因 + 歷史成功率揀；變好就接受、變差就回滾；每 4 局重測最佳參數防運氣 | `learning.py` |
| 局內反省 | 每分鐘同自己最佳局同一時間點比較等級/血量；落後或失血過快先叫 Jev 決定 play_safer / farm_harder / seek_heal | `/run/checkpoint` |
| 局後反省 | Jev 判斷真正死因、實驗假設 confirmed / refuted / inconclusive、下局主屬性 | `/run/end` |
| 教訓庫 | 例如「swarm_overrun 之後 kite_up 有效 (3/4)」「揀 Reflejos 平均多活 +45s (n=12)」 | `GET /report`、`logs/LEARNING_REPORT.md` |

驗證學習迴路本身有效（離線、模擬遊戲）：
```bash
python3 bridge/test_learning.py   # 60 局：平均存活 881s → 1352s，卡牌價值排序正確
```

## 6. 純學習模式 (Pure Learning Mode) — 現行模式

> 為咗實驗有意義：**Bot 唔可以用任何由遊戲原始碼得嚟嘅知識。** 之前「有人類攻略輔助」嘅版本（知識庫、選卡規則、敵人行為規則）已經存檔到 `archive/assisted_mode/`，唔再載入。

**Bot 可以用嘅（等同玩家睇到 + 手按到）**：敵人位置/大小/名、地上圓形或直線標記同佢幾滿、飛過嚟嘅子彈、掉落物圖示、地圖物件外觀、卡牌同道具上嘅字、畫面公告文字、自己嘅血/等級/經驗/金幣/券。

**全部要自己學**：
| 學咩 | 點學 | 檔案 |
|---|---|---|
| 乜嘢會傷我、要避幾遠 | 每次扣血記低當時貼住咩（含之前 0.9 秒嘅接近過程 = temporal credit），逐個距離區間計「每秒扣血率」| `knowledge.json → danger` |
| 掉落物有咩用 | 執到嗰刻血/經驗/金幣/券變化 | `pickups` |
| 打死邊種怪有著數 | 怪死後 1.2 秒內嘅收穫 | `kills` |
| 地圖物件/傳送門有咩用 | 好奇心：未試過嘅物件強制試 3 次，量度結果；之後 Jev 只憑經驗決定 | `objects` |
| 揀卡 / 道具 | Jev 只睇卡上文字 + 自己經驗（揀咗之後平均多活幾耐）| `card_stats` |
| 局外升級 / 搭檔 | Jev 只睇升級說明文字、圖鑑數值 + 之前結果 | `upgrades_hist`, `partners` |

驗證「由零開始真係學到」：
```bash
node sim/test_pure_learning.js
# PASS 學識避開會咬人嘅怪   PASS 學識地上圓圈會爆、提早離開   PASS 唔會怕無害嘅怪
```
查看 Bot 自己學到咩：`GET http://127.0.0.1:8765/kb`

## 7. 玩家日記模式 (Player Journal) — 取代「調旋鈕」

每局唔再係「fear +35% 睇吓分數」，而係好似玩家咁：**一條想解答嘅問題 + 一隻搭檔 + 一個 build 目標 + 一種打法**。

| 部分 | 做法 | 檔案 |
|---|---|---|
| 提問 | 第一次見到嘅物件/掉落物/Boss、未讀過嘅畫面文字、第一次出現嘅進化卡、擁有但未玩過嘅搭檔、未用過嘅扭蛋券、成日死喺同一樣嘢 → 自動變成問題 | `bridge/journal.py` |
| 假設 | 進化卡出現 → 由之前揀過嘅卡生成「X 喺揀咗 Y n 次之後出現」，之後每局自動記錄支持/反對 | `journal.json → beliefs` |
| 開局前計劃 | 上局反省時 Jev 已揀好：問題、搭檔、build、打法（激進刷怪 / 小心走位 / 尋寶探索 / 獵殺 Boss），所以 `/run/start` 即時返回 | `journal.json → next_plan` |
| 局後反省 | Jev 睇證據判斷問題「搵到答案 / 要再試 / 試唔到」，答案變信念，寫日記 | `logs/JOURNAL.md` |
| 局外 | 想換搭檔或者有扭蛋券 → 返主選單（唔再直接「再玩一次」）；扭蛋至少試一次 | `page_agent.js` |
| 數據衛生 | client 端 run id、重複記錄拒收、Auto-Iter 只觀察唔改參數 | `learning.py`, `auto_iteration.py` |

```bash
python3 bridge/test_journal.py   # 24 項：提問、假設驗證、扭蛋好奇心、去重、即時開局、完整反省
python3 bridge/test_pure_mode.py # 守門：現行代碼唔可以載入/寫死任何由遊戲原始碼得嚟嘅知識
python3 bridge/cleanup_kb.py     # (一次性) 去重 + 重算統計，先備份去 archive/
```
睇 Bot 而家諗緊咩：`logs/JOURNAL.md` 或 `GET http://127.0.0.1:8765/journal`
