# Current Version

当前版本号：Phase 1 Vision Provider Adapter v0.8

当前 Commit Hash：pending until commit; see final response for exact hash

当前 Branch：master

---

# Added

本次新增功能：
- Vision Provider Adapter
- 统一 Vision JSON schema
- Vision Router / Vision Gate
- Vision batch budget control
- OpenAI / MiMo / Qwen-VL / Gemini / Local provider 配置入口
- Local Vision fallback
- Vision A/B 测试准备
- 通用 vision_calls_used 批次统计字段
- 批次 Vision budget 更新接口
- Vision 路由测试、Provider schema 测试、预算暂停测试

---

# Database Changes

新增表：
- 无新增业务表

修改表：
- asset_batches：新增 vision_calls_used
- asset_batches：新增 max_vision_calls_per_batch
- asset_batches：新增 cost_limit
- asset_batches：新增 require_manual_confirm_before_large_vision_run
- asset_batches：新增 vision_status

保留兼容字段：
- asset_batches.openai_vision_calls_used 保留为旧版本兼容字段，但新逻辑优先使用 vision_calls_used

---

# API Changes

新增接口：
- POST /api/batches/{batch_id}/vision-budget

修改接口：
- POST /api/jobs/process：Vision 调用前必须经过 Vision Router、预算判断、候选收窄、Confidence Engine、Evidence Engine、Review Queue
- GET /api/batches：返回通用 vision_calls_used、vision_status、max_vision_calls_per_batch、estimated_cost
- GET /：管理后台批次进度显示通用 Vision calls 和 Vision budget 状态
- GET /api/pipelines/design：增加 vision_provider_adapter 架构信息

---

# Architecture Changes

本次架构调整：
- Vision 不再绑定 OpenAI，新增 provider adapter：openai、mimo、qwen_vl、gemini、local
- Vision Provider 必须返回统一 schema：product_match、product_structure、multi_product、quality
- Vision 不是第一层粗筛，第一层仍然是 Local Ingestion、Deduplication、Coarse Classification
- Vision 只能验证 Official Catalog 候选，不能自由创造品牌、产品、配饰或分类
- Vision 输出不能直接写入最终结果，必须经过 Confidence Engine、Evidence Engine、Review Queue
- Vision Router 会跳过 duplicate、near duplicate、corrupted、low_quality、scene-only、高置信度本地匹配等不值得调用 Vision 的图片
- 批次级预算控制支持 max_vision_calls_per_batch、cost_limit、require_manual_confirm_before_large_vision_run
- 超过预算时批次进入 paused_budget，不继续调用 Vision
- Product Structure DNA 的证据来源改为通用 vision_structure，保留 openai_vision_structure 兼容别名
- 普通上传仍然默认 Reality Truth，文件名不能创建 Official Truth

---

# What Works

目前已经可以工作的功能：
- 通过 VISION_PROVIDER 切换 openai / mimo / qwen_vl / gemini / local
- 未配置远程 provider 时，local provider 返回 Unknown，不猜测
- MiMo 类响应可以被标准化成统一 schema
- Vision 调用前会先做本地 ingestion、去重、粗分类、候选收窄和预算判断
- 低价值输入不会进入 Vision：duplicate、near duplicate、low_quality、scene_photo
- 批次 Vision 调用达到上限后暂停，后续图片不继续调用 Vision
- Vision 结果会保留 confidence、why、provider、vision_route
- 低置信度仍然进入 Human Review Queue
- Evidence Engine 仍然返回 matched_because、matched_official_assets、evidence_asset_ids、uncertain_fields
- 39 个自动化测试通过

---

# Known Limitations

目前已知问题：
- MiMo、Qwen-VL、Gemini 当前通过通用 HTTP endpoint 适配，具体厂商 payload 可能还需要按实际 API 文档细化
- Vision 成本估算仍是 Phase 1 粗估，每次调用按固定单价估算
- Vision A/B 测试框架已经可接入，但还没有独立的 A/B 报告页面
- 本地 visual matching 仍是轻量预筛选，不是生产级 embedding 检索
- Multi-product 图片仍然只有 region/candidate 结构预留和 Review Queue 路由，还没有稳定自动切图
- Human Review UI 仍是基础版本，适合验证工作流，但还不是高效率生产审核台

---

# Next Recommended Step

我建议下一步开发：
- 加一个 Vision A/B evaluation runner，用 50-100 张图片对 OpenAI 和 MiMo 比较准确率、结构识别、多商品识别、JSON 稳定性、速度和成本
- 增强 Official Product Visual Reference 的多图综合评分
- 增强 Human Review 修正入口，让人工修正能更清晰地回写 Reality Truth 和匹配提示
- 对 1,000 张测试 zip 做真实批量导入压测，观察 paused_budget、review_needed、unknown、duplicate 的比例

---

# Review Focus

请重点审查：
- Vision Provider Adapter 是否真的没有把 OpenAI 写死为唯一模型
- Vision Router 是否严格保证 Vision 不是第一层入口
- Vision 是否只能验证候选，不能创造新产品
- 统一 JSON schema 是否足够支持 OpenAI / MiMo / Qwen-VL / Gemini / Local
- Vision 输出是否仍然经过 Confidence Engine、Evidence Engine、Review Queue
- 批次预算控制是否能防止 1,000 或 10,000 张图片默认全部发送给 Vision
- Product Structure DNA 是否保持品牌无关
- Official Truth Lock、Unknown First、Phase 1 禁止生成是否没有被破坏
