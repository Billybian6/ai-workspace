# 市场投研分析系统

这是 `AI工作台` 下的独立投研驾驶舱原型，目标是从单一“宽基拥挤度”扩展到市场状态、风格轮动、行业热力、风险提示和数据质量监控。

## 文件结构

```text
market_research_system/
  dashboard.html                    静态投研驾驶舱，双击即可打开
  tools/build_market_dashboard.py   数据生成脚本
  data/cache/market_dashboard.json  结构化缓存
  data/cache/market_dashboard_data.js 供 HTML 本地加载的数据文件
  data/history/                     按交易日保存的历史快照
  data/raw/                         预留原始数据目录
  outputs/                          预留报告输出目录
```

## 使用方式

在 `AI工作台` 目录下运行：

```powershell
python .\market_research_system\tools\build_market_dashboard.py
```

然后打开：

```text
C:\Users\bianzhengzhi\Desktop\AI工作台\market_research_system\dashboard.html
```

## 当前版本

V0.4 投研版包含：

- 市场状态总览
- 市场宽度、板块扩散、成交集中度与主力净流入覆盖
- 风格轮动监控
- 风格价差、强弱背离和微盘相对中小盘观察
- 东方财富板块行业/主题热力表
- 宽基温度趋势图
- 资产详情面板
- 高拥挤与边际升温风险提示
- 数据质量与数据源说明
- 自动 Markdown 日报输出
- 按交易日自动保存历史快照

当前数据主要复用已有的 `congestion_data.json`，并尽量通过 Choice/EmQuant 补充行业指数行情；若外部接口失败，会自动用本地数据构建可用版本。

## 版本说明

- V0.4：新增市场宽度/扩散、风格价差、背离信号和 `data/history/market_dashboard_YYYY-MM-DD.json` 历史快照。
- V0.3：行业/主题热力默认使用东方财富板块 API，生成 `outputs/reports/daily_YYYY-MM-DD.md` 日报。
- V0.2：宽基、风格、风险提示和数据质量看板。
