---
name: qed-verification
description: "Use during QED implementation and before claiming completion — enforces test-first red, root-cause debugging, and evidence-before-claim; the three disciplines invoked by qed-implement/qed-closeout land here."
---

# QED 实现纪律与完成前验证（先红 → 根因 → 证据）

本技能是 `qed-implement`／`qed-closeout` 中三处通用纪律调用在本仓的落点：不再裸调用本仓不可达的
通用技能名，三条纪律就地成文。判据正文只在 `docs/standards/testing.md`（测试编写约定、门禁）
与 `docs/guides/development.md`（六步与门禁命令），本技能只给步骤与指针，不复制通用技能正文。

## 1. 先红（TDD）

涉及契约、状态机、迁移或行为变化，先写会失败的测试，**亲眼看到它红**，再写最小实现使其绿。
没看过红就不知道测试是否测对了东西；先写了实现再补测试不算数。
- 落点与隔离边界以 `docs/standards/testing.md`「测试编写约定」为准（临时目录、不读写真实数据根、
  默认零外部依赖、`QED_TEST_MYSQL` 唯一例外）。
- 可复现缺陷必先补一条回归测试再修，禁止只改实现不留守护。
- 本仓惯例见 `qed-implement`「隔离铁律」与各承载壳「过程与证据」中的先红后绿读数。

## 2. 根因（调试）

遇到 bug、测试失败或意外行为，先完成根因调查再提修复方案；猜测性修复与缓解症状的补丁一律禁止。
- 先复现、读全失败输出与退出码，再定位到具体模块／契约（用 `docs/architecture/code-map.md` 的
  DesignRef 与关联测试锚定），确认失败是代码、文档、环境还是夹具问题。
- 环境摩擦（沙箱 ACL、缓存目录、编码、端口占用）先按 `dsh-tooling-conventions` 与
  `cjk-safe-edit-and-verify` 排查，不把它误判为业务缺陷、不围绕它调业务参数。
- 根因确定后才改；改完用同一条失败用例复验，并补回归守护。

## 3. 证据（完成前验证）

声称「完成／修好／通过」之前，必须有本次会话内新鲜、完整的验证输出；没有证据就不作该声称。
- 识别哪条命令证明该声称 → 完整跑一遍 → 读全部输出、核对退出码与失败计数 → 输出证实才声称，
  否则按实测状态报告。任何一步跳过都不算验证。
- 本仓门禁命令取 `docs/guides/development.md`「门禁」节（当前为 `pytest tests -q` 与
  `ruff check src tests`）；DSH 会话内按 `dsh-tooling-conventions` 回避不可写缓存目录。
- 门禁输出（passed/skipped 计数、All checks passed）写在承载壳 `## 过程与证据` 与 `completed.md`
  行；只有「待提交」占位而无 hash 时按 `qed-closeout` 的证据规则处理。

## 与相邻技能的分工

- `qed-implement`：定位模块、遵循隔离铁律、同步 code-map，把本技能的先红与根因步骤用于实现批。
- `qed-closeout`：流程性收口（六步顺序、Retain/Delete、台账迁移）；本技能负责其中「门禁全绿」
  的证据纪律，两者不互相复制正文。
- 判据与门禁配置的任何变更必须先改 `docs/standards/testing.md` 与契约守护，再改本技能指针。
