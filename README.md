# AI-DEMO

包含两个可独立启动的本地 AI 演示应用：评论洞察工作台与说明书售后助手。

## 应用功能

| 应用 | 主要功能 | 界面 |
| --- | --- | --- |
| ReviewFlow 评论洞察 | 采集亚马逊美国站、德国站商品详情页可见评论，校验去重，生成主题洞察、原文证据与优先行动建议，导出 CSV / HTML | Electron 桌面窗口 |
| Support Desk 售后助手 | 在聊天窗口确认型号、描述问题和选择追问答案，流式显示回复，自动生成有来源的排查建议，保留单次会话记忆并导出摘要 | 本地聊天网页 |

评论结论只代表当前页面采集到的样本。售后知识库目前针对 **VIOFO A119 MINI 2**，图片识别后的型号仍需人工确认。

## Windows 启动

准备 Git、Node.js 24 和 Python 3.12，确保 `git`、`npm`、`py` 可以在 PowerShell 中执行。以下命令在项目根目录执行；模型服务须已由运行环境配置完成。

### 1. 下载与安装依赖

```powershell
git clone https://github.com/knight2018/AI-DEMO.git
cd AI-DEMO
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r support_demo\requirements.txt
npm.cmd ci
```

`support_demo/requirements.txt` 同时安装两个应用需要的 Python 依赖。

### 2. 启动评论洞察

在项目根目录打开 PowerShell：

```powershell
$env:Path = "$PWD\.venv\Scripts;$env:Path"
$env:PYTHONUTF8 = "1"
npm.cmd start
```

Electron 会自动启动 Python 后端并打开桌面窗口。输入商品地址，完成页面要求的登录或验证后，点击“开始采集与分析”。关闭桌面应用时，其启动的后端也会退出。

### 3. 启动售后助手

另开一个 PowerShell，进入同一项目根目录：

```powershell
.\.venv\Scripts\python.exe -m uvicorn support_demo.app:app --host 127.0.0.1 --port 8766
```

浏览器访问 <http://127.0.0.1:8766/>。在运行服务的终端按 `Ctrl+C` 停止。

在聊天中描述问题，按弹层核对型号即可开始。追问选项直接显示在消息下方；可自由输入补充信息，也可选择“反馈这项检查”说明操作结果。信息齐全后自动继续回复，无需逐个点击流程按钮。右侧可展开查看会话记忆、检索来源及说明书，刷新会恢复已保存的会话。选择转人工后会自动整理简要交接信息，可复制或下载，无需重新填写；当前演示尚未接入人工客服渠道。

仓库已包含生成好的知识库，正常启动无需重新建库。可访问 <http://127.0.0.1:8766/api/knowledge> 检查当前索引是否成功加载。

## AI 接口与返回格式

当前通过 AGICTO 的 **OpenAI 兼容 API** 调用模型。这里的“OpenAI 兼容”指请求和响应协议，不表示实际使用 OpenAI 的聊天模型。

| 用途 | 当前模型 | 接口与解析 |
| --- | --- | --- |
| 评论分析、意图识别、查询扩写、追问与建议、会话记忆 | `deepseek-flash` | Chat Completions，LangChain `ChatOpenAI` 调用 |
| 图片中的品牌与型号识别 | `deepseek-flash` | Chat Completions 多模态消息，图片使用 `image_url` 内容项 |
| 说明书和查询向量化 | `text-embedding-3-small` | Embeddings，读取 1536 维浮点向量 |

### Chat Completions

接口为 `POST /v1/chat/completions`。以下是精简的响应示例，`content` 内是模型生成的业务 JSON 字符串：

```json
{
  "object": "chat.completion",
  "model": "deepseek-flash",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\"brand\":\"VIOFO\",\"model\":\"A119 MINI 2\",\"visible_text\":\"VIOFO A119 MINI 2\",\"readable\":true}"
      },
      "finish_reason": "stop"
    }
  ]
}
```

程序通过提示约束业务 JSON，再使用 `PydanticOutputParser` 解析、校验字段。不同任务有不同的数据模型，例如评论洞察包含 `summary`、`topics`、`priorities`，图片识别包含 `brand`、`model`、`visible_text`、`readable`。业务 JSON 是 `message.content` 的内容，与外层 API 响应结构分开处理。

