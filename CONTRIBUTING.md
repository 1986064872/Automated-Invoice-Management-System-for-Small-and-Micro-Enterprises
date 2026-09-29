# Contributing

感谢你愿意参与这个项目。提交 Issue 或 Pull Request 前，请先阅读以下约定。

## 隐私红线

本项目处理真实发票和敏感财务信息。任何公开的 Issue、PR、截图、日志或测试数据都不得包含：

- 真实发票、订单、合同或银行流水文件
- 企业名称、统一社会信用代码、纳税人识别号
- 发票号码、银行账号、开票人姓名或联系方式
- API Key、Token、密码、Cookie、`.env` 内容
- 未脱敏的数据库、Excel、PDF、图片或本机绝对路径

如果需要复现问题，请使用 `backend/testdata/测试发票公开版/` 中的合成样本，或自行制作最小化假数据。

## 开发环境

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements.txt

cd frontend
npm install
cd ..
```

## 提交前检查

```powershell
python backend\scripts\batch_check.py --self-test-only
python -m compileall -q backend\app backend\scripts

cd frontend
npm run typecheck
npm run build
```

涉及端到端流程时，先启动后端，再运行：

```powershell
python backend\scripts\e2e_check.py
python backend\scripts\contract_check.py
```

## Pull Request

PR 应保持范围清晰，并说明：

- 改动解决了什么问题
- 用户可见行为是否变化
- 运行了哪些测试
- 是否触碰 OCR、解析器、账本、导出或数据存储
- 是否确认没有提交任何真实票据或密钥

解析器相关改动尤其需要说明回归结果，并避免只验证“字段有没有值”，还要抽查名称、规格、单位、数量和金额。

## 代码风格

- 优先沿用现有模块结构和命名。
- 业务逻辑放在后端服务层，不要把关键规则仅放在前端。
- 新接口需要同步检查 `frontend/src/types.ts` 和 `contract_check.py`。
- 错误信息应尽量可操作，但不得回传密钥或完整票面内容。
- 注释解释“为什么”，避免重复代码表面含义。
