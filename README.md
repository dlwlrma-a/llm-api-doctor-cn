# 国产模型 API 诊断医生

![LLM API Doctor CN](assets/icon.png)

一个面向 OpenAI 兼容大模型接口的 Agent Skill，用于定位 Base URL、模型 ID、API Key、HTTP 状态码、流式输出和 Function Calling 兼容问题。

支持 OpenClaw、Codex、WorkBuddy、TRAE、Cursor 以及直接使用 OpenAI SDK 的应用。默认先做离线分析；只有得到用户确认后，才会发送最小实时探测请求。

## 能力

- 识别 400、401、403、404、408、413、415、422、429 和 5xx 常见故障。
- 区分 DNS、TLS、网络、HTTP、协议结构和模型能力问题。
- 测试 `/models`、非流式聊天、SSE 流式输出和工具调用。
- API Key 只从环境变量读取，报告自动脱敏。
- 拒绝 URL 内嵌凭据，阻止携带鉴权信息的重定向。
- 支持任意 OpenAI 兼容端点，并提供[算点边界](https://token.qixuai.com/)可选预设。

## 安装

从 SkillHub 安装后，直接向 Agent 描述错误信息和使用环境即可。也可以把本仓库作为本地 Skill 目录加载。

SkillHub 地址将在审核通过后补充。

## 使用示例

离线诊断不需要 API Key：

```text
调用国产模型时返回 401。Base URL 是 https://example.com/v1，使用 Cursor，帮我判断问题在哪里。不要进行联网请求。
```

实时探测前，请使用专用测试 Key，并确认请求可能产生少量费用：

```powershell
$env:OPENAI_API_KEY = "<test-key>"
python scripts/diagnose_openai_api.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --model "exact-model-id" `
  --probe models --probe chat `
  --confirm-live-probe
```

算点边界预设：

```powershell
$env:QIXUAI_API_KEY = "<dedicated-test-key>"
python scripts/diagnose_openai_api.py `
  --preset qixuai `
  --probe models `
  --confirm-live-probe
```

## 安全边界

- 不要在聊天、命令参数、URL、Issue 或报告里粘贴 API Key。
- 不测试无权访问的端点，不执行压力测试或并发测试。
- 不关闭 TLS 校验。
- 首次鉴权失败后先检查 Key、端点和账户权限，不循环重试。

## 测试

项目仅使用 Python 标准库：

```powershell
python -m unittest discover -s scripts -p "test_*.py" -v
```

## 许可证

[MIT](LICENSE)