售后聊天使用模型真实流式输出，网页通过 POST + SSE 接收消息，无生成完成后的模拟打字。对外事件包括 `status`（处理阶段）、`draft`（正在生成的概要）、`state`（已保存会话）、`reply`（程序答复）、`done` 和 `error`。生成中的概要会标识为未确认，具体操作与引用完整校验后才展示；断流或失败不把草稿保存成正式建议。评论洞察接口保留原行为。

为减少等待，初始追问选择与记忆提取并行；有效记忆版本未变化时复用已有检索。用户补充事实后重新检索，不沿用过期证据。首条状态反馈与首段 AI 文字的时间不同，实际生成速度仍受模型服务影响。

引用还需通过程序检查：评论证据须对应评论 ID 和连续原文，售后建议须对应说明书来源。模型输出成功不等于证据校验通过；失败会明确报错，不自动切换备用模型。

### Embeddings

接口为 `POST /v1/embeddings`，请求携带 `model`、`input` 和 `encoding_format: "float"`。精简响应示例：

```json
{
  "data": [
    {
      "index": 0,
      "embedding": [0.012, -0.034, 0.056]
    }
  ]
}
```

示例仅展示 3 个数，实际每条向量为 **1536 维**。程序检查返回顺序、维度、有限数值与非零向量，归一化后建立 FAISS 内积索引。

## 章节切片与知识库存储

已有自动切片和建库程序：

- `support_demo/chunking.py`：依据 PDF 的分栏、标题字重、字号与位置识别章节／小节；长普通正文按段落边界继续切分，完整步骤和注意事项保留在一起。
- `support_demo/build_knowledge.py`：为切片生成检索问法和客服追问，校验证据，生成向量并发布索引。
- `support_demo/retrieval.py`：BM25 与 FAISS 分别召回，使用 RRF 融合排名。

切片保留原文、章节路径、PDF 页码、原始片段 ID 及起止偏移，便于回查。当前切分规则针对这份已知版式的说明书，不是任意 PDF 的通用解析器。

当前随仓库保存的索引为 `spec008-74c36c93a0a3`：**91 个切片、194 个检索问题、142 个追问、285 条向量**。

```text
support_demo/data/knowledge/
├── current.txt                      当前索引 ID
└── spec008-74c36c93a0a3/
    ├── documents.json               切片原文、章节、来源与生成问题
    ├── manifest.json                模型、维度、数量、版本及文件校验和
    └── vectors.faiss                向量索引
```

源 PDF、原始分栏文本和来源信息位于 `support_demo/manuals/`。原始分栏数据与章节切片用途不同，均保留供追溯。

修改源说明书、切片规则或向量模型后，使用已有程序重新建库：

```powershell
.\.venv\Scripts\python.exe -m support_demo.build_knowledge
```

建库会实际调用模型服务。新索引完整生成后才切换 `current.txt`；提交更新时，需同时提交该指针及其指向目录的三个数据文件。新生成目录受 `data/` 忽略规则影响，需要明确加入 Git。生成缓存、评论数据库 `data/reviews.sqlite3` 和售后会话数据库 `support_demo/data/sessions.sqlite3` 不随知识库提交。

## 测试与开发文档

在项目根目录执行：

```powershell
$env:Path = "$PWD\.venv\Scripts;$env:Path"
npm.cmd run test:all
```

测试按模块放在 `tests/`：评论领域与桌面交互、售后接口、混合检索、章节切片、会话记忆。自动测试与真实模型、真实网页的端到端验收分别记录。

开发前先阅读 `spec/` 中对应规格及测试用例。规格按设计（`planned`）、开发（`developing`）、完结（`completed`）、废弃（`discarded`）四类组织，状态记录在 `spec/status.json`；完结需满足对应测试与验收要求。

## 目录

| 目录 | 内容 |
| --- | --- |
| `desktop/` | Electron 窗口、商品页面采集与评论工作台 |
| `backend/` | 评论校验、分析、持久化与导出服务 |
| `support_demo/` | 售后网页、API、章节切片、检索与单次会话记忆 |
| `tests/` | 按功能模块划分的 JavaScript / Python 测试 |
| `spec/` | 设计规格、测试用例与开发状态 |
