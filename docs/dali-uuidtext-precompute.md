# Dali：离线 UUIDtext 与单次 Fastboot 返回

目标仍是真正的 SpringBoard 桌面。此批工具与0178不宣称已修复黑屏；实际 PortSend 的目标接收权属和队列状态仍未证明。

## 已验证的离线工作前移

两轮冻结根的 logd 自身日志证明其至少一次走过 main 与 `com.apple.logd` check-in 成功路径。两个 `logd+0x16300 / ENOENT` 是可返回的偏好读取错误，不是崩溃证据。shared-cache UUID harvest 经 `dispatch_async` 在独立队列执行，不能说主线程同步阻塞在 helper。

两轮现场均留下1 MiB的临时 DSC。按真实 helper 的格式，其表声明的内容远大于已持久化文件；临时状态不能独自区分运行中、阻塞或未完成持久化。

主机已从精确的 clean cache 独立构建完整 DSC：2710个图像、4301个范围、189960字节 metadata、101301914字节 section 数据及194104字节安装名，总101685978字节。metadata 和两轮现场已有的完整1 MiB前缀逐字节相同；另一次逆向核对覆盖全部范围和路径，无重叠/空洞。完整数据只留在私有主机工作目录，不进 Git 或 CI。

## 只读生成器

```sh
python3 -I tools/dali_uuidtext.py \
  --root-image /private/path/root.img \
  --expected-sha256 FULL_64_HEX_SHA256 \
  --output-dir /private/path/new-uuidtext-output
```

- 输入为普通、非symlink HFSX v5文件，4096-byte allocation block，卷头容量与文件一致，最大16 GiB。
- 仅支持本项目已验证的 arm64 cache/subcache 头布局。fork overflow、缺口、重叠、错误UUID、畸形命令和超界字段均明确拒绝。
- 同一只读FD贯穿前后完整SHA、catalog/cache解析和生成；发布前复核状态与完整SHA。此为普通并发修改检测，不是对恶意并发写入的不可变快照保证。
- 不mount、不fsck、不执行Apple二进制、不导入设备transport、不提供植入/刷机选项。
- 输出目录必须不存在，目录0700、文件0600；成功 `manifest.json` 最后排他发布。`.part`或无成功manifest的目录只是不完整输出，不应使用，也不自动覆盖或清理。
- manifest中的目标路径、UID/GID272、mode0644只是未来受审植入的数据契约；工具不chown、不写根。
- logd的既有快路径在sandbox检查允许后对最终UUID路径做 `access(F_OK)`，命中则不派发相应异步harvest。消费者接纳、首次启动迁移/清理和实际效果必须另验，不能仅凭生成成功宣称运行优化成功。

### 精确格式范围

DSC v2：小端16-byte `<4I>` 头，magic `0x64736368`、version2；24-byte `<QIIQ>` range；32-byte `<QI16sI>` UUID。表按cache-relative VM offset排序，owner重编号；payload与NUL终止安装名保持原image/section遍历顺序，无额外对齐。安装名读取 `LC_ID_DYLIB` 或dyld的 `LC_ID_DYLINKER`，不以image表别名替代。

只采集 `__TEXT`/`__CTF` 中的 `__cstring`、`__oslogstring`、`__asan_cstring`、`__ctf`、`__string`。该五项由实际trace库的 `__os_trace_sect_names` 校验，未从最新开源版本推定iOS16行为。生成上限256 MiB。

CI只使用人工小型HFSX/cache/Mach-O，经真实CLI完整走hash→HFS→cache→Mach-O→DSC；人工golden和独立解码器不调用生产生成算法。格式负控变异后重算SHA，避免只测试到hash门禁。

## 0178：默认关闭的单次返回准备

同板参考来自用户指定的 HackIOS：

- UEFI-Dali commit `47e6a77614de29d44969b26d04455d48cdd3e9f1`，`PlatformLib.c` 的标记协议/读回/回滚；文件SHA `3d21a2af2235a62eefff3b5c5b2871ac3100b46858203be4eeba867e732ec3b7`。
- UniXNU-Dali commit `d5120d8305c00ff8fccffb6990c1435d9974e95a`，`DaliRecovery.h`/`DaliPsci.h` 的单次cold复位ABI；保留其BSD-2-Clause-Patent来源标识。
- 参考cold/warm两轮有人确认未按键而自动返回Fastboot，但ramoops丢失，完整取证循环未通过；AEE轮没有自动返回。不可把这些合并成完整成功。

### 严格契约

只有 `dalifastboot=1` 加上准备器校验后的专用ADT属性才启用。真实FDT必须证明TOPRGU `0x1c010000/0x1000`，`syscon-reboot-mode` offset `0x24`、mask `0xF`、bootloader值3，PSCI `smc`及13MHz计时器。

```sh
python3 -I tools/dali_recovery_adt.py \
  --fdt /private/path/runtime.dtb --adt /private/path/raw.adt \
  --output /private/path/new.adt --run-token NONZERO_32_LOWER_HEX
```

输入只作数据，输出是独立raw ADT。只增加两个指定属性；去掉新增属性后必须恢复原ADT全部字节，不修改 `tools/mkadt.py`。sidecar列出未来候选必须精确绑定的bootargs；本命令不运行设备动作。

本项目当前观察到WDT MODE `0x17d`、LENGTH `0xf000`。0178保留它们和原喂狗算法，不照搬参考UEFI关闭/重新arm WDT流程。只读改写经验证的marker低4位并保存原值；标记已为3、无效读回、坏契约、预算错误均拒绝。失败回滚仍只改变低4位。

