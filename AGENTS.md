# PokéSurvivor 實驗規則

## 🚫 唔可以睇原始碼（最高優先）
呢個實驗嘅目標係 bot **自己探索遊戲直到通關（31:00）**。所以：

1. **唔可以讀或搜尋遊戲原始碼**：包括 pokesurvivor.com 嘅 JS（例如 `app.min.js`）、用瀏覽器或者網絡請求攞返嚟嘅遊戲腳本，同埋網上嘅攻略、wiki、數據表。
2. **唔可以打開、grep 或引用 `archive/assisted_mode/`**：入面全部係由原始碼得嚟嘅知識（`game_kb_from_source.json`、`*_assisted.*`），只作歷史存檔。
3. **唔可以將遊戲規則、數值或者機制寫入現行代碼或 Jev prompt**，例如進化條件、技能最高等級、物件獎勵、扭蛋機率、掉落物用途、邊個技能係輔助。呢啲要 bot 自己透過日記（`bridge/journal.py`）提問、試驗、量度得出。
4. **遇到 bug 或卡關時**，唔准「去原始碼睇吓點做」。可以做嘅係：
   - 睇 bot 自己嘅 log、`logs/runs/`、`logs/JOURNAL.md`、`logs/jev_decisions.jsonl`
   - 請用戶喺 browser 觀察畫面或者 HUD 再話俾我知
   - 令 bot 識得自己探索，例如撳畫面上見到嘅掣，然後觀察資源有冇變

## ✅ 可以用嘅（等同玩家睇到、按到嘅嘢）
畫面上嘅物件位置、大小、名稱、外觀，地上標記，飛行物，掉落物圖示，卡牌、道具、升級、畫面公告嘅**文字**，自己嘅血、等級、經驗、金幣、券、擁有嘅搭檔，同埋 bot 自己量度到嘅結果。

## ⚠️ 已知灰色地帶
現有代碼會直接叫部分遊戲內部函數嚟「睇」同「做」，例如 `PS.World.specialsNear`、`PS.Gacha.pull`、`PS.MenuUI.open`。呢啲當做身體同眼睛，可以保留，但**唔好再加新嘅內部 API**，也唔可以由內部數據讀出畫面上睇唔到嘅資訊（例如敵人隱藏數值、掉落率）。方向係逐步改為透過畫面 UI 自我探索。

## 🛡️ 守門
改完 `bridge/` 或者 `extension/` 之後一定要行 `python3 bridge/test_pure_mode.py`，要全部通過。發現新類型嘅違規時，要將佢加入 `FORBIDDEN` 清單。
