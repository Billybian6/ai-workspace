# A股投研分析系统

这是桌面上的独立投研驾驶舱目录，已经从 `AI工作台` 中拆出来。页面、脚本、基础数据、缓存、日报和历史快照都在这个文件夹内。

## 文件结构

```text
A股投研分析系统/
  strategy_dashboard.html           策略研究工作台
  intraday_research.html            历史分时研究
  morning_dashboard.html            午盘投研快照
  dashboard.html                    收盘投研驾驶舱
  update_strategy.cmd               推荐双击：生成策略页并打开
  update_morning.cmd                推荐双击：生成午盘页并打开
  update_daily.cmd                  推荐双击：生成收盘页并打开
  update_database.cmd               推荐双击：回填/刷新本地 SQLite 数据库
  update_minutes.cmd                推荐双击：沉淀最近5日指数分时
  update_intraday_research.cmd      推荐双击：生成历史分时研究页并打开
  一键更新策略研究.cmd              双击后生成策略页并打开
  一键生成午盘快照.cmd              双击后生成午盘页并打开
  一键生成收盘复盘.cmd              双击后生成收盘页并打开
  generate_strategy.ps1             英文名策略页生成脚本
  generate_database.ps1             回填/刷新 SQLite 数据库
  generate_minutes.ps1              沉淀最近5日指数分时
  generate_intraday_research.ps1    生成历史分时研究页缓存
  生成策略研究.ps1                  生成 CTA/低波/期权/ETF 策略研究页
  生成午盘快照.ps1                  双击/运行后生成午盘快照
  生成收盘复盘.ps1                  收盘后更新基础库并生成复盘
  data/congestion_data.json         宽基拥挤度基础数据
  data/research_warehouse.sqlite    本地 SQLite 投研数据库
  data/cache/                       页面读取的缓存数据
  data/history/                     历史快照
  outputs/reports/                  Markdown 报告
  tools/                            Python 和 PowerShell 脚本
```

## 策略研究

盘中或收盘后都可以运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\generate_strategy.ps1
```

然后打开：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\strategy_dashboard.html
```

策略研究页面向 FOF、低波、CTA、期权、套利和 ETF 跟踪：默认展示核心指数最近 5 日分时趋势横向对比、策略环境评分、主线强度、ETF 资金和全A微结构。页面不展示个股明细。

也可以直接双击：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\一键更新策略研究.cmd
```

更推荐双击英文入口，避免 Windows 批处理对中文路径和编码不稳定：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\update_strategy.cmd
```

这个脚本会生成最新数据并自动打开策略研究工作台。

## 午盘快照

早盘结束后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\生成午盘快照.ps1
```

然后打开：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\morning_dashboard.html
```

午盘快照重点看全A成交节奏、上涨家数、风格切换、板块放量异动和资金背离。

## 收盘复盘

收盘后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\生成收盘复盘.ps1
```

然后打开：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\dashboard.html
```

收盘复盘会先更新 `data/congestion_data.json`，再生成驾驶舱缓存和日报。若盘中运行，脚本会自动跳过尚未完整落库的日频收盘数据。

## 当前模块

- CTA：过去 5 日指数分时路径、趋势效率、分钟方向一致性、日方向一致性
- 量化/指增：500、1000、科创、小微盘相对核心宽基的超额代理，全A宽度和尾部风险
- 宏观股债商：股票宽基、国债ETF、黄金ETF、商品ETF的跨资产状态
- 低波/FOF：市场扩散、尾部涨跌停压力、风格拥挤和主线集中度
- 期权/波动：指数间离散、方向波动、尾部风险和市场温度
- ETF 跟踪：宽基、红利低波、券商、半导体、新能源等常用 ETF 成交和资金
- 午盘异动、风格切换、板块放量观察
- 宽基拥挤度趋势和资产详情
- 东方财富板块行业/主题热力
- Markdown 日报与历史快照

## 本地 SQLite 数据库

数据库文件：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\data\research_warehouse.sqlite
```

手动回填或刷新数据库：

```powershell
powershell -ExecutionPolicy Bypass -File .\generate_database.ps1
```

查看数据库摘要：

```powershell
python .\tools\research_db.py summary
```

查看数据源健康状态：

```powershell
python .\tools\research_db.py health
```

AKShare 已接入统一仓库的日频探针：

```powershell
python .\tools\akshare_warehouse.py backfill --preset probe --days 30
```

当前 probe 档用于验证 300/500 指数、300/500 ETF、IF/IC 期货连续合约和 CPI 宏观月频；结果写入 `market_daily_bars`、`macro_observations` 和 `data_source_health`。`--preset core` 会扩展到更多常用宽基、ETF 和股指期货。分钟两年历史已预留 `market_minute_bars`，后续可接 QMT、Tushare 分钟或 CSV 导入。

普通基金净值也已接入 `market_daily_bars`，当前 core 样本包括 `000001`、`110022`、`161725`。页面的“数据源健康状态”会展示每个指标的来源、最新日期、频率和完整性。

日常运行 `update_strategy.cmd`、`update_morning.cmd`、`update_daily.cmd` 时，会自动把最新结果写入数据库。

## 历史分时仓库

分钟线表在同一个 SQLite 文件里：

```text
index_minute_bars
```

当前可自动沉淀最近 5 日宽基分时趋势。因为 Choice 分钟 K 线接口当前返回 `insufficient user access`，所以现在先写入归一化收益线，字段 `price_unit=normalized_100`。后续若能从 Choice 或手动 CSV 下载原始分钟 OHLC，可继续导入同一张表。

手动沉淀最近 5 日分时：

```powershell
powershell -ExecutionPolicy Bypass -File .\generate_minutes.ps1
```

生成并打开历史分时研究页：

```powershell
powershell -ExecutionPolicy Bypass -File .\generate_intraday_research.ps1
```

页面路径：

```text
C:\Users\bianzhengzhi\Desktop\A股投研分析系统\intraday_research.html
```

导出某个指数某段时间的连续分时 CSV：

```powershell
python .\tools\backfill_index_minutes.py --export outputs\exports\沪深300_分时.csv --index 000300 --start 2026-05-12 --end 2026-05-18
```

如果你从 Choice/其他系统下载了 CSV，可以导入：

```powershell
python .\tools\backfill_index_minutes.py --import-csv path\to\file.csv --index 000300 --index-name 沪深300
```

## 迭代说明

任意历史日期的分钟线回看需要每天沉淀本地分钟数据，或者接入 Choice 分钟线接口。当前版本先稳定使用公开行情源的最近 5 日分时数据。
