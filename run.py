from __future__ import annotations

import argparse
from lls_platform.core.config import load_config
from lls_platform.sim.orchestrator import LinkAbstractionOrchestrator
from lls_platform.sim.numpy_bit_orchestrator import NumpyBitLevelOrchestrator


def main():
    parser = argparse.ArgumentParser(description="灵活码字映射 LLS Platform V2.2")
    parser.add_argument("--config", type=str, required=True, help="YAML 配置文件路径")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if cfg.simulation.backend == "link_abstraction":
        runner = LinkAbstractionOrchestrator(cfg)
    elif cfg.simulation.backend in ("numpy_bit_level", "bit_level_v0"):
        runner = NumpyBitLevelOrchestrator(cfg)
    elif cfg.simulation.backend in ("sionna_ldpc_bit_level", "sionna_bit_level", "ldpc_bit_level"):
        from lls_platform.sim.sionna_ldpc_orchestrator import SionnaLDPCBitLevelOrchestrator
        runner = SionnaLDPCBitLevelOrchestrator(cfg)
    else:
        raise NotImplementedError(
            "未知 backend: {}。当前支持 link_abstraction、numpy_bit_level、sionna_ldpc_bit_level。".format(
                cfg.simulation.backend
            )
        )

    out_dir = runner.run()
    print("\n仿真完成。结果保存在: {}".format(out_dir))


if __name__ == "__main__":
    main()
