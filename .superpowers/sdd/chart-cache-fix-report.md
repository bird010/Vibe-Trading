# 图表缓存修复报告

## 范围

- `frontend/src/pages/RunDetail.tsx`
- `frontend/src/components/run/SymbolMetricsTable.tsx`
- 对应的前端测试

## 修复

1. `RunDetail` 按标的维护已完成加载集合与进行中 Promise。表格展开、仅显示当前、添加和批量加载均复用同一请求；成功但价格序列为空的响应同样被记录为已加载。
2. 报告切换时重置详情页的图表缓存、加载完成集合和进行中请求映射。
3. `SymbolMetricsTable` 接收 `runId`，在它变化时清除展开项、局部图表缓存、加载状态和错误状态，避免复用同代码的旧报告图表。
4. Graph 集成测试改为用户点击“仅显示”后再请求图表，以匹配无初始预取的行为。

## 验证

- `npm run test:run -- src/pages/__tests__/RunDetail.test.tsx src/pages/__tests__/RunDetail.graph.test.tsx src/components/run/__tests__/SymbolMetricsTable.test.tsx`
  - 3 个测试文件、16 个测试通过。
- `npm run build`
  - TypeScript 检查与 Vite 生产构建通过；保留既有的大 chunk 警告。