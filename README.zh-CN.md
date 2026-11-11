# CRAVE 开放复现材料

本仓库提供一份匿名化实验材料，用于在锅炉协调控制系统（Boiler CCS）、Tennessee Eastman（TE）模型及已评审的 WaCSim CTown T7–PU10 单链实验上评估 CRAVE 风格的 Reach-Avoid 漏洞发现方法。

## 目录内容

- `src/stage1/`：语义提取、敏感性分析和危害变量排序。
- `src/stage2/`：基础搜索与条件搜索。
- `src/stage3/`：协调子区域构建。
- `simulators/`：平台模型、清单、变量表和运行时执行器。
- `evaluation/`：RQ1–RQ4 的实验结果处理程序和重放脚本。
- `results/`：论文表格使用的精简版发布结果快照。
- `WacSim-README.md`：WaCSim CTown T7–PU10 单链扩展的范围、锁定契约、调用命令与局限性。

## 环境配置

使用 Python 3.12 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

运行下面的快速脚本检查：

```powershell
$files = Get-ChildItem -Recurse -Filter *.py | ForEach-Object { $_.FullName }
python -m py_compile $files
python -m src.stage2.base_search --help
python -m evaluation.rq4.rq4_eval --help
```

## 主要命令

下面的命令会重新生成完整实验产物，运行可能需要数分钟。

Stage 2 基础搜索：

```powershell
python -m src.stage2.base_search --platform boiler_ccs --hazard-driver fuel_command --workers 16
```

Stage 2 条件搜索：

```powershell
python -m src.stage2.conditional_search `
  --platform boiler_ccs `
  --boundary-path results/stage2/boiler_ccs/fuel_command/boundary_results.json `
  --extraction-path results/stage1/boilerCCS/extraction.json `
  --manifest-path simulators/boiler_ccs/system_manifest.json `
  --output-path results/stage2/boiler_ccs/fuel_command/conditional_results.json `
  --output-root . `
  --workers 16
```

Stage 3 区域构建：

```powershell
python -m src.stage3.subregions `
  --platform boiler_ccs `
  --hazard-id H-PRESSURE-001 `
  --hazard-driver fuel_command `
  --base-path results/stage2/boiler_ccs/fuel_command/boundary_results.json `
  --conditional-path results/stage2/boiler_ccs/fuel_command/conditional_results.json `
  --extraction-path results/stage1/boilerCCS/extraction.json `
  --manifest-path simulators/boiler_ccs/system_manifest.json `
  --output-root . `
  --workers 16
```

读取仓库保留的 RQ 汇总结果：

```powershell
Get-Content results\rq1\rq1_main_tables.md
Get-Content results\rq2\rq2_main_table.md
Get-Content results\rq3\TE\summary.md
Get-Content results\rq4\README.md
```

RQ 脚本可以重新生成汇总结果并进行敏感性分析。默认情况下，RQ1 从保留的重放扫描结果重新生成论文表格；只有希望替换这份保留的 RQ1 扫描快照时，才传入 `--run-w-scan`。下面的部分命令可能运行数分钟，并在 `results/` 下写入新结果；需要替换保留快照时再运行：

```powershell
python -m evaluation.rq1.rq1_eval --root .
python -m evaluation.rq2.rq2_eval --root . --boundary-path results\rq2\te_crave_safe_boundary.json --budget 2939 --workers 4
python -m evaluation.RQ3.rq3_eval --root . --workers 4
```

RQ4 的 A 部分和 B 部分参数扫描，请参阅 `results/rq4/reproduce.md`。

## 匿名化说明

此发布版本有意不包含作者姓名、单位、私人路径和开发历史记录。生成的重放轨迹和工作目录未纳入版本控制；重新运行上述命令即可按需生成。
