# WaCSim CTown 平台扩展：历史 T7–PU10 单链实验

## 实验范围

本文说明 CRAVE 的 WaCSim CTown 平台扩展。当前公开实验有意限定为一条已评审的控制链：

```text
T7_LEVEL_SP -> PI-T7-PU10 -> PU10_SPEED_CMD -> T7_LEVEL
```

该实验不声称覆盖 CTown 的全部传感器、执行器或控制面。尤其是，它**不包含**后续多控制器/全控制面探索工作；任何 `full_control_surface*`、`full_surface*` 或多链 SP+PI 文件均不属于本平台发布内容。

## 实验契约

| 项目 | 锁定值 |
| --- | --- |
| 物理过程 | WaCSim CTown EPANET 回放 |
| driver 注入点 | `PU10_SPEED_CMD` |
| masker 注入点 | `T7_LEVEL_SP` |
| 控制回路 | `PI-T7-PU10` |
| 受保护过程变量 | `T7_LEVEL` |
| 危害 | `H-T7-LOW`：`T7_LEVEL <= 0.5 m` |
| 告警 | `A-T7-TRACK`：`abs(T7_LEVEL_SP - T7_LEVEL) > theta_m` |
| 标称设定值 | `2.5 m` |
| PI 参数 | `Kp = 0.2`，`Ki = 0.04`（每个 300 秒步长） |
| 设定值工程范围 | `[0.0, 5.0] m` |
| 评分起点 | 状态转移第 24 步 |
| 历史 ramp 时序 | 第 24 个评分步为捕获的零偏移基值；首个非零偏移出现在下一步 |

上表的历史时序是为了严格复现既有实验而保留的选择，并不表示它是新实验应采用的推荐时序规则。

## 源码与配置位置

```text
simulators/wacsim_ctown/
  system_manifest.json       # CRAVE 可见的平台契约
  runtime_executor.py        # 将 CRAVE 任务转为 WaCSim 回放的适配器
  controller.py              # 供 Stage 1 提取的 PI 语义依赖
  simulation.py              # 供 Stage 1 提取的水力语义依赖与轨迹契约
  variables-table.xlsx       # 已核查的变量清单

third_party/WaCSim/          # 固定版本的 Git 子模块
  examples/crave_ctown/
    scenario.yaml            # 单 T7–PU10 场景
    calibration.lock.yaml    # 固定的 PI 与告警参数
```

`system_manifest.json` 是唯一的活跃绑定文件；它只能引用：

```text
examples/crave_ctown/scenario.yaml
examples/crave_ctown/calibration.lock.yaml
```

## 可复现性与完整性要求

1. 克隆仓库后必须初始化 WaCSim Git 子模块。
2. 必须使用父仓库和活跃 manifest 所记录的固定 WaCSim 提交版本。
3. 复现实验时不得修改 `scenario.yaml`、`calibration.lock.yaml` 或回放实现；适配器会检查对应哈希。
4. WaCSim 回放须在受支持的 Linux Docker 环境中执行。Windows 原生环境仅用于静态检查源码和配置。
5. Stage 1、Stage 2、Stage 3 的输出分别构成独立证据；不得用 smoke 测试或校准诊断替代正式实验结果。

校准锁文件记录已选设定值、PI 参数、积分限幅、告警阈值、无攻击基线指标、场景哈希、网络哈希和实现哈希。本发布版本锁定 `theta_m = 1.0123111258725106`。

## 调用命令

以下命令须在仓库根目录的 PowerShell 中执行。WaCSim 的 EPANET 回放依赖 Linux 原生库，因此执行命令使用 Docker；不要在 Windows 虚拟环境中直接运行回放。

### 初始化子模块并构建镜像

```powershell
git submodule update --init --recursive
docker build -f docker/Dockerfile.wacsim-ctown -t crave-wacsim-ctown:local .
```

`docker build` 会使用 `WACSIM_DEPENDENCY.md` 和 Dockerfile 中固定的 WaCSim 基础镜像身份。构建前应确认 Git 子模块已被正确检出；不要以本机绝对路径替换 `third_party/WaCSim`。

### 无回放接口检查

