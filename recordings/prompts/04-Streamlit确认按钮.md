# Prompt 快照模板 04：Streamlit 确认按钮

> **模板，不是真实对话导出。** 说明见 `01-脚手架诊断Agent.md` 顶部。AI创新应用挑战赛若要求视频里出现协作过程，这份快照适合作为画面素材：打开本文件，指出「按钮在输入 CONFIRM 之前禁用」是人提出的约束。

## 这次协作要解决的问题

CLI 已经有确认门闩。作者希望图形界面和 CLI 使用同一条 `apply_action`，并且不能只靠按钮文案提醒用户。未输入确认词时，执行控件不可用。

## 人类提示（示例）

```text
改 src/ui/app.py，不要新写一套执行逻辑。

- 诊断结果下面列出每条 remediation：勾选框、命令、风险、是否可回滚
- 文本框标签必须是「输入 CONFIRM 后执行」
- 只有文本框去掉空白后等于 CONFIRM 时，「执行修复」才可点击
- 按钮被点击时再核对一次令牌；没勾选动作就不要调用 apply
- MOCK=1 时用说明文字告诉观众这是模拟
- MOCK=0 时用警告文字告诉观众接下来会改真实集群
- 结果正文里的 kubectl 注释以 # 开头，不要放进 st.success，否则会被当成 Markdown 标题
- 用测试锁住「未输入 CONFIRM 时按钮 disabled」
```

## 助手回复要点（示例）

- 从 `src.remediation.apply` 引入 `CONFIRM_TYPED` 和 `apply_action`，避免界面和 CLI 各认一套拼写。
- `st.button(..., disabled=not unlocked)`，`unlocked` 为 `confirm_text.strip() == "CONFIRM"`。
- 执行时把本次诊断的 scenario、namespace、pod 传给 `apply_action`。
- 成功与失败的第一行用 `st.success` / `st.error`，其余行用 `st.code`。
- `tests/test_remediation_ui.py` 检查按钮在空字符串时为禁用、在 `CONFIRM` 时启用。测试不启动浏览器。

## 和当前代码的对应关系

实现位于 `src/ui/app.py` 中「修复方案（需人工确认）」一节，测试位于 `tests/test_remediation_ui.py`。

录视频时如果走创新应用赛道，可以在这一页停 20 秒：左边是你的提示里那句「未输入不得执行」，右边是界面上灰色的按钮。这比口播「我用了 AI」更容易被核对。
