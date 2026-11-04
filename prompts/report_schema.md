请输出 Markdown 诊断报告，并在文末附上 JSON，字段包括：

```json
{
  "fault_type": "imagepull|oom|crashloop|general",
  "title": "简短标题",
  "phenomenon": "现象描述",
  "root_cause": "根因判断",
  "evidence": ["证据条目..."],
  "runbooks_used": ["runbook id..."],
  "suggestions": ["建议命令，写操作标注需人工确认"],
  "risks": "风险说明",
  "human_confirm_required": true
}
```

Markdown 建议章节：现象、根因判断、关键证据、参考 Runbook、建议操作、风险提示。