现有普通sampler每30秒调用一次返回检查，不从IRQ/异常钩子复位，不增加无限timer/worker。新profile要求heartbeat1、names `-`、delay/interval均30。默认after1080秒、lease1140秒，固定主机观察1200秒，留出继承WDT和USB枚举余量；不把日志收尾当作加时理由。

### 日志发布与复位

`prepared`是复位意图，不是复位已执行或Fastboot证据。普通线程请求现有logger发布当时的累计目标，最多20秒/有限轮询。只有原payload写→payload sync→header写→header sync全成功之后，logger才通过release/acquire标量发布已持久化总量。等待者不取UFS mutex、不碰descriptor/bounce、不重发I/O；丢唤醒仅再次wakeup。

发布确认、deadline、marker/MODE/LENGTH再次核对通过后，发一次PSCI cold `0x84000009`，x1..x7清零。SMC意外返回不是成功：记录并回滚，不自动换RESET2/AEE或重试。日志发布/时钟/状态未确认则不发cold，保留marker，只有原有有限WDT继续按其原行为运行；这不保证任何故障情形都自动恢复。

这是日志最佳努力收尾，不等整个根文件系统静止/干净卸载，也不保证ramoops经过cold reset保留。UFS现场始终必须先完整捕获再恢复；fsck只读结果和缺失日志照实保留。

## 主机无人值守接管门禁

`tools/dali_unattended_return.py` **没有进入Fastboot、刷候选或新增试验入口**。默认只验证，不调用transport。

它只接受一份未来候选目录和刷前批准清单的完整SHA。执行恢复前必须具备：

1. 人已确认该轮进入Fastboot/刷入和无人值守返回/恢复；同一非零run-token、候选hash、源码commit、唯一已启动且未耗尽的状态。
2. 固定1200秒起点，不延长；独占claim，失败不自动resume/retry。
3. `fastboot devices` 恰好一个精确目标，产品dali、槽b，不能以ADB、子串、旧连接、其他设备代替。
4. 批准清单逐字节绑定协调器及候选内三个专用adapter：`restore-stock-once.py`、`capture-actual-once.py`、`restore-root-once.py`。
5. 每阶段独立验收后才进入下一阶段；capture必须完整16 GiB三hash相同，最后必须原厂Android/完整物理对/完整根验收和四权限false。

三个adapter需要在**新的精确候选准备时**从本项目已验恢复工具生成并测试，绑定它们的真实参数及所有输入。协调器不是任意脚本执行器，也不能单靠一份自行填写的JSON赋予设备写权限。当前旧0177状态已闭合且次数耗尽，不能用此工具重启或恢复它。

实际用法只有在刷前确认后才允许 `--recover`；本次工具/CI交付不提供可直接执行的已授权设备目录。

## 整合候选与恢复链安全

`tools/dali_trial_adapters.py`提供三个薄adapter的实际阶段逻辑，复用候选内已验的device_pair、完整根capture和pinned-FD restorer，而不是第二套刷写算法。新候选的`trial-dependencies.json`绑定全部传递依赖；其完整SHA进入真人批准清单。核心候选文件也逐项回读hash。运行config、claim和state与静态依赖分域，不产生approval/config循环哈希。

- 候选reboot之前，`prepare_reboot`单次持久化起点及config，并fsync/复读；协调器不能在接管时重新计时。首次根增量前须先记录pending恢复义务。
- adapter在首个transport前核对批准、claim、token、根目标/恢复authority、当前阶段及所有依赖，然后独占发布started marker。未批准或旧spent状态原样拒绝。
- stock阶段必须完成Android及完整物理启动对验收。capture阶段只读固定SWEET窗口及最多2MiB的console-ramoops，不读pmsg/ftrace；完整16GiB三hash并再复核本地主机文件后才发布顶层capture marker。
- root阶段只有完整capture通过后才调用原pinned-FD算法，重新比较全部256个chunk，完整根/物理对验收后明确关闭四权限及pending。失败不发布闭合标记。
- coordinator将每个helper放进独立POSIX进程组，超时/信号/异常有界TERM、KILL和回收；正常父进程退出但残留子进程也不能算阶段成功。永久claim阻断自动重试。

**进程组退出不证明远端adb shell/su dd已停止。**已启动获批执行后，任何超时或异常都保留pending并停止自动链路，不自动执行下一写阶段或restore-only；后续人工恢复必须独立fresh确认无在途写入。无法确认就继续阻断。测试使用真实无设备子进程树及transport替身，不把它们当硬件无人值守验收。

根植入只允许在另行批准的全新主机COW副本中进行：保留原baseline/persona seed/原始现场，唯一新增DSC目录及最终文件，完整逻辑文件对照、只读fsck及全16GiB sparse虚拟重放必须通过。不存在自动植入设备的开关。

真实logd的UUID GC检查`ctime < time()-86400`（不是mtime），还有in-use UUID/boot-uuid xattr分支。部署前必须fresh核对正常创建的ctime与设备时钟并覆盖窗口；超龄或时钟异常就停止重新准备，不伪造version.plist、Salt、Identifier、bootUUID xattr或时间戳。静态快路径和目录契约不是运行接纳或黑屏修复证明。

## 验收边界

本地ARM64对象、实际C函数回归、合成测试和完整native CI分别记录。宿主clangd按x86目标给ARM64寄存器/片段头路径的诊断不能替代真实arm64编译，也不应隐藏真正编译错误。

**进入Fastboot或刷入新候选前先询问用户。**获准后同轮约定的自动返回/取证/恢复可无人值守执行；不自动追加下一轮。桌面仍须实屏图标网格和Dock、真实UI更新、同boot的surface/DSI成功和连续120秒，不因自动恢复或CI成功降低验收。
