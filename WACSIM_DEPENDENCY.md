# WaCSim dependency identity

This file fixes the WaCSim dependency required by the CRAVE `wacsim_ctown`
platform adapter. It is an integration record, not an experiment result.

## Delivery layout

The CRAVE repository will contain WaCSim as a Git submodule at:

```text
third_party/WaCSim
```

The CRAVE platform adapter itself will remain in:

```text
simulators/wacsim_ctown
```

CRAVE source code and platform configuration must not contain a machine-specific
absolute path such as `D:\\2026\\WaCSim`. The adapter will resolve required
assets relative to the checked-out submodule root.

## Fixed identities

| Item | Fixed value |
| --- | --- |
| CRAVE integration baseline | `56b601a15c60852b7fe9a93a44a49eee4b55b8c0` |
| WaCSim submodule commit | `dd14399412e2f84b6a0f44910809baad4b5fbd51` (local, pending publication; documentation-only successor of `70ef9015c43871fc3db055c6fd17dcffba47051f`) |
| WaCSim Stage 2 implementation commit | `d0512f19af82379103ff5eaac50856e256f3fe5a` |
| Docker image digest | `wacsim@sha256:392ed879bb7c41c6c37f190ea53bbb09fc03aee073fcb5a1f50c6b8f4e21eb26` |
| Scenario ID | `crave_ctown_t7_pu10` |
| Scenario SHA-256 | `e749d6f712a486514e065f81b63a07835f5e488a38b8c8072c11d258d5059a65` |
| Calibration-lock SHA-256 | `1a6cba68fa58f8ee242a4dedf442906f717733dc8bcf13c520c1af8718b71814` |

The byte-preservation commit is a child of the previous Stage 2 closure commit
`c223eeb98e5121aab9f56852c667cf1e480f4d4f`, which in turn carries the Stage 2
implementation history. It changes no replay logic: it records the reviewed
formal source bytes and disables Git newline conversion for every lock-sensitive
asset. The locked scenario, calibration lock, network, and implementation
fingerprints remain the fixed values above.

## Required assets inside the submodule

```text
third_party/WaCSim/examples/crave_ctown/scenario.yaml
third_party/WaCSim/examples/crave_ctown/calibration.lock.yaml
third_party/WaCSim/examples/crave_ctown/full_no_attack.yaml
third_party/WaCSim/examples/crave_ctown/full_driver.yaml
third_party/WaCSim/examples/crave_ctown/full_driver_masker.yaml
```

The calibration lock remains owned by WaCSim. CRAVE must validate its hash and
must not maintain a second editable copy.

## Execution environment

The Stage 2 replay engine includes Linux native EPANET libraries. It must run
inside the fixed Linux Docker image, not in CRAVE's Windows virtual environment.
This was checked without a replay: the Windows interpreter cannot load the
Linux native toolkit, while Python 3.10.12 in the fixed container imports the
mounted Stage 2 `wacsim.replay.engine` successfully.

The CRAVE adapter, Stage 1--3 smoke pipeline, and subsequent formal fast
replays will therefore execute inside a CRAVE integration image built from the
fixed WaCSim image. `docker/Dockerfile.wacsim-ctown` defines that image: it
includes the CRAVE source and the checked-out `third_party/WaCSim` submodule,
then exposes both through repository-relative Python paths. A Windows virtual
environment may be used for editing and pure-structure checks only.

## Reproduction bootstrap after the submodule is published

```text
git submodule update --init --recursive
```

The host must provide Docker/WSL2. The integration image defined by CRAVE will
use the fixed Docker digest and the checked-out submodule for both fast replay
and later complete WaCSim/Mininet validation.

## Current submodule binding and publication requirement

The approved fork remote is `https://github.com/Caoxj226/WaCSim.git`. Its
`crave-ctown-phase2` branch remains published at
`c223eeb98e5121aab9f56852c667cf1e480f4d4f`. The current byte-preservation
commit is held only on the local `crave-ctown-byte-identity` branch and has not
been pushed. It must be published to the approved fork before this CRAVE
Gitlink can support external reproduction.

The current CRAVE worktree contains the `third_party/WaCSim` Gitlink at the
local byte-preservation commit. Its tracked `.gitmodules` entry and its own
`origin` remote both use the approved HTTPS URL above. No local-drive URL is
recorded in Git, the manifest, or the runtime code.

The initial local checkout was used only to bootstrap this pinned commit while
the Windows Git client could not connect to GitHub. After publication, a fresh
clone uses the tracked remote URL and `git submodule update --init --recursive`;
it does not depend on the bootstrap location.
