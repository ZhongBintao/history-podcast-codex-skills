# History Podcast Codex Plugins 交接文档

更新时间：2026-05-18

## 仓库定位

本仓库包含四组插件：

- `plugins/podcast-production/`：中文知识播客生产插件（原始版本）。
- `plugins/wechat-article-production/`：播客口播稿转微信公众号草稿插件（原始版本）。
- `plugins/podcast-production-agent-version/`：**Codex 版本** — 播客生产 subagent 编排工作流。
- `plugins/wechat-article-production-agent-version/`：**Codex 版本** — 微信文章 showrunner + subagent 编排工作流。
- `plugins/podcast-production-agent-version-claude/`：**Claude Code 版本** — 单 agent 播客生产工作流。
- `plugins/wechat-article-production-agent-version-claude/`：**Claude Code 版本** — 单 agent 微信文章生产工作流。

Codex 版本使用 `.codex-plugin/plugin.json` + `agents/openai.yaml` 插件格式，依赖 Codex 的 subagent 编排能力。
Claude Code 版本使用 `.claude-plugin/plugin.json` 格式，将 subagent 工作流改写为单 agent 直接执行，并移除了 `agents/openai.yaml`。

## 播客插件

用户入口：

```text
plugins/podcast-production/skills/podcast-series-showrunner/
```

内部模块：

```text
plugins/podcast-production/skills/podcast-series-opening-voice-producer/
plugins/podcast-production/skills/podcast-episode-director/
plugins/podcast-production/skills/history-script-writer/
plugins/podcast-production/skills/podcast-tts-producer/
plugins/podcast-production/skills/podcast-episode-editor/
```

确定性脚本：

```text
plugins/podcast-production/scripts/resolve_writer.py
plugins/podcast-production/scripts/run_episode_pipeline.py
plugins/podcast-production/scripts/robust_episode_tts.py
plugins/podcast-production/scripts/validate_production.py
```

播客流程：

```text
用户需求
→ podcast-series-showrunner 澄清和策划
→ Production Readiness Check
→ series_plan.json
→ opening_voice.wav
→ episode_brief.json
→ writer skill 生成 narration.txt 和可选 fact_check.md
→ scripts/run_episode_pipeline.py 生成 voice.wav、timeline、episode.mp3
→ scripts/validate_production.py 校验产物
```

安全边界：

- 不生成、不选择、不管理、不混合音乐或音效。
- `DASHSCOPE_API_KEY` 只能来自环境变量或本轮临时聊天输入，不能写入仓库、manifest、Markdown、日志或最终回复。
- 没有 TTS 凭证时只能做到 `narration_done`。

## Writer 扩展机制

`history-script-writer` 是当前唯一可用 writer 实现。未来新增科普、人文、文化、旅行、商业 writer 时，不要替换它；应新增并列 skill。

所有 writer 必须遵守同一契约：

```text
Input: episode_brief.json
Required output: narration.txt
Optional output: fact_check.md
```

新增 writer 的步骤：

1. 在 `plugins/podcast-production/skills/` 下创建 `<domain>-script-writer/`。
2. 写 `SKILL.md`，frontmatter 的 `name` 使用目录名。
3. 写 `agents/openai.yaml`，并设置 `policy.allow_implicit_invocation: false`。
4. 更新 `plugins/podcast-production/skills/writer_registry.json`，将对应 domain 的 `skill` 指向新 writer，并设为 `available: true`。
5. 从 `plugins/podcast-production/` 运行：

   ```bash
   python3 scripts/resolve_writer.py --validate
   python3 scripts/resolve_writer.py --domain science
   ```

后续 TTS、剪辑和 WeChat 插件只依赖 `narration.txt`，不应因新增 writer 而改动。

## 微信插件

用户入口：

```text
plugins/wechat-article-production/skills/wechat-article-pipeline/
```

内部模块：

```text
plugins/wechat-article-production/skills/wechat-narration-article/
plugins/wechat-article-production/skills/wechat-image-director/
plugins/wechat-article-production/skills/wechat-html-publisher/
```

流程：

```text
narration.txt
→ .wechat-work/article.json
→ images/ + image_manifest.json
→ article.html
→ wechat_upload_result.json
```

微信插件只创建草稿，不发布、不群发、不删除账号资产。

凭证读取顺序：

```text
环境变量
→ ~/.codex/wechat.env
```

必需：

```env
WECHAT_APPID=
WECHAT_APPSECRET=
```

## 验证命令

从仓库根目录：

```bash
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
python3 -m json.tool plugins/podcast-production/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/wechat-article-production/.codex-plugin/plugin.json >/dev/null
```

从播客插件根目录：

```bash
cd plugins/podcast-production
python3 scripts/resolve_writer.py --validate
python3 -m py_compile scripts/*.py skills/podcast-tts-producer/scripts/*.py skills/podcast-episode-editor/scripts/*.py
```

从微信插件根目录：

```bash
cd plugins/wechat-article-production
python3 -m py_compile skills/wechat-html-publisher/scripts/*.py
```

## Agent 版本插件（Codex 版 vs Claude Code 版）

本仓库从 2026-05 起同时提供 Codex 和 Claude Code 两套 agent-version 插件。

### 播客生产 Agent 版本

| | Codex 版本 | Claude Code 版本 |
|---|---|---|
| 路径 | `plugins/podcast-production-agent-version/` | `plugins/podcast-production-agent-version-claude/` |
| 插件格式 | `.codex-plugin/plugin.json` | `.claude-plugin/plugin.json` |
| Agent 配置 | `agents/openai.yaml` | 无（SKILL.md frontmatter 直接驱动） |
| 工作流 | main agent + subagent 编排 | 单 agent 直接执行 |
| 入口 skill | `podcast-series-showrunner` | `podcast-series-showrunner` |
| 凭证读取 | `~/.codex/wechat.env` | `~/.claude/wechat.env` |

### 微信文章 Agent 版本

| | Codex 版本 | Claude Code 版本 |
|---|---|---|
| 路径 | `plugins/wechat-article-production-agent-version/` | `plugins/wechat-article-production-agent-version-claude/` |
| 插件格式 | `.codex-plugin/plugin.json` | `.claude-plugin/plugin.json` |
| Agent 配置 | `agents/openai.yaml` | 无（SKILL.md frontmatter 直接驱动） |
| 工作流 | main agent + subagent 编排 | 单 agent 直接执行 |
| 入口 skill | `wechat-article-pipeline` | `wechat-article-pipeline` |
| 凭证读取 | `~/.codex/wechat.env` | `~/.claude/wechat.env` |

### 版本差异总结

两套版本的 Python 脚本完全相同。差异仅在插件元数据和 SKILL.md：

- **Codex 版**：SKILL.md 描述 main agent / subagent 分工与 task packet 模板，依赖 Codex 的 subagent 能力。
- **Claude Code 版**：SKILL.md 移除 subagent 术语，改为 "you" 单 agent 叙述，所有步骤由同一个 agent 执行。

新增功能时，两套版本的脚本应保持同步。SKILL.md 按各自平台的 agent 模式维护。