以下命令只检查容器是否能导入 WaCSim 适配器、共享 Stage 1 模块和 WaCSim EPANET 回放实现，不执行水力回放，也不写入结果：

```powershell
docker run --rm crave-wacsim-ctown:local `
  -c "import simulators.wacsim_ctown.runtime_executor; import src.stage1.semantic_extraction; import wacsim.replay.engine; print('WaCSim import check passed')"

docker run --rm crave-wacsim-ctown:local -m src.stage2.base_search --help
docker run --rm crave-wacsim-ctown:local -m src.stage2.conditional_search --help
docker run --rm crave-wacsim-ctown:local -m src.stage3.subregions --help
```

### 读取仓库保留的单链结果

以下命令只读取已保留的 T7–PU10 单链结果：

```powershell
Get-Content results\rq1\rq1_wacsim_ctown_main_tables.json
Get-Content results\stage2\wacsim_ctown\PU10_SPEED_CMD\boundary_results.json
Get-Content results\stage2\wacsim_ctown\PU10_SPEED_CMD\conditional_results.json
Get-Content results\stage3\wacsim_ctown\PU10_SPEED_CMD\coordinated_regions.json
```

### 可选：在独立目录重新运行 RQ1

下列命令会运行新的单链 RQ1 扫描，可能耗时数分钟或更久。它将输出挂载到新目录，不会覆盖仓库中保留的结果；执行前请确认 Docker 镜像、场景、校准锁和子模块提交均与本 README 一致。

```powershell
$runRoot = Join-Path (Get-Location) "work\wacsim_ctown_rq1_rerun"
New-Item -ItemType Directory -Force -Path $runRoot
$stage1Destination = Join-Path $runRoot "stage1\WaCSimCTown"
New-Item -ItemType Directory -Force -Path $stage1Destination
Copy-Item results\stage1\WaCSimCTown\* $stage1Destination -Recurse

docker run --rm `
  --mount "type=bind,source=$runRoot,target=/opt/crave/results" `
  crave-wacsim-ctown:local `
  -m evaluation.rq1.rq1_eval `
  --root /opt/crave `
  --platform wacsim_ctown `
  --run-w-scan `
  --seed 460
```

其中复制的 `stage1/` 是 RQ1 读取的已发布单链语义提取、敏感性和危害排序输入；新的扫描和 RQ1 汇总只会写入 `$runRoot`。该命令只适用于当前单链契约。不要将任何五链/全控制面场景、参数、脚本或结果复制到该输出目录。当前仓库不将尚未执行的冻结 RQ2、RQ3、RQ4 命令作为正式可复现实验结论发布。

## 应发布的结果

仅单链正式输出应放入 CRAVE 的标准结果目录：

```text
results/stage1/WaCSimCTown/
results/stage2/wacsim_ctown/PU10_SPEED_CMD/
results/stage3/wacsim_ctown/PU10_SPEED_CMD/
```

原 Boiler 与 Tennessee Eastman 平台的源码和结果保持不变。校准轨迹、Docker smoke 输出、临时 manifest/log、以及全控制面探索材料均属于开发过程产物，不应作为正式 WaCSim 结果发布或引用。

## 当前局限性

- 当前仅为**单控制链**实验，不是 CTown 全控制面评估。
- 唯一 driver 为 `PU10_SPEED_CMD`；唯一协调 masker 为 PI 前的有效设定值 `T7_LEVEL_SP`。
- 不评估测量反馈 masker、通信/网络攻击、Mininet/SCADA 延迟，也不评估全部 CTown PLC、执行器、传感器或依赖关系。
- 快速回放使用已评审的 WaCSim EPANET 契约；它不等同于完整运行级网络物理仿真。
- 结果只能在固定的 T7 低液位危害和 `A-T7-TRACK` 告警契约下解释。未经独立评审的平台契约和重新校准，不得外推到其他 CTown 水箱、泵、阀门、告警或危害。

## 排除的探索材料

任何归档的多链/全控制面材料均明确处于公开实验路径之外。未经新的评审和显式版本化的实验契约，不得将其复制回活跃 manifest、WaCSim 子模块回放路径、正式结果目录或论文/公开结论中。
